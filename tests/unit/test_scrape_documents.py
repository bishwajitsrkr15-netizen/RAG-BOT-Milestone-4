import tempfile
import unittest
from pathlib import Path
from urllib.request import Request
from unittest.mock import patch

import yaml

from src.ingestion.scrape_documents import (
    APPROVED_SOURCES,
    AllowlistedRedirectHandler,
    ReadablePageParser,
    ScrapeError,
    load_sources,
)


class ReadablePageParserTests(unittest.TestCase):
    def test_extracts_content_without_script_or_navigation(self) -> None:
        parser = ReadablePageParser()
        parser.feed(
            "<html><head><title>Fund facts</title></head><body>"
            "<nav>menu text</nav><main><h1>Large Cap</h1>"
            "<p>Expense ratio: 0.5%</p><script>hidden secret</script></main>"
            "</body></html>"
        )
        title, content, _ = parser.result()
        self.assertEqual(title, "Fund facts")
        self.assertIn("Expense ratio: 0.5%", content)
        self.assertNotIn("menu text", content)
        self.assertNotIn("hidden secret", content)

    def test_extracts_source_update_metadata(self) -> None:
        parser = ReadablePageParser()
        parser.feed(
            '<meta property="article:modified_time" content="2026-01-02">'
            "<p>Page text</p>"
        )
        _, _, metadata = parser.result()
        self.assertEqual(metadata["source_updated_at"], "2026-01-02")


class SourceAllowlistTests(unittest.TestCase):
    def test_config_must_contain_exactly_the_approved_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "app.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "sources": [
                            {"id": source_id, "url": url}
                            for source_id, url in APPROVED_SOURCES.items()
                        ]
                    }
                ),
                encoding="utf-8",
            )
            _, sources = load_sources(config_path)
        self.assertEqual(len(sources), 5)

    def test_config_rejects_unapproved_internal_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "app.yaml"
            sources = [
                {"id": source_id, "url": url}
                for source_id, url in APPROVED_SOURCES.items()
            ]
            sources[0]["url"] = "https://groww.in/mutual-funds/another-page"
            config_path.write_text(yaml.safe_dump({"sources": sources}), encoding="utf-8")
            with self.assertRaises(ScrapeError):
                load_sources(config_path)

    def test_redirect_policy_rejects_other_paths_and_hosts(self) -> None:
        handler = AllowlistedRedirectHandler()
        with self.assertRaises(ScrapeError):
            handler.redirect_request(
                request=Request(APPROVED_SOURCES["hdfc-large-cap"]),
                fp=None,
                code=302,
                msg="Found",
                headers={},
                newurl="https://groww.in/mutual-funds/another-page",
            )
        with self.assertRaises(ScrapeError):
            handler.redirect_request(
                request=Request(APPROVED_SOURCES["hdfc-large-cap"]),
                fp=None,
                code=302,
                msg="Found",
                headers={},
                newurl="https://example.com/mutual-funds/hdfc-large-cap-fund-direct-growth",
            )

    def test_scrape_fetches_only_allowlisted_pages(self) -> None:
        from src.ingestion.scrape_documents import scrape

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "app.yaml"
            config = {
                "sources": [
                    {"id": source_id, "url": url}
                    for source_id, url in APPROVED_SOURCES.items()
                ],
                "artifacts": {
                    "raw": str(root / "raw"),
                    "loaded": str(root / "loaded"),
                    "manifests": str(root / "manifests"),
                },
            }
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
            page = (
                '<html><head><title>Fund page</title></head><body><main>'
                '<p>Expense ratio: 0.5%</p>'
                '<a href="https://groww.in/mutual-funds/unapproved">other page</a>'
                "</main></body></html>"
            ).encode()
            with patch(
                "src.ingestion.scrape_documents._read_response",
                return_value=(page, "text/html; charset=utf-8"),
            ) as fetch, patch("src.ingestion.scrape_documents.time.sleep"):
                manifest = scrape(config_path)

            self.assertEqual(fetch.call_count, 5)
            self.assertEqual(
                {call.args[0] for call in fetch.call_args_list},
                set(APPROVED_SOURCES.values()),
            )
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(len(list((root / "raw").glob("*.html"))), 5)
            self.assertFalse((root / "loaded" / "pages.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
