"""Load saved raw source documents and persist extracted page records."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ingestion.scrape_documents import (
    MAX_RESPONSE_BYTES,
    ReadablePageParser,
    ScrapeError,
    _atomic_write,
    _decode_body,
    load_sources,
)


def _read_saved_document(
    source_id: str,
    source_url: str,
    raw_dir: Path,
) -> tuple[bytes, dict[str, Any]]:
    raw_path = raw_dir / f"{source_id}.html"
    metadata_path = raw_dir / f"{source_id}.json"
    try:
        raw_body = raw_path.read_bytes()
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScrapeError(f"Saved document is missing: {exc.filename}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ScrapeError(f"Unable to read saved document: {exc}") from exc

    if not isinstance(metadata, dict):
        raise ScrapeError("Saved document metadata must be a JSON object")
    if metadata.get("source_id") != source_id or metadata.get("url") != source_url:
        raise ScrapeError("Saved document metadata does not match the configured source")
    if not isinstance(metadata.get("fetched_at"), str) or not metadata["fetched_at"]:
        raise ScrapeError("Saved document has no valid fetch timestamp")
    if len(raw_body) > MAX_RESPONSE_BYTES:
        raise ScrapeError(f"Saved document exceeds {MAX_RESPONSE_BYTES} bytes")
    if not raw_body:
        raise ScrapeError("Saved document is empty")

    actual_hash = hashlib.sha256(raw_body).hexdigest()
    if metadata.get("raw_sha256") != actual_hash:
        raise ScrapeError("Saved document checksum does not match its metadata")
    return raw_body, metadata


def _extract_page(
    source_id: str,
    source_url: str,
    raw_body: bytes,
    raw_metadata: dict[str, Any],
) -> dict[str, Any]:
    html = _decode_body(raw_body, str(raw_metadata.get("content_type", "")))
    parser = ReadablePageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        raise ScrapeError(f"Unable to parse saved document: {exc}") from exc

    title, content, page_metadata = parser.result()
    if not content:
        raise ScrapeError("No readable page content was extracted")

    raw_hash = hashlib.sha256(raw_body).hexdigest()
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "source_id": source_id,
        "url": source_url,
        "title": title,
        "content": content,
        "fetched_at": raw_metadata["fetched_at"],
        "source_updated_at": page_metadata.get("source_updated_at"),
        "raw_sha256": raw_hash,
        "content_hash": content_hash,
        "status": "success",
    }


def load_documents(config_path: Path) -> dict[str, Any]:
    config, sources = load_sources(config_path)
    artifacts = config.get("artifacts", {})
    raw_dir = Path(artifacts.get("raw", "data/raw"))
    loaded_dir = Path(artifacts.get("loaded", "data/loaded"))
    manifest_dir = Path(artifacts.get("manifests", "data/manifests"))
    pages_path = loaded_dir / "pages.jsonl"

    started_at = datetime.now(timezone.utc).isoformat()
    records: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for source in sources:
        try:
            raw_body, raw_metadata = _read_saved_document(
                source.source_id,
                source.url,
                raw_dir,
            )
            records.append(
                _extract_page(source.source_id, source.url, raw_body, raw_metadata)
            )
        except ScrapeError as exc:
            failures.append({"source_id": source.source_id, "error": str(exc)})

    # Never publish a partial set as the current loaded corpus.
    if not failures:
        jsonl = "".join(
            json.dumps(record, ensure_ascii=False) + "\n"
            for record in records
        )
        _atomic_write(pages_path, jsonl.encode("utf-8"))

    manifest = {
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "mode": "load_saved_documents",
        "expected_sources": len(sources),
        "loaded_sources": len(records),
        "failed_sources": failures,
        "status": "partial" if failures else "success",
        "output": str(pages_path) if not failures else None,
    }
    manifest_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        manifest_dir / "load-latest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/app.yaml"))
    args = parser.parse_args(argv)
    try:
        manifest = load_documents(args.config)
    except ScrapeError as exc:
        print(f"Document loading configuration error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
