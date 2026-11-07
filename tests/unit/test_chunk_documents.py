import json
import tempfile
import unittest
from pathlib import Path

import yaml

from src.ingestion.chunk_documents import (
    chunk_pages,
    create_chunks,
)
from src.ingestion.scrape_documents import APPROVED_SOURCES, ScrapeError


def page_record(source_id: str, content: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "url": APPROVED_SOURCES[source_id],
        "title": f"Title for {source_id}",
        "content": content,
        "source_updated_at": None,
        "status": "success",
    }


class ChunkPagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.expected = dict(APPROVED_SOURCES)

    def test_chunks_preserve_source_and_line_metadata(self) -> None:
        pages = [
            page_record(source_id, "Fund facts\nExpense ratio: 0.5%\nExit load: 1%")
            for source_id in self.expected
        ]
        chunks = chunk_pages(pages, self.expected, max_chars=100)

        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertLessEqual(len(chunk["text"]), 100)
            self.assertIn(chunk["source_id"], self.expected)
            self.assertEqual(chunk["url"], self.expected[chunk["source_id"]])
            self.assertGreaterEqual(chunk["line_start"], 1)
            self.assertGreaterEqual(chunk["line_end"], chunk["line_start"])
            self.assertTrue(chunk["text"].startswith(chunk["title"]))
            self.assertTrue(chunk["chunk_id"])

    def test_same_input_produces_stable_chunk_ids(self) -> None:
        pages = [
            page_record(source_id, "Fund facts\nExpense ratio: 0.5%")
            for source_id in self.expected
        ]

        first = chunk_pages(pages, self.expected)
        second = chunk_pages(pages, self.expected)

        self.assertEqual(
            [chunk["chunk_id"] for chunk in first],
            [chunk["chunk_id"] for chunk in second],
        )

    def test_changed_text_changes_chunk_content_hash_and_id(self) -> None:
        first_pages = [
            page_record(source_id, "Expense ratio: 0.5%")
            for source_id in self.expected
        ]
        changed_pages = [
            page_record(
                source_id,
                "Expense ratio: 0.6%" if source_id == "hdfc-large-cap" else "Expense ratio: 0.5%",
            )
            for source_id in self.expected
        ]

        first = chunk_pages(first_pages, self.expected)
        changed = chunk_pages(changed_pages, self.expected)
        original_chunk = next(c for c in first if c["source_id"] == "hdfc-large-cap")
        changed_chunk = next(c for c in changed if c["source_id"] == "hdfc-large-cap")
        self.assertNotEqual(original_chunk["chunk_id"], changed_chunk["chunk_id"])
        self.assertNotEqual(original_chunk["content_hash"], changed_chunk["content_hash"])

    def test_rejects_missing_or_duplicate_source_records(self) -> None:
        pages = [page_record(source_id, "Some content") for source_id in self.expected]
        with self.assertRaises(ScrapeError):
            chunk_pages(pages[:-1], self.expected)
        with self.assertRaises(ScrapeError):
            chunk_pages(pages + [pages[0]], self.expected)

    def test_rejects_unapproved_or_mismatched_page_url(self) -> None:
        pages = [page_record(source_id, "Some content") for source_id in self.expected]
        pages[0]["url"] = "https://groww.in/mutual-funds/not-approved"
        with self.assertRaises(ScrapeError):
            chunk_pages(pages, self.expected)

    def test_splits_an_oversized_line_without_dropping_text(self) -> None:
        long_text = " ".join(f"fact{i}" for i in range(500))
        pages = [page_record(source_id, long_text) for source_id in self.expected]

        chunks = chunk_pages(pages, self.expected, max_chars=120)
        chunks_for_source = [c for c in chunks if c["source_id"] == "hdfc-large-cap"]
        combined = " ".join(chunk["text"].split("\n", 1)[1] for chunk in chunks_for_source)
        self.assertTrue(all(len(chunk["text"]) <= 120 for chunk in chunks_for_source))
        self.assertEqual(combined.split(), long_text.split())


class CreateChunksTests(unittest.TestCase):
    def test_writes_chunks_and_manifest_from_loaded_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loaded = root / "loaded"
            loaded.mkdir()
            chunks = root / "chunks"
            manifests = root / "manifests"
            config_path = root / "app.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "sources": [
                            {"id": source_id, "url": url}
                            for source_id, url in APPROVED_SOURCES.items()
                        ],
                        "artifacts": {
                            "loaded": str(loaded),
                            "chunks": str(chunks),
                            "manifests": str(manifests),
                        },
                    }
                ),
                encoding="utf-8",
            )
            (loaded / "pages.jsonl").write_text(
                "".join(
                    json.dumps(page_record(source_id, "Fund fact\nExpense ratio: 0.5%")) + "\n"
                    for source_id in APPROVED_SOURCES
                ),
                encoding="utf-8",
            )
            manifest = create_chunks(config_path)

            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["source_count"], 5)
            self.assertTrue((chunks / "chunks.jsonl").exists())
            self.assertTrue((manifests / "chunk-latest.json").exists())

    def test_partial_or_malformed_loaded_corpus_does_not_replace_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loaded = root / "loaded"
            loaded.mkdir()
            chunks_dir = root / "chunks"
            chunks_dir.mkdir()
            existing = chunks_dir / "chunks.jsonl"
            existing.write_text('{"previous":"good"}\n', encoding="utf-8")
            (loaded / "pages.jsonl").write_text('{"source_id":"broken"\n', encoding="utf-8")
            config_path = root / "app.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "sources": [
                            {"id": source_id, "url": url}
                            for source_id, url in APPROVED_SOURCES.items()
                        ],
                        "artifacts": {
                            "loaded": str(loaded),
                            "chunks": str(chunks_dir),
                            "manifests": str(root / "manifests"),
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ScrapeError):
                create_chunks(config_path)
            self.assertEqual(existing.read_text(encoding="utf-8"), '{"previous":"good"}\n')


if __name__ == "__main__":
    unittest.main()
