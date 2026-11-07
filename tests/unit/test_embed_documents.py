import json
import tempfile
import unittest
from pathlib import Path

import yaml

from src.ingestion.embed_documents import (
    create_embeddings,
    embed_chunks,
)
from src.ingestion.scrape_documents import ScrapeError


class FakeEncoder:
    def __init__(self, model_name: str, dimensions: int = 3) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    def encode(
        self,
        sentences: list[str],
        *,
        batch_size: int,
        convert_to_numpy: bool,
        normalize_embeddings: bool,
        show_progress_bar: bool,
    ) -> list[list[float]]:
        self.calls.append(sentences)
        return [
            [float(len(text)), 1.0, 0.5][: self.dimensions]
            for text in sentences
        ]


def chunks_fixture(count: int = 5) -> list[dict[str, str]]:
    return [
        {
            "chunk_id": f"chunk-{index}",
            "source_id": "source",
            "url": "https://groww.in/mutual-funds/example",
            "text": f"source title\nFact {index}",
        }
        for index in range(count)
    ]


class EmbedChunksTests(unittest.TestCase):
    def test_batches_chunks_and_preserves_id_alignment(self) -> None:
        encoder = FakeEncoder("test-model")
        output, dimensions = embed_chunks(
            chunks_fixture(),
            "test-model",
            batch_size=2,
            encoder_factory=lambda model: encoder,
        )

        self.assertEqual(dimensions, 3)
        self.assertEqual(len(encoder.calls), 3)
        self.assertEqual([row["chunk_id"] for row in output], [f"chunk-{i}" for i in range(5)])
        self.assertTrue(all(len(row["embedding"]) == 3 for row in output))

    def test_rejects_empty_corpus_and_invalid_batch_size(self) -> None:
        with self.assertRaises(ScrapeError):
            embed_chunks([], "test-model")
        with self.assertRaises(ScrapeError):
            embed_chunks(chunks_fixture(1), "test-model", batch_size=0)

    def test_rejects_non_finite_embedding_values(self) -> None:
        class InvalidEncoder(FakeEncoder):
            def encode(self, sentences: list[str], **kwargs: object) -> list[list[float]]:
                return [[float("nan"), 0.0, 1.0] for _ in sentences]

        with self.assertRaises(ScrapeError):
            embed_chunks(
                chunks_fixture(1),
                "test-model",
                encoder_factory=lambda model: InvalidEncoder(model),
            )

    def test_rejects_wrong_embedding_count(self) -> None:
        class ShortEncoder(FakeEncoder):
            def encode(self, sentences: list[str], **kwargs: object) -> list[list[float]]:
                return []

        with self.assertRaises(ScrapeError):
            embed_chunks(
                chunks_fixture(1),
                "test-model",
                encoder_factory=lambda model: ShortEncoder(model),
            )

    def test_rejects_inconsistent_vector_dimensions(self) -> None:
        class InconsistentEncoder(FakeEncoder):
            def encode(self, sentences: list[str], **kwargs: object) -> list[list[float]]:
                return [[1.0, 2.0], [1.0, 2.0, 3.0]][: len(sentences)]

        with self.assertRaises(ScrapeError):
            embed_chunks(
                chunks_fixture(2),
                "test-model",
                batch_size=2,
                encoder_factory=lambda model: InconsistentEncoder(model),
            )


class CreateEmbeddingsTests(unittest.TestCase):
    def _create_project(self, root: Path) -> Path:
        chunks_dir = root / "chunks"
        chunks_dir.mkdir()
        (chunks_dir / "chunks.jsonl").write_text(
            "".join(json.dumps(chunk) + "\n" for chunk in chunks_fixture()),
            encoding="utf-8",
        )
        config_path = root / "app.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "artifacts": {
                        "chunks": str(chunks_dir),
                        "embeddings": str(root / "embeddings"),
                        "manifests": str(root / "manifests"),
                    },
                    "embedding": {"model": "configured-test-model"},
                }
            ),
            encoding="utf-8",
        )
        return config_path

    def test_writes_vectors_and_model_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = self._create_project(root)
            created: list[str] = []

            def encoder_factory(model_name: str) -> FakeEncoder:
                created.append(model_name)
                return FakeEncoder(model_name)

            manifest = create_embeddings(
                config_path,
                batch_size=2,
                encoder_factory=encoder_factory,
            )

            vector_lines = (root / "embeddings" / "vectors.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            vectors = [json.loads(line) for line in vector_lines]
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["chunk_count"], 5)
            self.assertEqual(manifest["vector_count"], 5)
            self.assertEqual(manifest["dimensions"], 3)
            self.assertEqual(created, ["configured-test-model"])
            self.assertEqual([row["chunk_id"] for row in vectors], [f"chunk-{i}" for i in range(5)])
            self.assertTrue((root / "manifests" / "embedding-latest.json").exists())

    def test_failed_embedding_preserves_previous_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = self._create_project(root)
            output_dir = root / "embeddings"
            output_dir.mkdir()
            output_path = output_dir / "vectors.jsonl"
            output_path.write_text('{"previous":"good"}\n', encoding="utf-8")

            def fail_factory(model_name: str) -> FakeEncoder:
                raise RuntimeError("model unavailable")

            with self.assertRaises(ScrapeError):
                create_embeddings(config_path, encoder_factory=fail_factory)
            self.assertEqual(output_path.read_text(encoding="utf-8"), '{"previous":"good"}\n')


if __name__ == "__main__":
    unittest.main()
