from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import List, Sequence

import requests


logger = logging.getLogger("Text-RAG-Manager")
JSON_BLOCK_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
CODE_REQUEST_PATTERN = re.compile(
    r"(代码|脚本|scheme|sde|sentaurus|tcad|python|program|function|生成.*代码|可执行)",
    re.IGNORECASE,
)
FILLER_PATTERN = re.compile(
    r"(请参考文档|参考文档|请帮我|帮我|为我|生成一个|生成一份|生成|给出|写一个|写一份|代码|脚本|可执行的?)"
)

DEFAULT_SYSTEM_PROMPT = """
You rewrite user questions into retrieval-focused search queries for a technical RAG system.
Return JSON only with the form: {"queries": ["...", "..."]}.
Rules:
- Keep the original domain and language.
- Do not answer the question.
- Produce 2 to 4 short retrieval queries.
- Make the queries more explicit for document retrieval.
- Prefer terminology, procedure names, artifact names, and constraint names that help retrieve relevant passages.
- Avoid directory-style keywords or generic filler.
- If the user asks for code/script generation, do not repeat "generate code".
- For code/script generation, decompose the retrieval intent into complementary queries such as:
  1. target structure/device + artifact terminology,
  2. tutorial/example/procedure oriented query,
  3. syntax/command/constraint oriented query.
""".strip()


@dataclass(frozen=True)
class QueryRewriteConfig:
    enabled: bool
    api_url: str
    api_key: str
    model: str
    timeout_seconds: int = 20
    max_rewrites: int = 4


def build_query_rewrite_config() -> QueryRewriteConfig:
    host = os.environ.get("WEB_FABGPT_HOST", "10.98.193.46")
    default_api_url = f"http://{host}:5110/v1/chat/completions"
    return QueryRewriteConfig(
        enabled=os.environ.get("WEB_FABGPT_RAG_QUERY_REWRITE", "1") != "0",
        api_url=os.environ.get("WEB_FABGPT_RAG_QUERY_REWRITE_API_URL", default_api_url),
        api_key=os.environ.get("WEB_FABGPT_RAG_QUERY_REWRITE_API_KEY", "webfabgpt-local"),
        model=os.environ.get("WEB_FABGPT_RAG_QUERY_REWRITE_MODEL", "webfabgpt-vl-3b"),
        timeout_seconds=int(os.environ.get("WEB_FABGPT_RAG_QUERY_REWRITE_TIMEOUT", "20")),
        max_rewrites=max(1, min(4, int(os.environ.get("WEB_FABGPT_RAG_QUERY_REWRITE_MAX", "3")))),
    )


class QueryRewriter:
    def __init__(self, cfg: QueryRewriteConfig) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self.session.trust_env = False

    def rewrite(self, query: str) -> List[str]:
        normalized_query = " ".join((query or "").split()).strip()
        if not normalized_query:
            return []
        if not self.cfg.enabled or len(normalized_query) < 8:
            return [normalized_query]

        try:
            generated = self._request_rewrites(normalized_query)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Query rewrite failed, fallback to original query: %s", exc)
            generated = []

        supplemental = _build_codegen_fallback_queries(normalized_query)
        queries = _dedupe_queries(
            [normalized_query, *generated, *supplemental],
            max_items=self.cfg.max_rewrites + 3,
        )
        return queries or [normalized_query]

    def _request_rewrites(self, query: str) -> List[str]:
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"

        payload = {
            "model": self.cfg.model,
            "temperature": 0.1,
            "top_p": 0.3,
            "stream": False,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_rewrite_request(query),
                },
            ],
        }
        response = self.session.post(
            self.cfg.api_url,
            headers=headers,
            json=payload,
            timeout=self.cfg.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return parse_rewrite_content(content, max_items=self.cfg.max_rewrites)


def parse_rewrite_content(content: str, *, max_items: int) -> List[str]:
    stripped = (content or "").strip()
    if not stripped:
        return []

    json_match = JSON_BLOCK_PATTERN.search(stripped)
    if json_match:
        try:
            parsed = json.loads(json_match.group(0))
            raw_queries = parsed.get("queries", [])
            if isinstance(raw_queries, Sequence) and not isinstance(raw_queries, str):
                return _dedupe_queries(raw_queries, max_items=max_items)
        except json.JSONDecodeError:
            logger.warning("Failed to parse query rewrite JSON payload")

    fallback_lines = []
    for line in stripped.splitlines():
        candidate = line.strip().lstrip("-").lstrip("*").strip()
        if candidate:
            fallback_lines.append(candidate)
    return _dedupe_queries(fallback_lines, max_items=max_items)


def build_rewrite_request(query: str) -> str:
    if CODE_REQUEST_PATTERN.search(query or ""):
        return (
            "Rewrite the following technical request into retrieval-friendly search queries.\n"
            "The user wants code or a script, but the retrieval system needs document-oriented search queries.\n"
            "Produce 3 complementary queries:\n"
            "- one targeting device structure and artifact terms,\n"
            "- one targeting tutorial/example/procedure sections,\n"
            "- one targeting syntax/command/constraint sections such as geometry, contact, doping, mesh, output.\n"
            "Do not answer the request and do not repeat generic phrases like 'generate code'.\n"
            f"User query: {query}"
        )
    return (
        "Rewrite the following user query into retrieval-friendly search queries.\n"
        f"User query: {query}"
    )


def _dedupe_queries(queries: Sequence[str], *, max_items: int) -> List[str]:
    seen = set()
    unique_queries: List[str] = []
    for query in queries:
        candidate = " ".join(str(query or "").split()).strip()
        if len(candidate) < 4:
            continue
        lowered = candidate.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique_queries.append(candidate)
        if len(unique_queries) >= max_items:
            break
    return unique_queries


def _build_codegen_fallback_queries(query: str) -> List[str]:
    if not CODE_REQUEST_PATTERN.search(query or ""):
        return []

    base_subject = _extract_base_subject(query)
    if not base_subject:
        base_subject = query

    return [
        f"{base_subject} geometry contacts doping mesh",
        f"{base_subject} tutorial example structure generation",
        f"{base_subject} scheme syntax contact doping mesh refinement",
    ]


def _extract_base_subject(query: str) -> str:
    normalized = " ".join((query or "").split()).strip()
    if not normalized:
        return ""
    normalized = FILLER_PATTERN.sub(" ", normalized)
    normalized = normalized.replace("的", " ")
    normalized = " ".join(normalized.split()).strip("：:，,。.;；")
    return normalized.strip()
