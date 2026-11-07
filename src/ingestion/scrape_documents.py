"""Fetch only the configured public pages and persist raw and extracted documents."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)

import yaml


MAX_RESPONSE_BYTES = 5 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "HDFCMutualFundFAQPrototype/0.1 (public-page ingestion)"

APPROVED_SOURCES = {
    "hdfc-mutual-fund-amc": "https://groww.in/mutual-funds/amc/hdfc-mutual-funds",
    "hdfc-large-cap": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
    "hdfc-flexi-cap": "https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth",
    "hdfc-elss-tax-saver": "https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth",
    "hdfc-mid-cap": "https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth",
}

SKIP_TAGS = {
    "head",
    "script",
    "style",
    "noscript",
    "svg",
    "nav",
    "header",
    "footer",
    "aside",
    "button",
}
BLOCK_TAGS = {
    "address",
    "article",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "main",
    "ol",
    "p",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}


class ScrapeError(Exception):
    """Raised when a configured page cannot be safely fetched or parsed."""


class ReadablePageParser(HTMLParser):
    """Extract readable text and page metadata without following any links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.metadata: dict[str, str] = {}
        self._tag_stack: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value for key, value in attrs}
        self._tag_stack.append(tag)
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = (
                attrs_dict.get("property")
                or attrs_dict.get("name")
                or attrs_dict.get("itemprop")
                or ""
            ).lower()
            value = attrs_dict.get("content")
            if key in {"article:modified_time", "dateModified".lower()} and value:
                self.metadata["source_updated_at"] = value.strip()
        if tag in BLOCK_TAGS and not self._skip_depth:
            self._append_break()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in BLOCK_TAGS and not self._skip_depth:
            self._append_break()

        for index in range(len(self._tag_stack) - 1, -1, -1):
            popped = self._tag_stack.pop()
            if popped in SKIP_TAGS:
                self._skip_depth = max(0, self._skip_depth - 1)
            if popped == tag:
                break

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        if not self._skip_depth:
            self.text_parts.append(text)

    def _append_break(self) -> None:
        if self.text_parts and self.text_parts[-1] != "\n":
            self.text_parts.append("\n")

    def result(self) -> tuple[str, str, dict[str, str]]:
        title = " ".join(self.title_parts).strip()
        content = "\n".join(
            line.strip()
            for line in "".join(self.text_parts).splitlines()
            if line.strip()
        )
        return title, content, self.metadata


@dataclass(frozen=True)
class Source:
    source_id: str
    url: str


class AllowlistedRedirectHandler(HTTPRedirectHandler):
    """Permit only same-page redirects on the expected Groww hosts."""

    def redirect_request(
        self,
        request: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        original = urlsplit(request.full_url)
        redirected = urlsplit(urljoin(request.full_url, newurl))
        allowed_hosts = {"groww.in", "www.groww.in"}
        if (
            redirected.scheme != "https"
            or redirected.hostname not in allowed_hosts
            or redirected.path != original.path
            or redirected.query
        ):
            raise ScrapeError("Rejected redirect outside the configured page")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def load_sources(config_path: Path) -> tuple[dict[str, Any], list[Source]]:
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ScrapeError(f"Unable to read config {config_path}: {exc}") from exc

    configured = config.get("sources") if isinstance(config, dict) else None
    if not isinstance(configured, list):
        raise ScrapeError("Config must contain a sources list")

    sources: list[Source] = []
    seen: set[str] = set()
    for item in configured:
        if not isinstance(item, dict):
            raise ScrapeError("Every source must have an id and URL")
        source_id = item.get("id")
        url = item.get("url")
        if not isinstance(source_id, str) or not isinstance(url, str):
            raise ScrapeError("Every source must have a string id and URL")
        if source_id in seen:
            raise ScrapeError(f"Duplicate source ID: {source_id}")
        if APPROVED_SOURCES.get(source_id) != url:
            raise ScrapeError(f"Source is not in the approved URL list: {source_id}")
        seen.add(source_id)
        sources.append(Source(source_id=source_id, url=url))

    if seen != set(APPROVED_SOURCES):
        missing = sorted(set(APPROVED_SOURCES) - seen)
        raise ScrapeError(f"Config must include all five approved sources; missing: {missing}")
    return config, sources


def _read_response(url: str) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    opener = build_opener(AllowlistedRedirectHandler())
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
            if final_url != url:
                original = urlsplit(url)
                final = urlsplit(final_url)
                if (
                    final.scheme != "https"
                    or final.hostname not in {"groww.in", "www.groww.in"}
                    or final.path != original.path
                    or final.query
                ):
                    raise ScrapeError("Rejected response URL outside the configured page")
            content_type = response.headers.get("Content-Type", "")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ScrapeError(f"Request failed: {exc}") from exc

    if len(body) > MAX_RESPONSE_BYTES:
        raise ScrapeError(f"Response exceeds {MAX_RESPONSE_BYTES} bytes")
    if not body:
        raise ScrapeError("Received an empty response")
    return body, content_type


def _decode_body(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    for part in content_type.split(";")[1:]:
        name, separator, value = part.strip().partition("=")
        if separator and name.lower() == "charset":
            charset = value.strip("\"' ")
            break
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_bytes(content)
    temporary_path.replace(path)


def scrape(config_path: Path) -> dict[str, Any]:
    config, sources = load_sources(config_path)
    artifacts = config.get("artifacts", {})
    raw_dir = Path(artifacts.get("raw", "data/raw"))
    manifest_dir = Path(artifacts.get("manifests", "data/manifests"))

    fetched_count = 0
    failures: list[dict[str, str]] = []
    run_started = datetime.now(timezone.utc).isoformat()

    for index, source in enumerate(sources):
        raw_path = raw_dir / f"{source.source_id}.html"
        metadata_path = raw_dir / f"{source.source_id}.json"
        try:
            raw_body, content_type = _read_response(source.url)
            fetched_at = datetime.now(timezone.utc).isoformat()
            raw_hash = hashlib.sha256(raw_body).hexdigest()
            _atomic_write(raw_path, raw_body)
            _atomic_write(
                metadata_path,
                json.dumps(
                    {
                        "source_id": source.source_id,
                        "url": source.url,
                        "fetched_at": fetched_at,
                        "content_type": content_type,
                        "raw_sha256": raw_hash,
                    },
                    ensure_ascii=False,
                    indent=2,
                ).encode("utf-8"),
            )
            fetched_count += 1
        except (ScrapeError, OSError) as exc:
            failures.append({"source_id": source.source_id, "error": str(exc)})
        if index < len(sources) - 1:
            time.sleep(1)

    manifest = {
        "started_at": run_started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "mode": "fetch_raw_documents",
        "expected_sources": len(sources),
        "fetched_sources": fetched_count,
        "failed_sources": failures,
        "status": "partial" if failures else "success",
    }
    manifest_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        manifest_dir / "scrape-latest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/app.yaml"))
    args = parser.parse_args(argv)
    try:
        manifest = scrape(args.config)
    except ScrapeError as exc:
        print(f"Scrape configuration error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
