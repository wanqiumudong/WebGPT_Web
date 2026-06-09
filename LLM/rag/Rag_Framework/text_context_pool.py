from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from Rag_Framework.config_manager import get_public_doc_id, get_stable_doc_id
from Rag_Framework.text_processing import chunk_pdf_document, ensure_pdf_file, extract_pdf_document
from Rag_Framework.text_retriever import TextMilvusRetriever
from Rag_Framework.text_runtime import TextModelRuntime


logger = logging.getLogger("Text-RAG-Manager")
DOT_LEADER_PATTERN = re.compile(r"(?:\.\s*){4,}|\.{4,}")
SENTENCE_PATTERN = re.compile(r"[。！？.!?;:]+")


@dataclass
class TextCollectionContext:
    collection_name: str
    database_name: str
    knowledge_path: Path
    data_path: Path
    runtime: TextModelRuntime
    retriever: TextMilvusRetriever
    lock: threading.RLock = field(default_factory=threading.RLock)
    documents: Dict[str, Dict] = field(default_factory=dict)
    last_used: float = field(default_factory=time.time)

    def ensure_ready(self) -> None:
        with self.lock:
            self.knowledge_path.mkdir(parents=True, exist_ok=True)
            self.data_path.mkdir(parents=True, exist_ok=True)
            self.retriever.ensure_collection()
            if not self.documents:
                self.documents = self._load_manifest()
            self.last_used = time.time()

    def refresh_documents(self, *, force_refresh: bool = False) -> List[Dict]:
        with self.lock:
            self.ensure_ready()
            if force_refresh:
                self.documents = self._load_manifest()

            file_map = {
                path.resolve(): path
                for path in self.knowledge_path.glob("*.pdf")
                if path.is_file()
            }
            stale_doc_ids = [
                doc_id
                for doc_id, manifest_entry in self.documents.items()
                if manifest_entry.get("file_path") and Path(manifest_entry["file_path"]).resolve() not in file_map
            ]
            for stale_doc_id in stale_doc_ids:
                deleted_count = self.retriever.delete_document(stale_doc_id)
                logger.info(
                    "移除孤儿文档向量: collection=%s doc_id=%s deleted_chunks=%s",
                    self.collection_name,
                    stale_doc_id,
                    deleted_count,
                )

            merged: Dict[str, Dict] = {}
            for file_path in sorted(file_map):
                resolved = str(file_path)
                public_doc_id = get_public_doc_id(resolved)
                manifest_entry = dict(self.documents.get(public_doc_id, {}))
                file_stat = os.stat(file_path)
                merged[public_doc_id] = {
                    "doc_id": public_doc_id,
                    "int_doc_id": get_stable_doc_id(resolved),
                    "filename": file_path.name,
                    "file_path": resolved,
                    "file_exists": True,
                    "file_size": file_stat.st_size,
                    "file_size_formatted": _format_file_size(file_stat.st_size),
                    "last_modified_time": time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(file_stat.st_mtime),
                    ),
                    "upload_time": manifest_entry.get("upload_time")
                    or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(file_stat.st_ctime)),
                    "status": manifest_entry.get("status", "unprocessed"),
                    "processed": bool(manifest_entry.get("processed", False)),
                    "total_pages": int(manifest_entry.get("total_pages", 0) or 0),
                    "processed_pages": int(
                        manifest_entry.get("processed_pages", manifest_entry.get("total_pages", 0))
                        or 0
                    ),
                    "file_mtime": file_stat.st_mtime,
                    "chunks_count": int(manifest_entry.get("chunks_count", 0) or 0),
                    "images_count": int(manifest_entry.get("chunks_count", 0) or 0),
                    "text_preview": manifest_entry.get("text_preview", ""),
                }

            self.documents = merged
            self._save_manifest()
            self.last_used = time.time()
            return self.list_documents()

    def list_documents(self) -> List[Dict]:
        return sorted(
            self.documents.values(),
            key=lambda item: (item.get("last_modified_time", ""), item.get("filename", "")),
            reverse=True,
        )

    def process_file(
        self,
        *,
        file_path: str,
        progress_callback=None,
        force_reindex: bool = False,
    ) -> Dict:
        with self.lock:
            self.ensure_ready()
            pdf_path = ensure_pdf_file(file_path)
            public_doc_id = get_public_doc_id(str(pdf_path))
            int_doc_id = get_stable_doc_id(str(pdf_path))
            file_stat = os.stat(pdf_path)
            existing = self.documents.get(public_doc_id)
            stored_mtime = float(existing.get("file_mtime", 0) or 0) if existing else 0.0
            file_unchanged = (
                existing is not None
                and int(existing.get("file_size", 0) or 0) == file_stat.st_size
                and abs(stored_mtime - file_stat.st_mtime) < 1e-6
            )
            if (
                existing
                and existing.get("processed")
                and file_unchanged
                and not force_reindex
                and self.retriever.has_document(public_doc_id)
            ):
                return {
                    "success": True,
                    "already_processed": True,
                    "doc_id": public_doc_id,
                    "int_doc_id": int_doc_id,
                    "total_pages": existing.get("total_pages", 0),
                    "processed_pages": existing.get("processed_pages", 0),
                    "total_chunks": existing.get("chunks_count", 0),
                    "text_preview": existing.get("text_preview", ""),
                }

            _emit_progress(progress_callback, {"progress": 10, "current_step": "extracting_text"})
            document = extract_pdf_document(str(pdf_path))
            total_pages = document.page_count

            _emit_progress(
                progress_callback,
                {
                    "progress": 25,
                    "current_step": "chunking_text",
                    "total_pages": total_pages,
                    "processed_pages": total_pages,
                    "current_page": total_pages,
                },
            )
            chunks = chunk_pdf_document(document)
            if not chunks:
                raise ValueError("PDF does not contain enough extractable text")

            _emit_progress(progress_callback, {"progress": 45, "current_step": "generating_embeddings"})
            embeddings = self.runtime.embed_documents([chunk.text_content for chunk in chunks])

            _emit_progress(progress_callback, {"progress": 85, "current_step": "inserting_data"})
            records = []
            for chunk, vector in zip(chunks, embeddings):
                records.append(
                    {
                        "vector": vector.tolist() if hasattr(vector, "tolist") else list(vector),
                        "doc_id": public_doc_id,
                        "chunk_id": f"{public_doc_id}:{chunk.chunk_index}",
                        "file_path": str(pdf_path),
                        "filename": pdf_path.name,
                        "text_content": chunk.text_content,
                        "page_num_start": chunk.page_num_start,
                        "page_num_end": chunk.page_num_end,
                        "section_title": chunk.section_title,
                        "section_path": chunk.section_path,
                        "section_level": chunk.section_level,
                        "chunk_order_in_section": chunk.chunk_order_in_section,
                    }
                )
            self.retriever.upsert_records(records)

            _emit_progress(progress_callback, {"progress": 97, "current_step": "finalizing"})
            self.documents[public_doc_id] = {
                "doc_id": public_doc_id,
                "int_doc_id": int_doc_id,
                "filename": pdf_path.name,
                "file_path": str(pdf_path),
                "file_exists": True,
                "file_size": file_stat.st_size,
                "file_size_formatted": _format_file_size(file_stat.st_size),
                "last_modified_time": time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(file_stat.st_mtime),
                ),
                "upload_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "status": "processed",
                "processed": True,
                "file_mtime": file_stat.st_mtime,
                "total_pages": total_pages,
                "processed_pages": total_pages,
                "chunks_count": len(chunks),
                "images_count": len(chunks),
                "text_preview": chunks[0].text_content[:240],
            }
            self._save_manifest()
            self.last_used = time.time()
            return {
                "success": True,
                "already_processed": False,
                "doc_id": public_doc_id,
                "int_doc_id": int_doc_id,
                "total_pages": total_pages,
                "processed_pages": total_pages,
                "total_chunks": len(chunks),
                "text_preview": chunks[0].text_content[:240],
            }

    def search(
        self,
        query: str,
        *,
        top_n: int,
        top_k: int,
        retrieval_queries: Optional[List[str]] = None,
    ) -> List[Dict]:
        with self.lock:
            self.ensure_ready()
            search_queries = _normalize_retrieval_queries(query, retrieval_queries)
            fused_hits = _fuse_multi_query_hits(
                runtime=self.runtime,
                retriever=self.retriever,
                queries=search_queries,
                limit=top_n,
            )
            if not fused_hits:
                return []
            hits = [item["hit"] for item in fused_hits]
            scores = self.runtime.rerank(
                query=query,
                documents=[hit.text_content for hit in hits],
            )
            reranked = []
            for fused_hit, rerank_score in zip(fused_hits, scores):
                hit = fused_hit["hit"]
                quality_adjustment = _estimate_text_quality_adjustment(hit.text_content)
                retrieval_bonus = float(fused_hit["bonus"])
                reranked.append(
                    {
                        "score": float(rerank_score) + quality_adjustment + retrieval_bonus,
                        "raw_rerank_score": float(rerank_score),
                        "quality_adjustment": float(quality_adjustment),
                        "retrieval_fusion_score": retrieval_bonus,
                        "doc_id": hit.doc_id,
                        "chunk_id": hit.chunk_id,
                        "file_path": hit.file_path,
                        "filename": hit.filename,
                        "text_content": hit.text_content,
                        "page_num": hit.page_num_start,
                        "page_num_start": hit.page_num_start,
                        "page_num_end": hit.page_num_end,
                        "section_title": hit.section_title,
                        "section_path": hit.section_path,
                        "section_level": hit.section_level,
                        "chunk_order_in_section": hit.chunk_order_in_section,
                        "image_path": "",
                    }
                )
            filtered = [item for item in reranked if item["quality_adjustment"] > -0.22]
            final_candidates = filtered if len(filtered) >= top_k else reranked
            final_candidates.sort(key=lambda item: item["score"], reverse=True)
            self.last_used = time.time()
            return final_candidates[:top_k]

    def delete_document(self, doc_id: str, *, physical_delete: bool) -> Dict:
        with self.lock:
            self.ensure_ready()
            document = self.documents.get(doc_id)
            if document is None:
                raise KeyError(f"Document not found: {doc_id}")
            delete_count = self.retriever.delete_document(doc_id)
            file_path = Path(document["file_path"])
            if physical_delete and file_path.exists():
                file_path.unlink()
            del self.documents[doc_id]
            self._save_manifest()
            self.last_used = time.time()
            return {
                "doc_id": doc_id,
                "filename": document["filename"],
                "deleted_chunks": delete_count,
                "file_deleted": physical_delete,
            }

    def clear_collection(self) -> None:
        with self.lock:
            self.ensure_ready()
            self.retriever.clear_collection()
            self.documents = {}
            self._save_manifest()
            self.last_used = time.time()

    def row_count(self) -> int:
        with self.lock:
            self.ensure_ready()
            return self.retriever.collection_row_count()

    def close(self) -> None:
        with self.lock:
            self.retriever.close()
            self.documents = {}

    @property
    def manifest_path(self) -> Path:
        return self.data_path / "documents.json"

    def _load_manifest(self) -> Dict[str, Dict]:
        if not self.manifest_path.exists():
            return {}
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("文档清单损坏，已忽略: %s", self.manifest_path)
            return {}

    def _save_manifest(self) -> None:
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(self.documents, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class TextCollectionContextRegistry:
    def __init__(self, runtime: TextModelRuntime, *, milvus_uri: str, max_contexts: int = 16) -> None:
        self.runtime = runtime
        self.milvus_uri = milvus_uri
        self.max_contexts = max_contexts
        self._contexts: Dict[str, TextCollectionContext] = {}
        self._lock = threading.RLock()

    def get_or_create(
        self,
        *,
        collection_name: str,
        database_name: str,
        knowledge_path: str,
        data_path: str,
    ) -> TextCollectionContext:
        with self._lock:
            context = self._contexts.get(collection_name)
            if context is not None:
                context.last_used = time.time()
                return context

            if len(self._contexts) >= self.max_contexts:
                evicted = self._evict_one()
                if not evicted and len(self._contexts) >= self.max_contexts:
                    raise RuntimeError(
                        f"文本上下文池已满（max_contexts={self.max_contexts}），请稍后重试"
                    )

            context = TextCollectionContext(
                collection_name=collection_name,
                database_name=database_name,
                knowledge_path=Path(knowledge_path),
                data_path=Path(data_path),
                runtime=self.runtime,
                retriever=TextMilvusRetriever(
                    uri=self.milvus_uri,
                    db_name=database_name,
                    collection_name=collection_name,
                ),
            )
            self._contexts[collection_name] = context

        context.ensure_ready()
        return context

    def remove(self, collection_name: str) -> None:
        with self._lock:
            context = self._contexts.pop(collection_name, None)
        if context is not None:
            context.close()

    def close_all(self) -> None:
        with self._lock:
            contexts = list(self._contexts.values())
            self._contexts.clear()
        for context in contexts:
            context.close()

    def count(self) -> int:
        with self._lock:
            return len(self._contexts)

    def _evict_one(self) -> bool:
        candidates = sorted(
            self._contexts.items(),
            key=lambda item: item[1].last_used,
        )
        for collection_name, context in candidates:
            if not context.lock.acquire(blocking=False):
                logger.info("跳过忙碌中的文本上下文回收: %s", collection_name)
                continue
            context.lock.release()
            context = self._contexts.pop(collection_name)
            context.close()
            logger.info("回收空闲文本上下文: %s", collection_name)
            return True
        return False


def _emit_progress(progress_callback, payload: Dict) -> None:
    if progress_callback is not None:
        progress_callback(payload)


def _format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 ** 2):.1f} MB"


def _estimate_text_quality_adjustment(text: str) -> float:
    normalized = (text or "").strip()
    if not normalized:
        return -0.35

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    line_count = len(lines)
    short_line_ratio = 0.0
    avg_line_length = len(normalized)
    if lines:
        avg_line_length = sum(len(line) for line in lines) / line_count
        short_line_ratio = sum(len(line) <= 28 for line in lines) / line_count

    sentence_count = len(SENTENCE_PATTERN.findall(normalized))
    dot_leader_count = len(DOT_LEADER_PATTERN.findall(normalized))
    alpha_numeric_chars = sum(ch.isalnum() for ch in normalized)
    symbol_ratio = 1.0 - (alpha_numeric_chars / max(len(normalized), 1))

    adjustment = 0.0
    if sentence_count >= 2:
        adjustment += 0.06
    if avg_line_length >= 48:
        adjustment += 0.05
    if short_line_ratio > 0.65:
        adjustment -= 0.14
    if dot_leader_count > 0:
        adjustment -= min(0.26, 0.08 * dot_leader_count)
    if line_count >= 8 and sentence_count == 0:
        adjustment -= 0.18
    if symbol_ratio > 0.30:
        adjustment -= min(0.12, (symbol_ratio - 0.30) * 0.6)
    return adjustment


def _normalize_retrieval_queries(query: str, retrieval_queries: Optional[List[str]]) -> List[str]:
    candidates = [query]
    if retrieval_queries:
        candidates.extend(retrieval_queries)

    normalized: List[str] = []
    seen = set()
    for item in candidates:
        candidate = " ".join(str(item or "").split()).strip()
        if len(candidate) < 4:
            continue
        lowered = candidate.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(candidate)
    return normalized[:5] or [query]


def _fuse_multi_query_hits(
    *,
    runtime: TextModelRuntime,
    retriever: TextMilvusRetriever,
    queries: List[str],
    limit: int,
) -> List[Dict[str, object]]:
    fused = {}
    rrf_k = 60.0

    for retrieval_query in queries:
        query_vector = runtime.embed_query(retrieval_query)
        hits = retriever.search(query_vector, limit=limit)
        for rank, hit in enumerate(hits, start=1):
            chunk_key = hit.chunk_id or f"{hit.doc_id}:{hit.page_num_start}:{hit.chunk_order_in_section}"
            bonus = 1.0 / (rrf_k + rank)
            current = fused.get(chunk_key)
            if current is None:
                fused[chunk_key] = {
                    "hit": hit,
                    "bonus": bonus,
                    "best_rank": rank,
                }
                continue

            current["bonus"] += bonus
            if rank < current["best_rank"]:
                current["best_rank"] = rank
                current["hit"] = hit

    ordered = sorted(
        fused.values(),
        key=lambda item: (item["bonus"], -item["best_rank"]),
        reverse=True,
    )
    result_hits: List[Dict[str, object]] = []
    for item in ordered[: limit * 2]:
        result_hits.append({"hit": item["hit"], "bonus": float(item["bonus"])})
    return result_hits
