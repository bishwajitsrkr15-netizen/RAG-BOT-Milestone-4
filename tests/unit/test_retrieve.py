import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

from src.ingestion.scrape_documents import ScrapeError
from src.retrieval.retrieve import (
    RetrievedChunk,
    identify_scheme,
    qdrant_index_status,
    retrieve,
)


class FakeEncoder:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def encode(self, sentences: list[str], **kwargs: Any) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in sentences]


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class FakeQdrant:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.points = [
            {
                "id": "point-1",
                "score": 0.8,
                "payload": {
                    "chunk_id": "large-cap-chunk",
                    "text": "HDFC Large Cap Fund\nExpense ratio is 0.5%.",
                    "source_id": "hdfc-large-cap",
                    "url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
                    "title": "HDFC Large Cap Fund",
                    "source_updated_at": None,
                },
            }
        ]

    def request(self, request: Any, timeout: int) -> FakeResponse:
        body = json.loads(request.data) if request.data else None
        self.requests.append(
            {
                "url": request.full_url,
                "method": request.method,
                "body": body,
                "api_key": request.get_header("Api-key"),
                "timeout": timeout,
            }
        )
        if request.method == "GET":
            return FakeResponse(
                {
                    "result": {
                        "points_count": len(self.points),
                        "config": {
                            "params": {
                                "vectors": {"size": 3, "distance": "Cosine"}
                            }
                        },
                    }
                }
            )
        return FakeResponse({"result": {"points": self.points}})


class RetrieveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        config_dir = root / "config"
        config_dir.mkdir()
        self.config_path = config_dir / "app.yaml"
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
                    "artifacts": {},
                    "embedding": {"model": "test-model"},
                    "vector_store": {
                        "provider": "qdrant",
                        "url_env": "QDRANT_URL",
                        "api_key_env": "QDRANT_API_KEY",
                        "collection_env": "QDRANT_COLLECTION",
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
        self.qdrant = FakeQdrant()
        self.settings = patch.dict(
            "os.environ",
            {
                "QDRANT_URL": "https://qdrant.example",
                "QDRANT_API_KEY": "test-api-key",
                "QDRANT_COLLECTION": "funds",
            },
        )
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def test_retrieves_relevant_chunks_with_scheme_filter(self) -> None:
        chunks = retrieve(
            "Expense ratio of HDFC Large Cap Fund?",
            self.config_path,
            encoder_factory=FakeEncoder,
            request_callable=self.qdrant.request,
        )

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].source_id, "hdfc-large-cap")
        self.assertEqual(chunks[0].chunk_id, "large-cap-chunk")
        self.assertIsInstance(chunks[0], RetrievedChunk)
        query = self.qdrant.requests[-1]
        self.assertEqual(query["method"], "POST")
        self.assertEqual(query["body"]["limit"], 4)
        self.assertEqual(
            query["body"]["filter"],
            {
                "must": [
                    {
                        "key": "source_id",
                        "match": {"value": "hdfc-large-cap"},
                    }
                ]
            },
        )
        self.assertEqual(query["api_key"], "test-api-key")

    def test_filters_chunks_beyond_cosine_distance_threshold(self) -> None:
        self.qdrant.points[0]["score"] = 0.1
        chunks = retrieve(
            "Expense ratio of HDFC Large Cap Fund?",
            self.config_path,
            encoder_factory=FakeEncoder,
            request_callable=self.qdrant.request,
        )
        self.assertEqual(chunks, [])

    def test_discards_vector_metadata_with_unapproved_citation_url(self) -> None:
        self.qdrant.points[0]["payload"]["url"] = (
            "https://groww.in/mutual-funds/not-approved"
        )
        chunks = retrieve(
            "Expense ratio of HDFC Large Cap Fund?",
            self.config_path,
            encoder_factory=FakeEncoder,
            request_callable=self.qdrant.request,
        )
        self.assertEqual(chunks, [])

    def test_rejects_query_embedding_with_wrong_dimension(self) -> None:
        class WrongDimensionEncoder(FakeEncoder):
            def encode(self, sentences: list[str], **kwargs: Any) -> list[list[float]]:
                return [[1.0, 0.0] for _ in sentences]

        with self.assertRaisesRegex(ScrapeError, "dimension"):
            retrieve(
                "Expense ratio?",
                self.config_path,
                encoder_factory=WrongDimensionEncoder,
                request_callable=self.qdrant.request,
            )

    def test_reports_qdrant_collection_status(self) -> None:
        self.assertEqual(
            qdrant_index_status(
                self.config_path,
                request_callable=self.qdrant.request,
            ),
            (2, 1),
        )

    def test_requires_qdrant_credentials(self) -> None:
        with patch.dict(
            "os.environ",
            {"QDRANT_URL": "", "QDRANT_API_KEY": "", "QDRANT_COLLECTION": ""},
        ):
            with self.assertRaisesRegex(ScrapeError, "QDRANT_URL"):
                retrieve(
                    "Expense ratio?",
                    self.config_path,
                    encoder_factory=FakeEncoder,
                    request_callable=self.qdrant.request,
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
