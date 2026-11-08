"""Upload the verified local chunk embeddings to Qdrant Cloud."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[1]
VECTOR_DIMENSIONS = 384
DEFAULT_COLLECTION = "hdfc-mutual-funds"


class MigrationError(Exception):
    """Raised when the local corpus or Qdrant configuration is invalid."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"Unable to read required JSON file: {path.name}") from exc
    if not isinstance(value, dict):
        raise MigrationError(f"Expected a JSON object in {path.name}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise MigrationError(f"Unable to read required data file: {path.name}") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MigrationError(
                f"Invalid JSON in {path.name} at line {line_number}"
            ) from exc
        if not isinstance(record, dict):
            raise MigrationError(
                f"Expected an object in {path.name} at line {line_number}"
            )
        records.append(record)
    return records


def _request(
    url: str,
    api_key: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "api-key": api_key,
            "Content-Type": "application/json",
            "User-Agent": "MutualFundFactsIndexMigration/1.0",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            response_body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return 404, {}
        raise MigrationError(f"Qdrant request failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MigrationError("Unable to reach Qdrant Cloud") from exc
    try:
        value = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise MigrationError("Qdrant returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise MigrationError("Qdrant returned an unexpected response")
    if status >= 400 or value.get("status") == "error":
        raise MigrationError("Qdrant rejected the migration request")
    return status, value


def _load_config(path: Path) -> dict[str, Any]:
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MigrationError("Unable to read application configuration") from exc
    if not isinstance(config, dict) or not isinstance(config.get("sources"), list):
        raise MigrationError("Application configuration must contain approved sources")
    return config


def _verified_local_records(
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    artifacts = config.get("artifacts", {})
    if not isinstance(artifacts, dict):
        raise MigrationError("Artifact paths are invalid")
    chunks_path = ROOT / str(artifacts.get("chunks", "data/chunks")) / "chunks.jsonl"
    vectors_path = (
        ROOT / str(artifacts.get("embeddings", "data/embeddings")) / "vectors.jsonl"
    )
    manifest_path = (
        ROOT
        / str(artifacts.get("manifests", "data/manifests"))
        / "vector-store-current.json"
    )
    embedding_manifest_path = (
        ROOT
        / str(artifacts.get("manifests", "data/manifests"))
        / "embedding-latest.json"
    )
    store_manifest = _read_json(manifest_path)
    embedding_manifest = _read_json(embedding_manifest_path)
    if store_manifest.get("status") != "success":
        raise MigrationError("The local vector-store manifest is not successful")
    if embedding_manifest.get("status") != "success":
        raise MigrationError("The local embedding manifest is not successful")
    if store_manifest.get("dimensions") != VECTOR_DIMENSIONS:
        raise MigrationError("The local index does not use 384-dimensional vectors")
    if store_manifest.get("embedding_model") != "sentence-transformers/all-MiniLM-L6-v2":
        raise MigrationError("The local index uses an unexpected embedding model")

    try:
        chunks_bytes = chunks_path.read_bytes()
    except OSError as exc:
        raise MigrationError("Unable to read the local chunk file") from exc
    if hashlib.sha256(chunks_bytes).hexdigest() != store_manifest.get("chunks_sha256"):
        raise MigrationError("Local chunks do not match the verified vector-store manifest")

    chunks = _read_jsonl(chunks_path)
    vectors = _read_jsonl(vectors_path)
    expected_count = store_manifest.get("stored_count")
    if not isinstance(expected_count, int) or len(chunks) != expected_count:
        raise MigrationError("Local chunk count does not match the verified manifest")
    if len(vectors) != expected_count:
        raise MigrationError("Local vector count does not match the verified manifest")

    approved_sources = {
        source["id"]: source["url"]
        for source in config["sources"]
        if isinstance(source, dict)
        and isinstance(source.get("id"), str)
        and isinstance(source.get("url"), str)
    }
    chunk_by_id = {chunk.get("chunk_id"): chunk for chunk in chunks}
    vector_by_id = {vector.get("chunk_id"): vector for vector in vectors}
    if (
        len(chunk_by_id) != expected_count
        or len(vector_by_id) != expected_count
        or chunk_by_id.keys() != vector_by_id.keys()
    ):
        raise MigrationError("Chunk IDs and vector IDs do not match exactly")

    points: list[dict[str, Any]] = []
    for chunk_id, chunk in chunk_by_id.items():
        vector_record = vector_by_id[chunk_id]
        source_id = chunk.get("source_id")
        url = chunk.get("url")
        text = chunk.get("text")
        embedding = vector_record.get("embedding")
        if (
            not isinstance(chunk_id, str)
            or not isinstance(source_id, str)
            or approved_sources.get(source_id) != url
            or not isinstance(text, str)
            or not isinstance(embedding, list)
            or len(embedding) != VECTOR_DIMENSIONS
        ):
            raise MigrationError("Local corpus contains invalid source or vector data")
        try:
            vector = [float(value) for value in embedding]
        except (TypeError, ValueError) as exc:
            raise MigrationError("Local vector contains a non-numeric value") from exc
        if not all(math.isfinite(value) for value in vector):
            raise MigrationError("Local vector contains a non-finite value")
        norm = math.sqrt(sum(value * value for value in vector))
        if not math.isclose(norm, 1.0, rel_tol=0.01, abs_tol=0.01):
            raise MigrationError("Local vector is not normalized")

        payload: dict[str, Any] = {
            "chunk_id": chunk_id,
            "source_id": source_id,
            "url": url,
            "title": str(chunk.get("title", source_id)),
            "text": text,
        }
        for key in (
            "source_updated_at",
            "section",
            "position",
            "line_start",
            "line_end",
            "content_hash",
        ):
            if chunk.get(key) is not None:
                payload[key] = chunk[key]
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))
        points.append({"id": point_id, "vector": vector, "payload": payload})
    return points, chunks


def _ensure_collection(
    base_url: str,
    collection: str,
    api_key: str,
) -> None:
    url = f"{base_url}/collections/{collection}"
    status, payload = _request(url, api_key)
    if status == 404:
        _request(
            url,
            api_key,
            method="PUT",
            body={
                "vectors": {
                    "size": VECTOR_DIMENSIONS,
                    "distance": "Cosine",
                }
            },
        )
        print("Created Qdrant cosine collection.")
        return
    result = payload.get("result")
    config = result.get("config") if isinstance(result, dict) else None
    params = config.get("params") if isinstance(config, dict) else None
    vectors = params.get("vectors") if isinstance(params, dict) else None
    if (
        not isinstance(vectors, dict)
        or vectors.get("size") != VECTOR_DIMENSIONS
        or str(vectors.get("distance", "")).lower() != "cosine"
    ):
        raise MigrationError(
            "Existing Qdrant collection must use one 384-dimensional cosine vector"
        )


def _ensure_source_id_index(
    base_url: str,
    collection: str,
    api_key: str,
) -> None:
    _request(
        f"{base_url}/collections/{collection}/index",
        api_key,
        method="PUT",
        body={"field_name": "source_id", "field_schema": "keyword"},
    )


def migrate(collection_override: str | None, batch_size: int) -> int:
    base_url = os.environ.get("QDRANT_URL", "").strip().rstrip("/")
    parsed_url = urlsplit(base_url)
    is_local_http = parsed_url.scheme == "http" and parsed_url.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    api_key = os.environ.get("QDRANT_API_KEY", "").strip()
    collection = (
        collection_override
        or os.environ.get("QDRANT_COLLECTION", "").strip()
        or DEFAULT_COLLECTION
    )
    if (parsed_url.scheme != "https" and not is_local_http) or not api_key:
        raise MigrationError("Set QDRANT_URL and QDRANT_API_KEY before migrating")
    if not collection or "/" in collection:
        raise MigrationError("QDRANT_COLLECTION must be a collection name")

    config = _load_config(ROOT / "config" / "app.yaml")
    points, _ = _verified_local_records(config)
    _ensure_collection(base_url, collection, api_key)
    _ensure_source_id_index(base_url, collection, api_key)
    for start in range(0, len(points), batch_size):
        batch = points[start : start + batch_size]
        _request(
            f"{base_url}/collections/{collection}/points?wait=true",
            api_key,
            method="PUT",
            body={"points": batch},
        )
        print(f"Uploaded {min(start + len(batch), len(points))}/{len(points)} points.")
    return len(points)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", help="Override QDRANT_COLLECTION")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args(argv)
    if args.batch_size < 1 or args.batch_size > 100:
        parser.error("--batch-size must be between 1 and 100")
    try:
        count = migrate(args.collection, args.batch_size)
    except MigrationError as exc:
        print(f"Qdrant migration failed: {exc}", file=sys.stderr)
        return 1
    print(f"Migration complete: {count} verified chunks uploaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
