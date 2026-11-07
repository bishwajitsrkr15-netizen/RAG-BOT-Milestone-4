import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

from src.ingestion.store_vectors import (
    _collection_name,
    _validate_records,
    store_vectors,
)
from src.ingestion.scrape_documents import ScrapeError


class FakeCollection:
    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        self.metadata = metadata or {
            "hnsw:space": "cosine",
            "embedding_model": "test-model",
        }
        self.records: dict[str, dict[str, Any]] = {}

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        for chunk_id, embedding, document, metadata in zip(
            ids, embeddings, documents, metadatas
        ):
            self.records[chunk_id] = {
                "embedding": embedding,
                "document": document,
                "metadata": metadata,
            }

    def get(self, *, ids: list[str], include: list[str]) -> dict[str, Any]:
        existing = [chunk_id for chunk_id in ids if chunk_id in self.records]
        return {"ids": existing}

    def count(self) -> int:
        return len(self.records)


class FakeClient:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def get_or_create_collection(
        self,
        *,
        name: str,
        metadata: dict[str, Any],
    ) -> FakeCollection:
        if name not in self.collections:
            self.collections[name] = FakeCollection(metadata)
        return self.collections[name]


def records_fixture() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    chunks = [
        {
            "chunk_id": "chunk-a",
            "source_id": "fund",
            "url": "https://groww.in/mutual-funds/fund",
            "title": "Fund",
            "text": "Fund facts: expense ratio",
            "position": 0,
            "content_hash": "hash-a",
            "source_updated_at": None,
        },
        {
            "chunk_id": "chunk-b",
            "source_id": "fund",
            "url": "https://groww.in/mutual-funds/fund",
            "title": "Fund",
            "text": "Fund facts: exit load",
            "position": 1,
            "content_hash": "hash-b",
            "source_updated_at": None,
        },
    ]
    vectors = [
        {"chunk_id": "chunk-a", "embedding": [1.0, 0.0]},
        {"chunk_id": "chunk-b", "embedding": [0.0, 1.0]},
    ]
    manifest = {
        "status": "success",
        "model": "test-model",
        "normalized": True,
        "chunk_count": 2,
        "vector_count": 2,
        "dimensions": 2,
    }
    return chunks, vectors, manifest


class ValidateRecordsTests(unittest.TestCase):
    def test_aligns_vectors_to_chunk_ids_and_metadata(self) -> None:
        chunks, vectors, manifest = records_fixture()
        ids, embeddings, documents, metadata, dimension = _validate_records(
            chunks, vectors, manifest, "test-model", "cosine"
        )
        self.assertEqual(ids, ["chunk-a", "chunk-b"])
        self.assertEqual(embeddings, [[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(documents, [chunk["text"] for chunk in chunks])
        self.assertEqual(metadata[0]["url"], chunks[0]["url"])
        self.assertEqual(dimension, 2)

    def test_rejects_missing_duplicate_and_orphan_vectors(self) -> None:
        chunks, vectors, manifest = records_fixture()
        with self.assertRaises(ScrapeError):
            _validate_records(chunks, vectors[:1], manifest, "test-model", "cosine")
        with self.assertRaises(ScrapeError):
            _validate_records(
                chunks,
                vectors + [vectors[0]],
                {**manifest, "vector_count": 3},
                "test-model",
                "cosine",
            )
        with self.assertRaises(ScrapeError):
            _validate_records(
                chunks,
                vectors + [{"chunk_id": "orphan", "embedding": [1.0, 1.0]}],
                {**manifest, "vector_count": 3},
                "test-model",
                "cosine",
            )

    def test_rejects_bad_dimensions_nonfinite_values_and_wrong_model(self) -> None:
        chunks, vectors, manifest = records_fixture()
        with self.assertRaises(ScrapeError):
            _validate_records(chunks, vectors, manifest, "different-model", "cosine")
        bad_vectors = [vectors[0], {"chunk_id": "chunk-b", "embedding": [float("nan"), 1.0]}]
        with self.assertRaises(ScrapeError):
            _validate_records(chunks, bad_vectors, manifest, "test-model", "cosine")
        bad_vectors = [vectors[0], {"chunk_id": "chunk-b", "embedding": [1.0]}]
        with self.assertRaises(ScrapeError):
            _validate_records(chunks, bad_vectors, manifest, "test-model", "cosine")

    def test_collection_name_is_safe_and_versioned(self) -> None:
        name = _collection_name("hdfc-mutual-funds", "test-model", "a" * 64)
        self.assertLessEqual(len(name), 63)
        self.assertIn("a" * 12, name)
        self.assertNotEqual(
            name,
            _collection_name("hdfc-mutual-funds", "other-model", "a" * 64),
        )


class StoreVectorsTests(unittest.TestCase):
    def _create_project(self, root: Path) -> Path:
        chunks_dir = root / "chunks"
        embeddings_dir = root / "embeddings"
        manifests_dir = root / "manifests"
        chunks_dir.mkdir()
        embeddings_dir.mkdir()
        manifests_dir.mkdir()
        chunks, vectors, embedding_manifest = records_fixture()
        chunks_path = chunks_dir / "chunks.jsonl"
        chunks_path.write_text(
            "".join(json.dumps(record) + "\n" for record in chunks),
            encoding="utf-8",
        )
        (embeddings_dir / "vectors.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in vectors),
            encoding="utf-8",
        )
        embedding_manifest["input_sha256"] = hashlib.sha256(
            chunks_path.read_bytes()
        ).hexdigest()
        (manifests_dir / "embedding-latest.json").write_text(
            json.dumps(embedding_manifest), encoding="utf-8"
        )
        config_path = root / "app.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "artifacts": {
                        "chunks": str(chunks_dir),
                        "embeddings": str(embeddings_dir),
                        "manifests": str(manifests_dir),
                    },
                    "embedding": {"model": "test-model"},
                    "vector_store": {
                        "provider": "chromadb",
                        "path": str(root / "chroma"),
                        "collection": "test-funds",
                        "distance_metric": "cosine",
                    },
                }
            ),
            encoding="utf-8",
        )
        return config_path

    def test_persists_and_verifies_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = FakeClient()
            manifest = store_vectors(
                self._create_project(root),
                client_factory=lambda path: client,
            )
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["stored_count"], 2)
            self.assertEqual(manifest["vector_count"], 2)
            self.assertEqual(len(client.collections), 1)
            self.assertTrue((root / "manifests" / "vector-store-latest.json").exists())
            self.assertTrue((root / "manifests" / "vector-store-current.json").exists())

    def test_repeated_store_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = FakeClient()
            config_path = self._create_project(root)
            first = store_vectors(config_path, client_factory=lambda path: client)
            second = store_vectors(config_path, client_factory=lambda path: client)
            self.assertEqual(first["collection"], second["collection"])
            self.assertEqual(len(client.collections), 1)
            self.assertEqual(next(iter(client.collections.values())).count(), 2)

    def test_bad_input_preserves_previous_successful_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = self._create_project(root)
            manifest_path = root / "manifests" / "vector-store-latest.json"
            manifest_path.write_text('{"previous":"good"}', encoding="utf-8")
            current_path = root / "manifests" / "vector-store-current.json"
            current_path.write_text('{"last_success":"good"}', encoding="utf-8")
            (root / "embeddings" / "vectors.jsonl").write_text("", encoding="utf-8")
            with self.assertRaises(ScrapeError):
                store_vectors(config_path, client_factory=lambda path: FakeClient())
            failed = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(
                json.loads(current_path.read_text(encoding="utf-8")),
                {"last_success": "good"},
            )


if __name__ == "__main__":
    unittest.main()
