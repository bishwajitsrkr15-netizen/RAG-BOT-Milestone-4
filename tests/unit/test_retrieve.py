import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

from src.ingestion.scrape_documents import ScrapeError
from src.retrieval.retrieve import RetrievedChunk, identify_scheme, retrieve


class FakeEncoder:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def encode(self, sentences: list[str], **kwargs: Any) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in sentences]


class FakeCollection:
    metadata = {"hnsw:space": "cosine", "embedding_model": "test-model"}

    def __init__(self) -> None:
        self.queries: list[dict[str, Any]] = []
        self.records = [
            {
                "id": "large-cap-chunk",
                "document": "HDFC Large Cap Fund\nExpense ratio is 0.5%.",
                "metadata": {
                    "source_id": "hdfc-large-cap",
                    "url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
                    "title": "HDFC Large Cap Fund",
                    "source_updated_at": None,
                },
                "distance": 0.2,
            }
        ]

    def count(self) -> int:
        return len(self.records)

    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
        where: dict[str, Any] | None = None,
        include: list[str],
    ) -> dict[str, Any]:
        self.queries.append(
            {"query_embeddings": query_embeddings, "n_results": n_results, "where": where}
        )
        rows = [
            row
            for row in self.records
            if where is None or all(row["metadata"].get(k) == v for k, v in where.items())
        ][:n_results]
        return {
            "ids": [[row["id"] for row in rows]],
            "documents": [[row["document"] for row in rows]],
            "metadatas": [[row["metadata"] for row in rows]],
            "distances": [[row["distance"] for row in rows]],
        }


class FakeClient:
    def __init__(self, collection: FakeCollection) -> None:
        self.collection = collection

    def get_collection(self, name: str) -> FakeCollection:
        return self.collection


class RetrieveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / "manifests").mkdir()
        self.collection = FakeCollection()
        self.config_path = root / "app.yaml"
        self.config_path.write_text(
            yaml.safe_dump(
                {
                    "sources": [
                        {
                            "id": "hdfc-mutual-fund-amc",
                            "url": "https://groww.in/mutual-funds/amc/hdfc-mutual-funds",
                        },
                        {
                            "id": "hdfc-large-cap",
                            "url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
                        },
                    ],
                    "artifacts": {"manifests": str(root / "manifests")},
                    "embedding": {"model": "test-model"},
                    "vector_store": {
                        "provider": "chromadb",
                        "path": str(root / "chroma"),
                        "collection": "funds",
                        "distance_metric": "cosine",
                    },
                    "retrieval": {"top_k": 4, "minimum_relevance": 0.45},
                    "scheme_aliases": {
                        "hdfc-large-cap": ["HDFC Large Cap", "Large Cap"]
                    },
                }
            ),
            encoding="utf-8",
        )
        (root / "manifests" / "vector-store-current.json").write_text(
            json.dumps(
                {
                    "status": "success",
                    "path": str(root / "chroma"),
                    "collection": "funds",
                    "collection_base": "funds",
                    "distance_metric": "cosine",
                    "embedding_model": "test-model",
                    "dimensions": 3,
                    "stored_count": 1,
                }
            ),
            encoding="utf-8",
        )

    def test_retrieves_relevant_chunks_with_scheme_filter(self) -> None:
        chunks = retrieve(
            "Expense ratio of HDFC Large Cap Fund?",
            self.config_path,
            encoder_factory=FakeEncoder,
            chroma_factory=lambda path: FakeClient(self.collection),
        )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].source_id, "hdfc-large-cap")
        self.assertEqual(self.collection.queries[0]["where"], {"source_id": "hdfc-large-cap"})
        self.assertIsInstance(chunks[0], RetrievedChunk)

    def test_filters_chunks_beyond_cosine_distance_threshold(self) -> None:
        self.collection.records[0]["distance"] = 0.8
        chunks = retrieve(
            "Expense ratio of HDFC Large Cap Fund?",
            self.config_path,
            encoder_factory=FakeEncoder,
            chroma_factory=lambda path: FakeClient(self.collection),
        )
        self.assertEqual(chunks, [])

    def test_discards_vector_metadata_with_unapproved_citation_url(self) -> None:
        self.collection.records[0]["metadata"]["url"] = (
            "https://groww.in/mutual-funds/not-approved"
        )
        chunks = retrieve(
            "Expense ratio of HDFC Large Cap Fund?",
            self.config_path,
            encoder_factory=FakeEncoder,
            chroma_factory=lambda path: FakeClient(self.collection),
        )
        self.assertEqual(chunks, [])

    def test_rejects_stale_or_missing_index(self) -> None:
        manifest_path = self.config_path.parent / "manifests" / "vector-store-current.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["embedding_model"] = "other-model"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ScrapeError):
            retrieve(
                "Expense ratio?",
                self.config_path,
                encoder_factory=FakeEncoder,
                chroma_factory=lambda path: FakeClient(self.collection),
            )

    def test_rejects_empty_question(self) -> None:
        with self.assertRaises(ScrapeError):
            retrieve("", self.config_path)

    def test_identifies_most_specific_configured_scheme_alias(self) -> None:
        self.assertEqual(
            identify_scheme(
                "What is the expense ratio of HDFC Large Cap Fund?",
                {"large": ["Large Cap"], "large-fund": ["HDFC Large Cap Fund"]},
            ),
            "large-fund",
        )


if __name__ == "__main__":
    unittest.main()
