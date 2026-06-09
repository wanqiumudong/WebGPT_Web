from __future__ import annotations

import atexit
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
import os
import shutil
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests
import torch
from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.utils import secure_filename

from Rag_Framework import config_manager
from Rag_Framework.config_manager import (
    CHATBOT_PORT,
    CONFIG_FILE,
    MILVUS_URI,
    RAG_MANAGER_PORT,
    SOCKET_TIMEOUT,
    get_collection_db_name,
    get_public_doc_id,
    get_stable_doc_id,
    get_visible_configurations,
    initialize_knowledge_base_structure,
    is_milvus_available,
    is_user_visible_config,
    load_rag_configurations,
    save_rag_configurations,
)
from Rag_Framework.query_rewriter import QueryRewriter, build_query_rewrite_config
from Rag_Framework.task_store import TaskStore
from Rag_Framework.text_context_pool import TextCollectionContextRegistry
from Rag_Framework.text_runtime import TextModelRuntime


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("text_rag_service.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("Text-RAG-Manager")


@dataclass(frozen=True)
class ServiceConfig:
    service_host: str
    bind_host: str
    rag_root: Path
    models_root: Path
    embedding_model_path: Path
    reranker_model_path: Path
    tasks_storage_path: Path


SERVICE_HOST = os.environ.get("WEB_FABGPT_HOST", "10.98.193.46")
BIND_HOST = os.environ.get("WEB_FABGPT_BIND_HOST", SERVICE_HOST)
RAG_ROOT = Path(__file__).resolve().parent
MODELS_ROOT = Path(os.environ.get("WEB_FABGPT_RAG_MODELS_DIR", str(RAG_ROOT / "models")))
SERVICE_CONFIG = ServiceConfig(
    service_host=SERVICE_HOST,
    bind_host=BIND_HOST,
    rag_root=RAG_ROOT,
    models_root=MODELS_ROOT,
    embedding_model_path=MODELS_ROOT / "text" / "qwen" / "Qwen3-Embedding-0.6B",
    reranker_model_path=MODELS_ROOT / "text" / "qwen" / "Qwen3-Reranker-0.6B",
    tasks_storage_path=RAG_ROOT / "user_tasks",
)


app = Flask(__name__)
CORS(app)

internal_http_session = requests.Session()
internal_http_session.trust_env = False

rag_configurations = config_manager.rag_configurations
task_store = TaskStore(str(SERVICE_CONFIG.tasks_storage_path))
user_session_states: dict[str, dict] = {}
user_active_configurations: dict[str, str] = {}
model_runtime: TextModelRuntime | None = None
context_registry: TextCollectionContextRegistry | None = None
query_rewriter: QueryRewriter | None = None
service_lock = threading.RLock()
upload_executor = ThreadPoolExecutor(
    max_workers=max(1, int(os.environ.get("WEB_FABGPT_RAG_UPLOAD_WORKERS", "2"))),
    thread_name_prefix="text-rag-upload",
)


def get_user_active_config_id(user_id: str) -> str:
    preferred = user_active_configurations.get(str(user_id or "anonymous"))
    if preferred in rag_configurations and is_user_visible_config(rag_configurations[preferred], user_id):
        return preferred
    if "default" in rag_configurations:
        return "default"
    if "none" in rag_configurations:
        return "none"
    return next(iter(rag_configurations), "none")


def set_user_active_config_id(user_id: str, config_id: str) -> None:
    user_active_configurations[str(user_id or "anonymous")] = config_id


def initialize_rag() -> bool:
    global model_runtime, context_registry, rag_configurations, query_rewriter

    with service_lock:
        if context_registry is not None:
            context_registry.close_all()
        if model_runtime is not None:
            model_runtime.close()

        rag_configurations = config_manager.rag_configurations
        if not rag_configurations:
            load_rag_configurations()
            rag_configurations = config_manager.rag_configurations

        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        runtime = TextModelRuntime(
            str(SERVICE_CONFIG.embedding_model_path),
            str(SERVICE_CONFIG.reranker_model_path),
            device=device,
        )
        runtime.load()
        model_runtime = runtime
        context_registry = TextCollectionContextRegistry(
            runtime,
            milvus_uri=MILVUS_URI,
            max_contexts=int(os.environ.get("WEB_FABGPT_RAG_MAX_CONTEXTS", "16")),
        )
        query_rewriter = QueryRewriter(build_query_rewrite_config())

    logger.info(
        "Text RAG runtime initialized: device=%s, milvus=%s",
        device,
        MILVUS_URI,
    )
    return True


def ensure_runtime_ready() -> bool:
    with service_lock:
        if model_runtime is not None and context_registry is not None and query_rewriter is not None:
            return True
    return initialize_rag()


def get_collection_context(user_id: str, config_id: str, *, force_refresh: bool = False):
    config = rag_configurations.get(config_id)
    if config is None:
        raise KeyError(f"配置不存在: {config_id}")
    if not is_user_visible_config(config, user_id):
        raise PermissionError(f"用户 {user_id} 无权访问知识库 {config_id}")
    if config_id == "none":
        return config_id, config, None

    if not ensure_runtime_ready():
        raise RuntimeError("RAG runtime initialization failed")

    assert context_registry is not None
    collection_name = config_manager.get_user_collection_name(user_id, config_id)
    context = context_registry.get_or_create(
        collection_name=collection_name,
        database_name=get_collection_db_name(user_id, config_id),
        knowledge_path=config_manager.get_user_knowledge_path(user_id, config_id),
        data_path=config_manager.get_user_data_path(user_id, config_id),
    )
    if force_refresh:
        context.refresh_documents(force_refresh=True)
    return config_id, config, context


def switch_knowledge_base(config_id: str, *, user_id: str) -> bool:
    if config_id not in rag_configurations:
        return False
    if config_id != "none":
        get_collection_context(user_id, config_id, force_refresh=False)
    set_user_active_config_id(user_id, config_id)
    return True


def notify_chatbot_sync(config_id: str) -> int | str:
    try:
        response = internal_http_session.post(
            f"http://{SERVICE_HOST}:{CHATBOT_PORT}/set_active_configuration",
            json={"config_id": config_id, "is_sync_request": True},
            timeout=3,
        )
        return response.status_code
    except Exception as exc:  # noqa: BLE001
        logger.warning("同步通知 chatbot 失败: %s", exc)
        return f"error: {exc}"


def create_task_payload(*, file_path: str, file_name: str, original_name: str, config_id: str, user_id: str) -> dict:
    tracking_id = get_public_doc_id(file_path)
    return {
        "task_id": tracking_id,
        "doc_id": tracking_id,
        "tracking_id": tracking_id,
        "status": "processing",
        "progress": 0,
        "file_name": file_name,
        "original_name": original_name,
        "file_path": file_path,
        "config_id": config_id,
        "user_id": user_id,
        "start_time": time.time(),
        "current_step": "queued",
        "current_page": 0,
        "processed_pages": 0,
        "total_pages": 0,
        "error": None,
    }


def update_task(task_id: str, payload: dict) -> None:
    current = task_store.get_task(task_id) or {"task_id": task_id, "doc_id": task_id}
    current.update(payload)
    task_store.upsert_task(task_id, current)


def background_process_upload(task_id: str, *, user_id: str, config_id: str, file_path: str) -> None:
    try:
        _, _, context = get_collection_context(user_id, config_id, force_refresh=False)
        if context is None:
            raise RuntimeError("知识库上下文初始化失败")

        def progress_callback(progress_info: dict) -> None:
            update_task(task_id, progress_info)

        result = context.process_file(file_path=file_path, progress_callback=progress_callback)
        update_task(
            task_id,
            {
                "status": "completed",
                "progress": 100,
                "current_step": "finalizing",
                "processed_pages": result.get("processed_pages", 0),
                "total_pages": result.get("total_pages", 0),
                "chunks_count": result.get("total_chunks", 0),
                "int_doc_id": result.get("int_doc_id"),
                "text_preview": result.get("text_preview", ""),
                "keep_until": time.time() + 300,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("后台处理文档失败")
        update_task(
            task_id,
            {
                "status": "failed",
                "error": str(exc),
                "keep_until": time.time() + 300,
            },
        )


def run_rag_search(*, query: str, user_id: str, config_id: str, top_k: int):
    config_id, config, context = get_collection_context(user_id, config_id, force_refresh=False)
    knowledge_base_name = config.get("display_name") or config.get("name") or config_id
    if context is None:
        return config_id, knowledge_base_name, [], [query]
    retrieval_queries = query_rewriter.rewrite(query) if query_rewriter is not None else [query]
    logger.info("RAG retrieval queries: %s", retrieval_queries)
    top_n = max(top_k * 8, 40)
    results = context.search(
        query,
        top_n=top_n,
        top_k=top_k,
        retrieval_queries=retrieval_queries,
    )
    return config_id, knowledge_base_name, results, retrieval_queries


@app.route("/get_rag_configurations", methods=["GET"])
def get_rag_configurations():
    user_id = request.args.get("user_id", "anonymous")
    active_config_id = get_user_active_config_id(user_id)
    configurations = get_visible_configurations(user_id, active_config_id=active_config_id)
    return jsonify({"configurations": configurations}), 200


@app.route("/create_rag_configuration", methods=["POST"])
def create_rag_configuration():
    data = request.get_json(force=True) or {}
    user_id = data.get("user_id", "anonymous")
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "知识库名称不能为空"}), 400

    config_id = f"config_{int(time.time())}_{hashlib.md5(f'{user_id}:{name}:{time.time()}'.encode()).hexdigest()[:8]}"
    rag_configurations[config_id] = {
        "id": config_id,
        "name": name,
        "display_name": name,
        "folder": config_manager.get_user_knowledge_path(user_id, config_id),
        "db_path": config_manager.get_user_data_path(user_id, config_id),
        "active": False,
        "created_time": time.time(),
        "readonly": False,
        "owner_id": str(user_id),
    }
    Path(rag_configurations[config_id]["folder"]).mkdir(parents=True, exist_ok=True)
    Path(rag_configurations[config_id]["db_path"]).mkdir(parents=True, exist_ok=True)
    save_rag_configurations()
    return jsonify(
        {
            "success": True,
            "configuration": rag_configurations[config_id],
            "config": rag_configurations[config_id],
        }
    ), 200


@app.route("/set_active_configuration", methods=["POST"])
def set_active_configuration():
    data = request.get_json(force=True) or {}
    user_id = data.get("user_id", "anonymous")
    config_id = data.get("config_id", "default")

    try:
        success = switch_knowledge_base(config_id, user_id=user_id)
    except PermissionError as exc:
        return jsonify({"error": str(exc), "success": False}), 403
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc), "success": False}), 503

    if not success:
        return jsonify({"error": "知识库切换失败", "success": False}), 400

    chatbot_sync_status = notify_chatbot_sync(config_id)
    config = rag_configurations.get(config_id, {})
    config_name = config.get("display_name") or config.get("name") or config_id
    return jsonify(
        {
            "success": True,
            "message": f"当前知识库已切换为 {config_name}",
            "config_id": config_id,
            "config_name": config_name,
            "chatbot_sync_status": chatbot_sync_status,
        }
    ), 200


@app.route("/delete_rag_configuration", methods=["POST"])
def delete_rag_configuration():
    data = request.get_json(force=True) or {}
    user_id = data.get("user_id", "anonymous")
    config_id = data.get("config_id", "")
    if config_id not in rag_configurations:
        return jsonify({"error": "知识库配置不存在"}), 404
    if config_manager.is_readonly_config(config_id):
        return jsonify({"error": f"配置 {config_id} 为只读，不允许删除"}), 403

    config = rag_configurations[config_id]
    if not is_user_visible_config(config, user_id):
        return jsonify({"error": "无权访问该知识库配置"}), 403

    collection_name = config_manager.get_user_collection_name(user_id, config_id)
    database_name = get_collection_db_name(user_id, config_id)
    deleted_config = dict(config)
    deleted_folders = []

    if is_milvus_available():
        try:
            admin_client = requests.Session()
            admin_client.close()
            from pymilvus import MilvusClient

            client = MilvusClient(uri=MILVUS_URI)
            if database_name in client.list_databases():
                db_client = MilvusClient(uri=MILVUS_URI, db_name=database_name)
                if db_client.has_collection(collection_name):
                    db_client.drop_collection(collection_name)
                client.drop_database(db_name=database_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("删除 Milvus 数据库失败: %s", exc)
            return jsonify(
                {
                    "success": False,
                    "deleted": False,
                    "error": f"删除 Milvus 数据库失败: {exc}",
                }
            ), 503

    if context_registry is not None:
        context_registry.remove(collection_name)

    for folder in [
        config_manager.get_user_knowledge_path(user_id, config_id),
        config_manager.get_user_data_path(user_id, config_id),
    ]:
        folder_path = Path(folder)
        if folder_path.exists():
            shutil.rmtree(folder_path, ignore_errors=True)
            deleted_folders.append(str(folder_path))

    del rag_configurations[config_id]
    save_rag_configurations()
    if get_user_active_config_id(user_id) == config_id:
        set_user_active_config_id(user_id, "default")

    return jsonify(
        {
            "success": True,
            "deleted": True,
            "message": f"知识库配置 '{deleted_config['name']}' 已删除",
            "deleted_folders": deleted_folders,
        }
    ), 200


@app.route("/get_rag_documents", methods=["GET"])
def get_rag_documents():
    user_id = request.args.get("user_id", "anonymous")
    config_id = request.args.get("config_id", "default")
    force_refresh = request.args.get("force_refresh", "false").lower() == "true"

    if config_id == "none":
        return jsonify(
            {
                "documents": [],
                "milvus_status": {"connected": False, "message": "当前为无知识库模式"},
                "documents_count": 0,
                "processing_count": 0,
                "processing_pages": 0,
                "total_pages_count": 0,
                "total_chunks": 0,
                "collection_name": config_manager.get_user_collection_name(user_id, config_id),
                "database_name": "none",
            }
        ), 200

    try:
        _, config, context = get_collection_context(user_id, config_id, force_refresh=force_refresh)
    except KeyError as exc:
        return jsonify({"error": str(exc), "documents": []}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc), "documents": []}), 503

    assert context is not None
    documents = context.refresh_documents(force_refresh=force_refresh)
    total_pages = sum(int(doc.get("total_pages", 0) or 0) for doc in documents if doc.get("processed"))
    total_chunks = sum(int(doc.get("chunks_count", 0) or 0) for doc in documents if doc.get("processed"))
    return jsonify(
        {
            "documents": documents,
            "documents_count": sum(1 for doc in documents if doc.get("processed")),
            "processing_count": sum(1 for doc in documents if doc.get("status") == "processing"),
            "processing_pages": sum(int(doc.get("processed_pages", 0) or 0) for doc in documents),
            "total_pages_count": total_pages,
            "total_chunks": total_chunks,
            "config": config,
            "collection_name": context.collection_name,
            "database_name": context.database_name,
            "milvus_status": {
                "connected": is_milvus_available(),
                "collection_name": context.collection_name,
                "database_name": context.database_name,
                "row_count": context.row_count(),
            },
        }
    ), 200


@app.route("/upload_rag_document", methods=["POST"])
def upload_rag_document():
    file = request.files.get("file")
    config_id = request.form.get("config_id", "default")
    user_id = request.form.get("user_id", "anonymous")
    max_upload_mb = int(os.environ.get("WEB_FABGPT_RAG_MAX_UPLOAD_MB", "50"))
    max_upload_bytes = max_upload_mb * 1024 * 1024
    if file is None or not file.filename:
        return jsonify({"error": "未上传文件"}), 400
    if config_id not in rag_configurations:
        return jsonify({"error": f'知识库配置 "{config_id}" 不存在'}), 404
    if config_manager.is_readonly_config(config_id):
        return jsonify({"error": f"配置 {config_id} 为只读，不允许上传文档"}), 403
    if config_id == "none":
        return jsonify({"error": "无知识库模式不允许上传文档"}), 403

    filename = secure_filename(file.filename)
    if not filename.lower().endswith(".pdf"):
        return jsonify({"error": "只支持PDF文件格式"}), 400
    if (request.content_length or 0) > max_upload_bytes:
        return jsonify({"error": f"文件过大，请上传小于 {max_upload_mb}MB 的 PDF"}), 413
    try:
        file.stream.seek(0, os.SEEK_END)
        upload_size = file.stream.tell()
        file.stream.seek(0)
    except (AttributeError, OSError):
        upload_size = request.content_length or 0
    if upload_size > max_upload_bytes:
        return jsonify({"error": f"文件过大，请上传小于 {max_upload_mb}MB 的 PDF"}), 413

    knowledge_dir = Path(config_manager.get_user_knowledge_path(user_id, config_id))
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    file_path = knowledge_dir / filename
    if file_path.exists():
        upload_bytes = file.read()
        file.seek(0)
        existing_hash = hashlib.md5(file_path.read_bytes()).hexdigest()
        upload_hash = hashlib.md5(upload_bytes).hexdigest()
        if existing_hash != upload_hash:
            timestamp = int(time.time())
            file_path = knowledge_dir / f"{file_path.stem}_{timestamp}{file_path.suffix}"

    file.save(file_path)
    task_payload = create_task_payload(
        file_path=str(file_path.resolve()),
        file_name=file_path.name,
        original_name=file.filename,
        config_id=config_id,
        user_id=user_id,
    )

    try:
        import fitz

        with fitz.open(str(file_path)) as document:
            task_payload["total_pages"] = len(document)
    except Exception:  # noqa: BLE001
        pass

    task_id = task_payload["task_id"]
    task_store.associate(task_id, str(user_id))
    task_store.upsert_task(task_id, task_payload)

    upload_executor.submit(
        background_process_upload,
        task_id,
        user_id=user_id,
        config_id=config_id,
        file_path=str(file_path.resolve()),
    )

    return jsonify(
        {
            "message": f"文件 '{file.filename}' 上传成功，正在后台处理",
            "filename": file_path.name,
            "original_name": file.filename,
            "status": "processing",
            "task_id": task_id,
            "doc_id": task_id,
            "tracking_id": task_id,
            "config_id": config_id,
            "total_pages": task_payload["total_pages"],
        }
    ), 200


@app.route("/check_processing_progress", methods=["GET"])
def check_processing_progress():
    task_id = request.args.get("task_id") or request.args.get("doc_id") or request.args.get("tracking_id")
    user_id = request.args.get("user_id", "anonymous")
    if not task_id:
        return jsonify({"status": "error", "error": "未提供有效的任务ID", "progress": 0}), 400

    task = task_store.get_task(str(task_id))
    if task is None:
        return jsonify({"status": "not_found", "progress": 0, "task_id": task_id}), 404

    owner = task_store.task_users.get(str(task_id))
    if owner and owner != str(user_id):
        return jsonify({"status": "unauthorized", "error": "没有权限访问此任务", "progress": 0}), 403

    return jsonify(task), 200


@app.route("/delete_rag_document", methods=["POST"])
def delete_rag_document():
    data = request.get_json(force=True) or {}
    user_id = data.get("user_id", "anonymous")
    config_id = data.get("config_id", "default")
    doc_id = str(data.get("doc_id", ""))
    physical_delete = bool(data.get("physical_delete", True))

    if not doc_id:
        return jsonify({"error": "未提供文档ID"}), 400
    if config_manager.is_readonly_config(config_id):
        return jsonify({"error": f"配置 {config_id} 为只读，不允许删除文档"}), 403

    try:
        _, _, context = get_collection_context(user_id, config_id, force_refresh=False)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 503

    assert context is not None
    try:
        result = context.delete_document(doc_id, physical_delete=physical_delete)
    except KeyError:
        return jsonify({"error": "文档不存在"}), 404

    return jsonify({"success": True, "deleted": True, **result}), 200


@app.route("/get_relevant_context", methods=["POST"])
def get_relevant_context():
    data = request.get_json(force=True) or {}
    query = data.get("query", "")
    user_id = data.get("user_id", "anonymous")
    config_id = data.get("config_id", "default")
    top_k = max(3, min(10, int(data.get("top_k", 5) or 5)))
    if not query:
        return jsonify({"error": "查询内容不能为空"}), 400

    try:
        config_id, knowledge_base_name, results, retrieval_queries = run_rag_search(
            query=query,
            user_id=user_id,
            config_id=config_id,
            top_k=top_k,
        )
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except Exception as exc:  # noqa: BLE001
        logger.exception("RAG 检索失败")
        return jsonify({"error": str(exc)}), 500

    if not results:
        return jsonify(
            {
                "context": "在知识库中未找到与查询相关的内容。",
                "knowledge_base_name": knowledge_base_name,
                "config_id": config_id,
                "retrieval_queries": retrieval_queries,
                "results": [],
            }
        ), 200

    context_text = "以下是与查询相关的内容:\n\n" + "\n".join(
        f"页面内容:\n{item['text_content']}\n" for item in results
    )
    return jsonify(
        {
            "context": context_text,
            "knowledge_base_name": knowledge_base_name,
            "config_id": config_id,
            "retrieval_queries": retrieval_queries,
            "results": results,
        }
    ), 200


@app.route("/rag_query", methods=["POST"])
def rag_query():
    return get_relevant_context()


@app.route("/chatbot_rag_query", methods=["POST"])
def chatbot_rag_query():
    data = request.get_json(force=True) or {}
    query = data.get("message") or data.get("messages") or ""
    user_id = data.get("user_id", "anonymous")
    config_id = data.get("config_id", "default")
    top_k = max(3, min(10, int(data.get("top_k", 5) or 5)))
    if not query:
        return jsonify({"error": "查询内容不能为空"}), 400

    try:
        config_id, knowledge_base_name, results, retrieval_queries = run_rag_search(
            query=query,
            user_id=user_id,
            config_id=config_id,
            top_k=top_k,
        )
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    if not results:
        return jsonify(
            {
                "context": "在知识库中未找到与查询相关的内容。",
                "knowledge_base_name": knowledge_base_name,
                "config_id": config_id,
                "retrieval_queries": retrieval_queries,
                "results": [],
            }
        ), 200

    context_text = "以下是与查询相关的内容:\n\n" + "\n".join(
        f"页面内容:\n{item['text_content']}\n" for item in results
    )
    return jsonify(
        {
            "context": context_text,
            "knowledge_base_name": knowledge_base_name,
            "config_id": config_id,
            "retrieval_queries": retrieval_queries,
            "results": results,
        }
    ), 200


@app.route("/clear_milvus_collection", methods=["POST"])
def clear_milvus_collection():
    data = request.get_json(force=True) or {}
    user_id = data.get("user_id", "anonymous")
    config_id = data.get("config_id", "default")
    if config_manager.is_readonly_config(config_id):
        return jsonify({"error": f"配置 {config_id} 为只读，不允许清空"}), 403
    try:
        _, _, context = get_collection_context(user_id, config_id, force_refresh=False)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 503
    assert context is not None
    context.clear_collection()
    return jsonify({"success": True, "message": f"已清空集合 {context.collection_name}"}), 200


@app.route("/get_user_tasks", methods=["GET"])
def get_user_tasks():
    user_id = request.args.get("user_id", "anonymous")
    config_id = request.args.get("config_id")
    tasks = task_store.list_user_tasks(user_id)
    if config_id:
        tasks = [task for task in tasks if str(task.get("config_id", "")) == str(config_id)]
    return jsonify({"user_id": user_id, "tasks": tasks, "count": len(tasks)}), 200


@app.route("/get_processing_tasks", methods=["GET"])
def get_processing_tasks():
    user_id = request.args.get("user_id", "anonymous")
    tasks = [task for task in task_store.list_user_tasks(user_id) if task.get("status") == "processing"]
    return jsonify({"tasks": tasks, "count": len(tasks)}), 200


@app.route("/get_task_by_filename", methods=["GET"])
def get_task_by_filename():
    filename = request.args.get("filename", "")
    user_id = request.args.get("user_id", "anonymous")
    if not filename:
        return jsonify({"error": "未提供文件名"}), 400
    task = task_store.find_task_by_filename(user_id=user_id, filename=filename)
    if task is None:
        return jsonify({"error": "未找到匹配的任务"}), 404
    return jsonify(task), 200


@app.route("/save_user_session_state", methods=["POST"])
def save_user_session_state():
    data = request.get_json(force=True) or {}
    session_key = f"{data.get('user_id', 'anonymous')}_{data.get('config_id', 'default')}"
    user_session_states[session_key] = {
        "documents": data.get("documents", []),
        "stats": data.get("stats", {}),
        "timestamp": time.time(),
    }
    return jsonify({"success": True, "message": "会话状态已保存"}), 200


@app.route("/get_user_session_state", methods=["GET"])
def get_user_session_state():
    user_id = request.args.get("user_id", "anonymous")
    config_id = request.args.get("config_id", "default")
    session_key = f"{user_id}_{config_id}"
    session_data = user_session_states.get(session_key)
    if session_data is None:
        return jsonify({"success": True, "has_state": False, "documents": [], "stats": {}}), 200
    if time.time() - session_data.get("timestamp", 0) > 1800:
        user_session_states.pop(session_key, None)
        return jsonify({"success": False, "has_state": False, "message": "会话状态已过期"}), 200
    return jsonify({"success": True, "has_state": True, **session_data}), 200


@app.route("/clear_user_session_state", methods=["POST"])
def clear_user_session_state():
    user_id = request.args.get("user_id", "anonymous")
    config_id = request.args.get("config_id", "default")
    user_session_states.pop(f"{user_id}_{config_id}", None)
    return jsonify({"success": True, "message": "会话状态已清除"}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify(
        {
            "status": "healthy",
            "config_file": CONFIG_FILE,
            "config_status": "loaded" if rag_configurations else "empty",
            "retriever_mode": "text_dense_rerank",
            "embedding_model_status": "loaded" if model_runtime and model_runtime.embedder else "not_loaded",
            "reranker_status": "loaded" if model_runtime and model_runtime.reranker_model else "not_loaded",
            "milvus_available": is_milvus_available(),
            "active_contexts": context_registry.count() if context_registry is not None else 0,
            "query_rewrite_enabled": bool(query_rewriter is not None and query_rewriter.cfg.enabled),
            "socket_timeout": SOCKET_TIMEOUT,
        }
    ), 200


def cleanup_resources() -> None:
    global model_runtime, context_registry, query_rewriter
    if context_registry is not None:
        context_registry.close_all()
    if model_runtime is not None:
        model_runtime.close()
    context_registry = None
    model_runtime = None
    query_rewriter = None
    user_session_states.clear()
    user_active_configurations.clear()
    upload_executor.shutdown(wait=False, cancel_futures=True)


def signal_handler(sig, frame) -> None:  # noqa: ARG001
    cleanup_resources()
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    initialize_knowledge_base_structure()
    task_store.initialize()
    task_store.load()
    task_store.recover_incomplete_tasks(failure_message="RAG 服务重启，任务已中断，请重新上传")
    initialize_rag()
    atexit.register(cleanup_resources)

    logger.info("=" * 50)
    logger.info("Text RAG Manager 服务已启动")
    logger.info("运行于 http://%s:%s", SERVICE_HOST, RAG_MANAGER_PORT)
    logger.info("=" * 50)

    app.run(debug=False, host=BIND_HOST, port=RAG_MANAGER_PORT, threaded=True)
