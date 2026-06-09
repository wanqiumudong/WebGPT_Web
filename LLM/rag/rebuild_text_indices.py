from __future__ import annotations

import argparse
import logging
from pathlib import Path

import torch

from Rag_Framework import config_manager
from Rag_Framework.config_manager import (
    MILVUS_URI,
    get_collection_db_name,
    initialize_knowledge_base_structure,
)
from Rag_Framework.text_context_pool import TextCollectionContextRegistry
from Rag_Framework.text_runtime import TextModelRuntime


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("Text-RAG-Rebuild")

RAG_ROOT = Path(__file__).resolve().parent
MODELS_ROOT = Path(
    config_manager.normalize_local_rag_path(
        str(Path(config_manager.RAG_ROOT) / "models")
    )
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild local text RAG indices")
    parser.add_argument("--user-id", default="yphu")
    parser.add_argument("--config-id", required=True)
    parser.add_argument("--file-path")
    parser.add_argument("--clear", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    initialize_knowledge_base_structure()
    rag_configurations = config_manager.rag_configurations
    if args.config_id not in rag_configurations:
        parser.error(f"Unknown config_id: {args.config_id}")

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    runtime = TextModelRuntime(
        str(MODELS_ROOT / "text" / "qwen" / "Qwen3-Embedding-0.6B"),
        str(MODELS_ROOT / "text" / "qwen" / "Qwen3-Reranker-0.6B"),
        device=device,
    )
    runtime.load()
    registry = TextCollectionContextRegistry(runtime, milvus_uri=MILVUS_URI)

    context = registry.get_or_create(
        collection_name=config_manager.get_user_collection_name(args.user_id, args.config_id),
        database_name=get_collection_db_name(args.user_id, args.config_id),
        knowledge_path=config_manager.get_user_knowledge_path(args.user_id, args.config_id),
        data_path=config_manager.get_user_data_path(args.user_id, args.config_id),
    )

    if args.clear:
        logger.info("Clearing collection before rebuild")
        context.clear_collection()

    if args.file_path:
        result = context.process_file(
            file_path=str(Path(args.file_path).resolve()),
            force_reindex=True,
        )
        logger.info("Indexed one file: %s", result)
        return 0

    documents = context.refresh_documents(force_refresh=True)
    file_paths = [document["file_path"] for document in documents]
    if not file_paths:
        logger.info("No PDF files found in %s", context.knowledge_path)
        return 0

    for index, file_path in enumerate(file_paths, start=1):
        logger.info("[%s/%s] indexing %s", index, len(file_paths), file_path)
        result = context.process_file(file_path=file_path, force_reindex=True)
        logger.info("Completed: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
