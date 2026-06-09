from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

import torch
from sentence_transformers import SentenceTransformer
from transformers import AutoModelForCausalLM, AutoTokenizer


logger = logging.getLogger("Text-RAG-Manager")

DEFAULT_RERANK_INSTRUCTION = (
    "Given a web search query, retrieve relevant passages that answer the query"
)
RERANK_PREFIX = (
    '<|im_start|>system\nJudge whether the Document meets the requirements based '
    'on the Query and the Instruct provided. Note that the answer can only be '
    '"yes" or "no".<|im_end|>\n<|im_start|>user\n'
)
RERANK_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


@dataclass(frozen=True)
class RerankCandidate:
    index: int
    score: float


def format_reranker_instruction(instruction: str, query: str, document: str) -> str:
    return (
        f"<Instruct>: {instruction}\n"
        f"<Query>: {query}\n"
        f"<Document>: {document}"
    )


class TextModelRuntime:
    def __init__(
        self,
        embedding_model_path: str,
        reranker_model_path: str,
        *,
        device: str,
        embedding_batch_size: int = 32,
        rerank_batch_size: int = 4,
    ) -> None:
        self.embedding_model_path = Path(embedding_model_path)
        self.reranker_model_path = Path(reranker_model_path)
        self.device = device
        self.embedding_batch_size = embedding_batch_size
        self.rerank_batch_size = rerank_batch_size
        self.embedder: SentenceTransformer | None = None
        self.reranker_tokenizer = None
        self.reranker_model = None
        self.token_true_id: int | None = None
        self.token_false_id: int | None = None
        self.prefix_tokens: List[int] | None = None
        self.suffix_tokens: List[int] | None = None
        self.max_length = 8192
        self._load_lock = threading.RLock()
        self._infer_lock = threading.Semaphore(1)

    def load(self) -> bool:
        with self._load_lock:
            if self.is_loaded():
                return True

            if not self.embedding_model_path.exists():
                raise FileNotFoundError(f"Embedding model not found: {self.embedding_model_path}")
            if not self.reranker_model_path.exists():
                raise FileNotFoundError(f"Reranker model not found: {self.reranker_model_path}")

            self.embedder = SentenceTransformer(
                str(self.embedding_model_path),
                trust_remote_code=True,
                local_files_only=True,
                device=self.device,
            )

            model_kwargs = {
                "trust_remote_code": True,
                "local_files_only": True,
            }
            if self.device.startswith("cuda"):
                model_kwargs["torch_dtype"] = (
                    torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                )

            self.reranker_tokenizer = AutoTokenizer.from_pretrained(
                str(self.reranker_model_path),
                trust_remote_code=True,
                local_files_only=True,
                padding_side="left",
            )
            self.reranker_model = AutoModelForCausalLM.from_pretrained(
                str(self.reranker_model_path),
                **model_kwargs,
            ).eval()
            self.reranker_model.to(self.device)

            self.token_false_id = self.reranker_tokenizer.convert_tokens_to_ids("no")
            self.token_true_id = self.reranker_tokenizer.convert_tokens_to_ids("yes")
            self.prefix_tokens = self.reranker_tokenizer.encode(
                RERANK_PREFIX,
                add_special_tokens=False,
            )
            self.suffix_tokens = self.reranker_tokenizer.encode(
                RERANK_SUFFIX,
                add_special_tokens=False,
            )
            return True

    def is_loaded(self) -> bool:
        return self.embedder is not None and self.reranker_model is not None

    def close(self) -> None:
        with self._load_lock:
            self.embedder = None
            self.reranker_model = None
            self.reranker_tokenizer = None
            self.token_true_id = None
            self.token_false_id = None
            self.prefix_tokens = None
            self.suffix_tokens = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def embed_query(self, query: str):
        return self._encode_texts([query], prompt_name="query")[0]

    def embed_documents(self, texts: Sequence[str]):
        return self._encode_texts(list(texts), prompt_name="document")

    def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
        instruction: str = DEFAULT_RERANK_INSTRUCTION,
    ) -> List[float]:
        if not documents:
            return []
        if not self.is_loaded():
            raise RuntimeError("TextModelRuntime is not loaded")

        tokenizer = self.reranker_tokenizer
        model = self.reranker_model
        prefix_tokens = self.prefix_tokens or []
        suffix_tokens = self.suffix_tokens or []
        token_true_id = self.token_true_id
        token_false_id = self.token_false_id

        pairs = [
            format_reranker_instruction(instruction, query, document)
            for document in documents
        ]

        scores: List[float] = []
        with self._infer_lock:
            for batch_start in range(0, len(pairs), self.rerank_batch_size):
                batch_pairs = pairs[batch_start: batch_start + self.rerank_batch_size]
                inputs = tokenizer(
                    batch_pairs,
                    padding=False,
                    truncation="longest_first",
                    return_attention_mask=False,
                    max_length=self.max_length - len(prefix_tokens) - len(suffix_tokens),
                )
                for index, token_ids in enumerate(inputs["input_ids"]):
                    inputs["input_ids"][index] = prefix_tokens + token_ids + suffix_tokens
                inputs = tokenizer.pad(
                    inputs,
                    padding=True,
                    return_tensors="pt",
                    max_length=self.max_length,
                )
                for key, value in inputs.items():
                    inputs[key] = value.to(model.device)
                with torch.no_grad():
                    logits = model(**inputs).logits[:, -1, :]
                batch_scores = torch.stack(
                    [logits[:, token_false_id], logits[:, token_true_id]],
                    dim=1,
                )
                batch_scores = torch.nn.functional.log_softmax(batch_scores, dim=1)
                scores.extend(batch_scores[:, 1].exp().tolist())
        return scores

    def _encode_texts(self, texts: Sequence[str], *, prompt_name: str):
        if not texts:
            return []
        if not self.is_loaded():
            raise RuntimeError("TextModelRuntime is not loaded")

        with self._infer_lock:
            embeddings = self.embedder.encode(
                list(texts),
                prompt_name=prompt_name,
                batch_size=self.embedding_batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
        return embeddings
