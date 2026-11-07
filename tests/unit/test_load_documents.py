import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from src.ingestion.load_documents import load_documents
from src.ingestion.scrape_documents import APPROVED_SOURCES


class LoadDocumentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.raw_dir = self.root / "raw"
        self.loaded_dir = self.root / "loaded"
        self.manifest_dir = self.root / "manifests"
        self.raw_dir.mkdir()
        self.config_path = self.root / "app.yaml"
        self.config_path.write_text(
            yaml.safe_dump(
                {
                    "sources": [
                        {"id": source_id, "url": url}
                        for source_id, url in APPROVED_SOURCES.items()
                    ],
                    "artifacts": {
                        "raw": str(self.raw_dir),
                        "loaded": str(self.loaded_dir),
                        "manifests": str(self.manifest_dir),
                    },
                }
            ),
            encoding="utf-8",
        )

    def _save_source(self, source_id: str, url: str, body: bytes | None = None) -> None:
        if body is None:
            body = (
                "<html><head><title>Scheme facts</title></head><body>"
                "<main><h1>HDFC Large Cap Fund</h1>"
                "<p>Expense ratio: 0.5%</p></main></body></html>"
            ).encode("utf-8")
        raw_hash = hashlib.sha256(body).hexdigest()
        (self.raw_dir / f"{source_id}.html").write_bytes(body)
        (self.raw_dir / f"{source_id}.json").write_text(
            json.dumps(
                {
                    "source_id": source_id,
                    "url": url,
                    "fetched_at": "2026-11-07T12:00:00+00:00",
                    "content_type": "text/html; charset=utf-8",
                    "raw_sha256": raw_hash,
                }
            ),
            encoding="utf-8",
        )

    def _save_all_sources(self) -> None:
        for source_id, url in APPROVED_SOURCES.items():
            self._save_source(source_id, url)

    def test_loads_saved_documents_and_saves_traceable_records(self) -> None:
        self._save_all_sources()
        with patch("src.ingestion.scrape_documents.build_opener") as network:
            manifest = load_documents(self.config_path)

        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["loaded_sources"], 5)
        network.assert_not_called()
        records = [
            json.loads(line)
            for line in (self.loaded_dir / "pages.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        self.assertEqual(len(records), 5)
        self.assertIn("Expense ratio: 0.5%", records[0]["content"])
        self.assertEqual(records[0]["status"], "success")
        self.assertTrue(records[0]["content_hash"])
        self.assertTrue((self.manifest_dir / "load-latest.json").exists())

    def test_missing_raw_source_does_not_replace_previous_loaded_corpus(self) -> None:
        self._save_all_sources()
        pages_path = self.loaded_dir / "pages.jsonl"
        self.loaded_dir.mkdir(exist_ok=True)
        pages_path.write_text('{"previous":"known-good"}\n', encoding="utf-8")
        (self.raw_dir / "hdfc-large-cap.html").unlink()

        manifest = load_documents(self.config_path)

        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(pages_path.read_text(encoding="utf-8"), '{"previous":"known-good"}\n')
        self.assertEqual(manifest["failed_sources"][0]["source_id"], "hdfc-large-cap")

    def test_rejects_modified_raw_html_checksum(self) -> None:
        self._save_all_sources()
        (self.raw_dir / "hdfc-large-cap.html").write_text(
            "<html><body>tampered</body></html>",
            encoding="utf-8",
        )

        manifest = load_documents(self.config_path)

        self.assertEqual(manifest["status"], "partial")
        self.assertIn("checksum", manifest["failed_sources"][0]["error"])

    def test_rejects_raw_metadata_for_different_source(self) -> None:
        self._save_all_sources()
        metadata_path = self.raw_dir / "hdfc-large-cap.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["url"] = "https://groww.in/mutual-funds/not-approved"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        manifest = load_documents(self.config_path)

        self.assertEqual(manifest["status"], "partial")
        self.assertIn("does not match", manifest["failed_sources"][0]["error"])


if __name__ == "__main__":
    unittest.main()
