from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence

from pymilvus import DataType, MilvusClient


logger = logging.getLogger("Text-RAG-Manager")

TEXT_VECTOR_DIM = 1024
TEXT_COLLECTION_FIELDS = {
    "pk",
    "vector",
    "doc_id",
    "chunk_id",
    "file_path",
    "filename",
    "text_content",
    "page_num_start",
    "page_num_end",
    "section_title",
    "section_path",
    "section_level",
    "chunk_order_in_section",
}


@dataclass(frozen=True)
class TextSearchHit:
    score: float
    doc_id: str
    chunk_id: str
    file_path: str
    filename: str
    text_content: str
    page_num_start: int
    page_num_end: int
    section_title: str
    section_path: str
    section_level: int
    chunk_order_in_section: int


class TextMilvusRetriever:
    def __init__(
        self,
        *,
        uri: str,
        db_name: str,
        collection_name: str,
        vector_dim: int = TEXT_VECTOR_DIM,
    ) -> None:
        self.uri = uri
        self.db_name = db_name
        self.collection_name = collection_name
        self.vector_dim = vector_dim
        self.client: MilvusClient | None = None

    def connect(self) -> None:
        admin_client = MilvusClient(uri=self.uri)
        database_names = admin_client.list_databases()
        if self.db_name not in database_names:
            admin_client.create_database(db_name=self.db_name)
        self.client = MilvusClient(uri=self.uri, db_name=self.db_name)

    def ensure_collection(self) -> None:
        if self.client is None:
            self.connect()
        assert self.client is not None

        if self.client.has_collection(self.collection_name):
            if not self._schema_matches():
                logger.warning("集合 %s schema 不匹配，重新创建", self.collection_name)
                self.client.drop_collection(self.collection_name)

        if not self.client.has_collection(self.collection_name):
            schema = self.client.create_schema(auto_id=True, enable_dynamic_fields=False)
            schema.add_field("pk", DataType.INT64, is_primary=True, auto_id=True)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.vector_dim)
            schema.add_field("doc_id", DataType.VARCHAR, max_length=128)
            schema.add_field("chunk_id", DataType.VARCHAR, max_length=160)
            schema.add_field("file_path", DataType.VARCHAR, max_length=2048)
            schema.add_field("filename", DataType.VARCHAR, max_length=512)
            schema.add_field("text_content", DataType.VARCHAR, max_length=65535)
            schema.add_field("page_num_start", DataType.INT64)
            schema.add_field("page_num_end", DataType.INT64)
            schema.add_field("section_title", DataType.VARCHAR, max_length=1024)
            schema.add_field("section_path", DataType.VARCHAR, max_length=4096)
            schema.add_field("section_level", DataType.INT64)
            schema.add_field("chunk_order_in_section", DataType.INT64)

            index_params = MilvusClient.prepare_index_params()
            index_params.add_index(
                field_name="vector",
                index_type="HNSW",
                metric_type="COSINE",
                params={"M": 16, "efConstruction": 200},
            )
            self.client.create_collection(
                collection_name=self.collection_name,
                schema=schema,
                index_params=index_params,
            )

        self.client.load_collection(self.collection_name)

    def upsert_records(self, records: Sequence[Dict], *, batch_size: int = 128) -> None:
        if not records:
            return
        self.ensure_collection()
        assert self.client is not None

        doc_ids = sorted({record["doc_id"] for record in records})
        for doc_id in doc_ids:
            self.delete_document(doc_id)

        for batch_start in range(0, len(records), batch_size):
            batch = list(records[batch_start: batch_start + batch_size])
            self.client.insert(self.collection_name, batch)

        self.client.flush(self.collection_name)

    def search(self, query_vector, *, limit: int = 20) -> List[TextSearchHit]:
        self.ensure_collection()
        assert self.client is not None

        raw_results = self.client.search(
            collection_name=self.collection_name,
            data=[query_vector.tolist() if hasattr(query_vector, "tolist") else query_vector],
            limit=limit,
            anns_field="vector",
            output_fields=[
                "doc_id",
                "chunk_id",
                "file_path",
                "filename",
                "text_content",
                "page_num_start",
                "page_num_end",
                "section_title",
                "section_path",
                "section_level",
                "chunk_order_in_section",
            ],
            search_params={"metric_type": "COSINE", "params": {"ef": 64}},
        )

        hits: List[TextSearchHit] = []
        for item in raw_results[0] if raw_results else []:
            entity = item.get("entity", item)
            hits.append(
                TextSearchHit(
                    score=float(item.get("distance", item.get("score", 0.0))),
                    doc_id=str(entity.get("doc_id", "")),
                    chunk_id=str(entity.get("chunk_id", "")),
                    file_path=str(entity.get("file_path", "")),
                    filename=str(entity.get("filename", "")),
                    text_content=str(entity.get("text_content", "")),
                    page_num_start=int(entity.get("page_num_start", 0) or 0),
                    page_num_end=int(entity.get("page_num_end", 0) or 0),
                    section_title=str(entity.get("section_title", "")),
                    section_path=str(entity.get("section_path", "")),
                    section_level=int(entity.get("section_level", 0) or 0),
                    chunk_order_in_section=int(entity.get("chunk_order_in_section", 0) or 0),
                )
            )
        return hits

    def query_all_chunks(self, *, limit: int = 20000) -> List[Dict]:
        self.ensure_collection()
        assert self.client is not None
        return self.client.query(
            collection_name=self.collection_name,
            filter="",
            output_fields=[
                "doc_id",
                "chunk_id",
                "file_path",
                "filename",
                "text_content",
                "page_num_start",
                "page_num_end",
                "section_title",
                "section_path",
                "section_level",
                "chunk_order_in_section",
            ],
            limit=limit,
        )

    def delete_document(self, doc_id: str) -> int:
        self.ensure_collection()
        assert self.client is not None
        result = self.client.delete(
            collection_name=self.collection_name,
            filter=f'doc_id == "{doc_id}"',
        )
        self.client.flush(self.collection_name)
        return int(result.get("delete_count", 0))

    def has_document(self, doc_id: str) -> bool:
        self.ensure_collection()
        assert self.client is not None
        results = self.client.query(
            collection_name=self.collection_name,
            filter=f'doc_id == "{doc_id}"',
            output_fields=["doc_id"],
            limit=1,
        )
        return bool(results)

    def clear_collection(self) -> None:
        self.ensure_collection()
        assert self.client is not None
        self.client.drop_collection(self.collection_name)
        self.ensure_collection()

    def collection_row_count(self) -> int:
        self.ensure_collection()
        assert self.client is not None
        stats = self.client.get_collection_stats(self.collection_name)
        return int(stats.get("row_count", 0) or 0)

    def close(self) -> None:
        self.client = None

    def _schema_matches(self) -> bool:
        assert self.client is not None
        description = self.client.describe_collection(self.collection_name)
        field_entries = description.get("fields", [])
        field_names = {
            (entry.get("name") or entry.get("field_name"))
            for entry in field_entries
            if isinstance(entry, dict)
        }
        if field_names != TEXT_COLLECTION_FIELDS:
            return False

        vector_entries = [
            entry
            for entry in field_entries
            if (entry.get("name") or entry.get("field_name")) == "vector"
        ]
        if not vector_entries:
            return False
        vector_entry = vector_entries[0]
        params = vector_entry.get("params") or vector_entry.get("type_params") or {}
        dim = params.get("dim") or vector_entry.get("dim")
        try:
            return int(dim) == self.vector_dim
        except (TypeError, ValueError):
            return False
