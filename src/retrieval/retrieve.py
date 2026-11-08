"""Retrieve relevant chunks from the configured Qdrant collection."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

import yaml

from src.ingestion.embed_documents import Encoder, _load_encoder
from src.ingestion.scrape_documents import ScrapeError
from src.settings import read_setting


QDRANT_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    text: str
    source_id: str
    url: str
    title: str
    distance: float
    source_updated_at: str | None
    metadata: dict[str, Any]


def identify_scheme(question: str, aliases: dict[str, list[str]]) -> str | None:
    normalized = question.casefold()
    matches: list[tuple[int, str]] = []
    for source_id, terms in aliases.items():
        if any(term.casefold() in normalized for term in terms):
            matches.append((max(map(len, terms)), source_id))
    if not matches:
        return None
    matches.sort(reverse=True)
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return None
    return matches[0][1]


def _read_runtime_config(config_path: Path) -> dict[str, Any]:
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ScrapeError(f"Unable to read config {config_path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ScrapeError("Configuration must be a YAML object")

    embedding = config.get("embedding")
    vector_store = config.get("vector_store")
    retrieval = config.get("retrieval")
    sources = config.get("sources")
    if (
        not all(
            isinstance(value, dict)
            for value in (embedding, vector_store, retrieval)
        )
        or not isinstance(sources, list)
    ):
        raise ScrapeError("Config must contain embedding, vector-store, and retrieval objects")
    source_urls: dict[str, str] = {}
    for source in sources:
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("id"), str)
            or not isinstance(source.get("url"), str)
        ):
            raise ScrapeError("Every configured source must have a string ID and URL")
        if source["id"] in source_urls:
            raise ScrapeError(f"Duplicate configured source ID: {source['id']}")
        source_urls[source["id"]] = source["url"]
    if not source_urls:
        raise ScrapeError("Config must contain at least one approved source")
    aliases = config.get("scheme_aliases", {})
    if not isinstance(aliases, dict) or any(
        not isinstance(source_id, str)
        or source_id not in source_urls
        or not isinstance(terms, list)
        or any(not isinstance(term, str) or not term.strip() for term in terms)
        for source_id, terms in aliases.items()
    ):
        raise ScrapeError("scheme_aliases must map configured source IDs to string lists")

    model_name = embedding.get("model")
    if not isinstance(model_name, str) or not model_name.strip():
        raise ScrapeError("Config must specify embedding.model")
    if vector_store.get("provider") != "qdrant":
        raise ScrapeError("Only the configured Qdrant vector-store provider is supported")
    if vector_store.get("distance_metric") != "cosine":
        raise ScrapeError("Retrieval currently supports only cosine distance")
    if (
        not isinstance(vector_store.get("url_env"), str)
        or not vector_store["url_env"]
        or not isinstance(vector_store.get("api_key_env"), str)
        or not vector_store["api_key_env"]
        or not isinstance(vector_store.get("collection"), str)
        or not vector_store["collection"]
        or (
            "collection_env" in vector_store
            and (
                not isinstance(vector_store["collection_env"], str)
                or not vector_store["collection_env"]
            )
        )
    ):
        raise ScrapeError("Qdrant URL, API key, and collection settings are required")
    top_k = retrieval.get("top_k")
    threshold = retrieval.get("minimum_relevance")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise ScrapeError("retrieval.top_k must be a positive integer")
    if (
        not isinstance(threshold, (int, float))
        or isinstance(threshold, bool)
        or not 0 <= threshold <= 2
    ):
        raise ScrapeError("retrieval.minimum_relevance must be a cosine distance in [0, 2]")
    return config


def _qdrant_settings(
    config: dict[str, Any],
    project_root: Path,
) -> tuple[str, str, str]:
    vector_config = config["vector_store"]
    try:
        base_url = read_setting(vector_config["url_env"], project_root)
        api_key = read_setting(vector_config["api_key_env"], project_root)
        collection_env = vector_config.get("collection_env", "QDRANT_COLLECTION")
        collection = (
            read_setting(collection_env, project_root)
            or vector_config["collection"]
        )
    except OSError as exc:
        raise ScrapeError(f"Unable to read Qdrant configuration: {exc}") from exc
    if not base_url or not api_key:
        raise ScrapeError("Set QDRANT_URL and QDRANT_API_KEY in app secrets or environment")
    parsed_url = urlsplit(base_url)
    is_local_http = parsed_url.scheme == "http" and parsed_url.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    if (parsed_url.scheme != "https" and not is_local_http) or not parsed_url.netloc:
        raise ScrapeError("QDRANT_URL must be HTTPS, except for local development")
    if not collection or "/" in collection:
        raise ScrapeError("QDRANT_COLLECTION must be a collection name")
    return base_url.rstrip("/"), api_key, collection


def _qdrant_request(
    base_url: str,
    collection: str,
    api_key: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    request_callable: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        f"{base_url}/collections/{quote(collection, safe='')}"
        + ("/points/query" if method == "POST" else ""),
        data=data,
        headers={
            "api-key": api_key,
            "Content-Type": "application/json",
            "User-Agent": "MutualFundFactsStreamlit/1.0",
        },
        method=method,
    )
    try:
        with request_callable(request, timeout=QDRANT_TIMEOUT_SECONDS) as response:
            response_body = response.read()
    except HTTPError as exc:
        raise ScrapeError(f"Qdrant request failed with HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ScrapeError("Unable to reach the configured Qdrant collection") from exc
    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise ScrapeError("Qdrant returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("status") == "error":
        raise ScrapeError("Qdrant returned an unexpected response")
    return payload


def _collection_info(
    config: dict[str, Any],
    project_root: Path,
    request_callable: Callable[..., Any] = urlopen,
) -> tuple[dict[str, Any], str]:
    base_url, api_key, collection = _qdrant_settings(config, project_root)
    response = _qdrant_request(
        base_url,
        collection,
        api_key,
        request_callable=request_callable,
    )
    result = response.get("result")
    collection_config = result.get("config") if isinstance(result, dict) else None
    params = (
        collection_config.get("params")
        if isinstance(collection_config, dict)
        else None
    )
    vectors = params.get("vectors") if isinstance(params, dict) else None
    if (
        not isinstance(result, dict)
        or not isinstance(vectors, dict)
        or not isinstance(vectors.get("size"), int)
        or isinstance(vectors.get("size"), bool)
        or str(vectors.get("distance", "")).lower() != "cosine"
    ):
        raise ScrapeError(
            "Qdrant collection must use one cosine vector with a fixed dimension"
        )
    return result, collection


def qdrant_index_status(
    config_path: Path = Path("config/app.yaml"),
    *,
    request_callable: Callable[..., Any] = urlopen,
) -> tuple[int, int]:
    """Return approved source count and verified Qdrant point count."""
    config = _read_runtime_config(config_path)
    result, _ = _collection_info(config, config_path.resolve().parent, request_callable)
    points_count = result.get("points_count")
    if (
        not isinstance(points_count, int)
        or isinstance(points_count, bool)
        or points_count < 0
    ):
        raise ScrapeError("Qdrant did not report a valid point count")
    return len(config["sources"]), points_count


def _query_qdrant(
    config: dict[str, Any],
    project_root: Path,
    vector: list[float],
    limit: int,
    source_id: str | None,
    request_callable: Callable[..., Any],
) -> list[dict[str, Any]]:
    base_url, api_key, collection = _qdrant_settings(config, project_root)
    body: dict[str, Any] = {
        "query": vector,
        "limit": limit,
        "with_payload": True,
    }
    if source_id:
        body["filter"] = {
            "must": [{"key": "source_id", "match": {"value": source_id}}]
        }
    response = _qdrant_request(
        base_url,
        collection,
        api_key,
        method="POST",
        body=body,
        request_callable=request_callable,
    )
    result = response.get("result")
    points = result.get("points") if isinstance(result, dict) else None
    if not isinstance(points, list):
        raise ScrapeError("Qdrant query returned an invalid point list")
    return [point for point in points if isinstance(point, dict)]


def retrieve(
    question: str,
    config_path: Path = Path("config/app.yaml"),
    *,
    encoder_factory: Callable[[str], Encoder] = _load_encoder,
    request_callable: Callable[..., Any] = urlopen,
) -> list[RetrievedChunk]:
    if not isinstance(question, str) or not question.strip():
        raise ScrapeError("Question must not be empty")
    config = _read_runtime_config(config_path)
    project_root = config_path.resolve().parent.parent
    info, _ = _collection_info(config, project_root, request_callable)
    vectors = info["config"]["params"]["vectors"]
    model_name = config["embedding"]["model"]
    try:
        encoder = encoder_factory(model_name)
        query_vector = encoder.encode(
            [question.strip()],
            batch_size=1,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
    except Exception as exc:
        raise ScrapeError(f"Unable to embed query: {exc}") from exc
    if hasattr(query_vector, "tolist"):
        query_vector = query_vector.tolist()
    if (
        not isinstance(query_vector, (list, tuple))
        or len(query_vector) != 1
        or not isinstance(query_vector[0], (list, tuple))
    ):
        raise ScrapeError("Query encoder returned an invalid embedding")
    try:
        vector = [float(value) for value in query_vector[0]]
    except (TypeError, ValueError) as exc:
        raise ScrapeError("Query embedding contains a non-numeric value") from exc
    if not vector or not all(math.isfinite(value) for value in vector):
        raise ScrapeError("Query embedding contains invalid values")
    if len(vector) != vectors["size"]:
        raise ScrapeError("Query embedding dimension does not match Qdrant collection")
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
        raise ScrapeError("Query embedding is not normalized")

    source_id = identify_scheme(
        question,
        config.get("scheme_aliases", {}),
    )
    points = _query_qdrant(
        config,
        project_root,
        vector,
        config["retrieval"]["top_k"],
        source_id,
        request_callable,
    )

    retrieved: list[RetrievedChunk] = []
    threshold = float(config["retrieval"]["minimum_relevance"])
    approved_urls = {source["id"]: source["url"] for source in config["sources"]}
    for point in points:
        metadata = point.get("payload")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("text"), str):
            continue
        try:
            similarity = float(point.get("score"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(similarity) or not -1.0 <= similarity <= 1.0:
            continue
        distance_value = 1.0 - similarity
        if distance_value > threshold:
            continue
        url = metadata.get("url")
        source = metadata.get("source_id")
        chunk_id = metadata.get("chunk_id", str(point.get("id", "")))
        if not isinstance(source, str) or approved_urls.get(source) != url:
            continue
        if not isinstance(chunk_id, str) or not chunk_id:
            continue
        if source_id and source != source_id:
            continue
        retrieved.append(
            RetrievedChunk(
                chunk_id=chunk_id,
                text=metadata["text"],
                source_id=str(source),
                url=url,
                title=str(metadata.get("title", source)),
                distance=distance_value,
                source_updated_at=metadata.get("source_updated_at"),
                metadata=metadata,
            )
        )
    return retrieved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--config", type=Path, default=Path("config/app.yaml"))
    args = parser.parse_args(argv)
    try:
        chunks = retrieve(args.question, args.config)
    except ScrapeError as exc:
        print(f"Retrieval failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            [
                {
                    "chunk_id": chunk.chunk_id,
                    "source_id": chunk.source_id,
                    "url": chunk.url,
                    "title": chunk.title,
                    "distance": chunk.distance,
                    "source_updated_at": chunk.source_updated_at,
                    "text": chunk.text,
                }
                for chunk in chunks
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
