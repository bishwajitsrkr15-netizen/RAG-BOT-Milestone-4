"""Create traceable, bounded chunks from the loaded page corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ingestion.scrape_documents import ScrapeError, _atomic_write, load_sources


DEFAULT_MAX_CHARS = 1400


def _read_pages(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ScrapeError(f"Unable to read loaded page corpus {path}: {exc}") from exc

    pages: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            page = json.loads(line)
            if not isinstance(page, dict):
                raise ScrapeError(f"Page record on line {line_number} is not an object")
            pages.append(page)
    except json.JSONDecodeError as exc:
        raise ScrapeError(f"Invalid JSON in loaded page corpus: {exc}") from exc
    return pages


def _split_long_line(line: str, max_chars: int) -> list[str]:
    if len(line) <= max_chars:
        return [line]

    sentences = re.split(r"(?<=[.!?])\s+", line)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        remaining = sentence
        while len(remaining) > max_chars:
            split_at = remaining.rfind(" ", 0, max_chars + 1)
            if split_at <= 0:
                split_at = max_chars
            piece = remaining[:split_at].strip()
            if piece:
                if current:
                    parts.append(current)
                    current = ""
                parts.append(piece)
            remaining = remaining[split_at:].strip()
        if not remaining:
            continue
        candidate = f"{current} {remaining}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = remaining
    if current:
        parts.append(current)
    return parts


def _group_lines(content: str, max_chars: int) -> list[tuple[str, int, int]]:
    source_lines = [line.strip() for line in content.splitlines() if line.strip()]
    groups: list[tuple[str, int, int]] = []
    current_lines: list[str] = []
    current_length = 0
    start_line = 0
    end_line = 0

    for line_number, source_line in enumerate(source_lines, start=1):
        pieces = _split_long_line(source_line, max_chars)
        for piece in pieces:
            if current_lines and current_length + len(piece) + 1 > max_chars:
                groups.append(("\n".join(current_lines), start_line, end_line))
                current_lines = []
                current_length = 0
            if not current_lines:
                start_line = line_number
            current_lines.append(piece)
            current_length += len(piece) + (1 if len(current_lines) > 1 else 0)
            end_line = line_number

    if current_lines:
        groups.append(("\n".join(current_lines), start_line, end_line))
    return groups


def chunk_pages(
    pages: list[dict[str, Any]],
    expected_sources: dict[str, str],
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[dict[str, Any]]:
    if max_chars < 100:
        raise ScrapeError("max_chars must be at least 100")

    by_id: dict[str, dict[str, Any]] = {}
    for page in pages:
        source_id = page.get("source_id")
        if not isinstance(source_id, str) or source_id not in expected_sources:
            raise ScrapeError(f"Loaded page has an unapproved source ID: {source_id}")
        if source_id in by_id:
            raise ScrapeError(f"Duplicate loaded page record: {source_id}")
        if page.get("url") != expected_sources[source_id]:
            raise ScrapeError(f"Loaded page URL does not match config: {source_id}")
        if page.get("status") != "success":
            raise ScrapeError(f"Loaded page is not successful: {source_id}")
        if not isinstance(page.get("content"), str) or not page["content"].strip():
            raise ScrapeError(f"Loaded page has no content: {source_id}")
        by_id[source_id] = page

    missing = sorted(set(expected_sources) - set(by_id))
    if missing:
        raise ScrapeError(f"Loaded corpus is missing configured sources: {missing}")

    chunks: list[dict[str, Any]] = []
    for source_id in expected_sources:
        page = by_id[source_id]
        title = str(page.get("title") or source_id).strip()
        content_limit = max_chars - len(title) - 1
        if content_limit < 1:
            raise ScrapeError(f"Page title exceeds the maximum chunk size: {source_id}")
        for position, (body, line_start, line_end) in enumerate(
            _group_lines(page["content"], content_limit),
            start=0,
        ):
            text = f"{title}\n{body}"
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            chunk_id = hashlib.sha256(
                f"{source_id}:{content_hash}".encode("utf-8")
            ).hexdigest()
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "source_id": source_id,
                    "url": page["url"],
                    "title": title,
                    "section": None,
                    "position": position,
                    "line_start": line_start,
                    "line_end": line_end,
                    "text": text,
                    "content_hash": content_hash,
                    "source_updated_at": page.get("source_updated_at"),
                }
            )
    return chunks


def create_chunks(config_path: Path, max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
    config, sources = load_sources(config_path)
    artifacts = config.get("artifacts", {})
    loaded_path = Path(artifacts.get("loaded", "data/loaded")) / "pages.jsonl"
    chunks_dir = Path(artifacts.get("chunks", "data/chunks"))
    manifest_dir = Path(artifacts.get("manifests", "data/manifests"))
    expected_sources = {source.source_id: source.url for source in sources}
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        pages = _read_pages(loaded_path)
        chunks = chunk_pages(pages, expected_sources, max_chars=max_chars)
    except ScrapeError as exc:
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "mode": "chunk_loaded_documents",
            "input": str(loaded_path),
            "status": "failed",
            "error": str(exc),
        }
        _atomic_write(
            manifest_dir / "chunk-latest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        raise

    jsonl = "".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks)
    _atomic_write(chunks_dir / "chunks.jsonl", jsonl.encode("utf-8"))
    manifest = {
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "mode": "chunk_loaded_documents",
        "input": str(loaded_path),
        "output": str(chunks_dir / "chunks.jsonl"),
        "max_chars": max_chars,
        "source_count": len(expected_sources),
        "chunk_count": len(chunks),
        "chunks_by_source": {
            source_id: sum(1 for chunk in chunks if chunk["source_id"] == source_id)
            for source_id in expected_sources
        },
        "status": "success",
    }
    manifest_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        manifest_dir / "chunk-latest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/app.yaml"))
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    args = parser.parse_args(argv)
    try:
        manifest = create_chunks(args.config, max_chars=args.max_chars)
    except ScrapeError as exc:
        print(f"Chunking failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
