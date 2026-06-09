"""Shared model runtime and per-collection context pool for RAG."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import torch

from Rag_Framework.colpali_manager import ColPaliManager

logger = logging.getLogger("ColPali-RAG-Manager")


class ModelRuntime:
    """Own exactly one ColPali model/processor pair for the whole service."""

    def __init__(self, model_path: str, base_model_path: str, device: str) -> None:
        self.model_path = model_path
        self.base_model_path = base_model_path
        self.device = device
        self.model = None
        self.processor = None
        self._load_lock = threading.RLock()
        self._embed_lock = threading.Semaphore(1)

    def load(self) -> bool:
        with self._load_lock:
            if self.model is not None and self.processor is not None:
                return True

            manager = ColPaliManager(
                model_path=self.model_path,
                base_model_path=self.base_model_path,
                device=self.device,
            )
            success = manager.load_model()
            if not success:
                return False

            self.model = manager.model
            self.processor = manager.processor
            return True

    def is_loaded(self) -> bool:
        return self.model is not None and self.processor is not None

    def create_manager(self) -> ColPaliManager:
        if not self.is_loaded():
            raise RuntimeError("ModelRuntime is not loaded")

        manager = ColPaliManager(
            model_path=self.model_path,
            base_model_path=self.base_model_path,
            device=self.device,
        )
        manager.model = self.model
        manager.processor = self.processor
        return manager

    def embed_query(self, query: str):
        if not self.is_loaded():
            raise RuntimeError("ColPali model is not loaded")

        with self._embed_lock:
            inputs = self.processor.process_queries([query])
            inputs = {key: value.to(self.model.device) for key, value in inputs.items()}
            autocast_context = (
                torch.cuda.amp.autocast()
                if torch.cuda.is_available()
                else nullcontext()
            )
            with autocast_context:
                with torch.no_grad():
                    query_embedding = self.model(**inputs)
            return query_embedding[0].float().cpu().numpy()

    def close(self) -> None:
        with self._load_lock:
            self.model = None
            self.processor = None


@dataclass
class CollectionContext:
    """Per-collection runtime state with an isolated Milvus client."""

    collection_name: str
    manager: ColPaliManager
    database_name: str
    lock: threading.RLock = field(default_factory=threading.RLock)
    last_used: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.last_used = time.time()

    def ensure_ready(self) -> bool:
        with self.lock:
            retriever = getattr(self.manager, "retriever", None)
            client = getattr(retriever, "client", None)

            if retriever is None or client is None:
                success = self.manager.setup_milvus(collection_name=self.collection_name)
                if not success:
                    return False
            else:
                try:
                    if client.has_collection(self.collection_name):
                        client.load_collection(self.collection_name)
                    else:
                        success = self.manager.setup_milvus(
                            collection_name=self.collection_name
                        )
                        if not success:
                            return False
                except Exception as exc:
                    logger.warning(
                        "加载集合 %s 失败，尝试重新初始化 Milvus 上下文: %s",
                        self.collection_name,
                        exc,
                    )
                    success = self.manager.setup_milvus(collection_name=self.collection_name)
                    if not success:
                        return False

            self.touch()
            return True

    def sync_documents(self, force_refresh: bool = False) -> bool:
        with self.lock:
            if not self.ensure_ready():
                return False

            if not force_refresh and getattr(self.manager, "documents", None):
                self.touch()
                return True

            client = self.manager.retriever.client
            stats = client.get_collection_stats(self.collection_name)
            row_count = stats.get("row_count", 0)
            if row_count <= 0:
                self.manager.documents = {}
                self.touch()
                return True

            success = self.manager._sync_documents_standalone(self.collection_name)
            self.touch()
            return success

    def search(self, query_vector, top_k: int, search_id: Optional[str] = None):
        with self.lock:
            if not self.ensure_ready():
                raise RuntimeError(
                    f"集合 {self.collection_name} 当前不可用，无法执行检索"
                )
            self.touch()
            return self.manager.retriever.search(
                query_vector,
                top_k,
                text_extractor=self.manager.extract_text_from_pdf_by_image_path,
                log_search_id=search_id,
            )

    def process_file(self, **kwargs):
        with self.lock:
            if not self.ensure_ready():
                raise RuntimeError(
                    f"集合 {self.collection_name} 当前不可用，无法处理文档"
                )
            result = self.manager.process_file(**kwargs)
            self.touch()
            return result

    def close(self) -> None:
        with self.lock:
            try:
                retriever = getattr(self.manager, "retriever", None)
                client = getattr(retriever, "client", None)
                if client is not None:
                    try:
                        del retriever.client
                    except Exception:
                        pass
                self.manager.retriever = None
                self.manager.documents = {}
            except Exception as exc:
                logger.warning("关闭集合上下文 %s 失败: %s", self.collection_name, exc)


class CollectionContextRegistry:
    """Cache per-collection managers while sharing one model runtime."""

    def __init__(self, model_runtime: ModelRuntime, max_contexts: int = 16) -> None:
        self.model_runtime = model_runtime
        self.max_contexts = max_contexts
        self._contexts: Dict[str, CollectionContext] = {}
        self._lock = threading.RLock()

    def get_or_create(self, collection_name: str) -> CollectionContext:
        with self._lock:
            context = self._contexts.get(collection_name)
            if context is not None:
                context.touch()
                return context

            if len(self._contexts) >= self.max_contexts:
                self._evict_one()

            manager = self.model_runtime.create_manager()
            context = CollectionContext(
                collection_name=collection_name,
                manager=manager,
                database_name=f"rag_{collection_name}",
            )
            self._contexts[collection_name] = context

        if not context.ensure_ready():
            with self._lock:
                self._contexts.pop(collection_name, None)
            raise RuntimeError(f"初始化集合上下文失败: {collection_name}")

        return context

    def get_existing(self, collection_name: str) -> Optional[CollectionContext]:
        with self._lock:
            context = self._contexts.get(collection_name)
            if context is not None:
                context.touch()
            return context

    def list_contexts(self) -> List[CollectionContext]:
        with self._lock:
            return list(self._contexts.values())

    def count(self) -> int:
        with self._lock:
            return len(self._contexts)

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

    def _evict_one(self) -> None:
        evict_name = None
        evict_context = None
        for collection_name, context in self._contexts.items():
            if evict_context is None or context.last_used < evict_context.last_used:
                evict_name = collection_name
                evict_context = context

        if evict_name is None or evict_context is None:
            return

        self._contexts.pop(evict_name, None)
        evict_context.close()
        logger.info("已回收空闲集合上下文: %s", evict_name)
