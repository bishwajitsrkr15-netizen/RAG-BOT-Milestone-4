"""Retrieve relevant chunks from the configured persistent ChromaDB collection."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import yaml

from src.ingestion.embed_documents import Encoder, _load_encoder
from src.ingestion.scrape_documents import ScrapeError


class QueryCollection(Protocol):
    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
        where: dict[str, Any] | None = None,
        include: list[str],
    ) -> dict[str, Any]: ...

    def count(self) -> int: ...


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
    artifacts = config.get("artifacts", {})
    sources = config.get("sources")
    if not all(
        isinstance(value, dict)
        for value in (embedding, vector_store, retrieval, artifacts)
    ) or not isinstance(sources, list):
        raise ScrapeError("Config must contain embedding, vector-store, retrieval, and artifacts objects")
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
    if vector_store.get("provider") != "chromadb":
        raise ScrapeError("Only the configured chromadb vector-store provider is supported")
    if vector_store.get("distance_metric") != "cosine":
        raise ScrapeError("Retrieval currently supports only cosine distance")
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


def _load_collection(
    config: dict[str, Any],
    chroma_factory: Callable[[str], Any] | None,
) -> tuple[QueryCollection, dict[str, Any]]:
    artifacts = config.get("artifacts", {})
    vector_config = config["vector_store"]
    manifest_path = Path(artifacts.get("manifests", "data/manifests")) / (
        "vector-store-current.json"
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScrapeError(f"Unable to read current vector-store manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("status") != "success":
        raise ScrapeError("No successfully verified vector-store collection is configured")

    model_name = config["embedding"]["model"]
    if manifest.get("embedding_model") != model_name:
        raise ScrapeError("Current vector-store collection uses a different embedding model")
    if manifest.get("distance_metric") != vector_config["distance_metric"]:
        raise ScrapeError("Current vector-store collection uses a different distance metric")
    if manifest.get("collection_base") != vector_config.get("collection"):
        raise ScrapeError("Current vector-store collection does not match configured collection")
    if chroma_factory is None:
        try:
            import chromadb
        except ImportError as exc:
            raise ScrapeError(
                "chromadb is not installed; install dependencies from requirements.txt"
            ) from exc
        chroma_factory = lambda path: chromadb.PersistentClient(path=path)
    try:
        client = chroma_factory(manifest["path"])
        collection = client.get_collection(manifest["collection"])
    except Exception as exc:
        raise ScrapeError(f"Unable to open current ChromaDB collection: {exc}") from exc

    metadata = collection.metadata or {}
    if metadata.get("hnsw:space") != "cosine":
        raise ScrapeError("Vector-store collection is not configured for cosine distance")
    if metadata.get("embedding_model") != model_name:
        raise ScrapeError("Vector-store collection embedding model does not match config")
    if collection.count() != manifest.get("stored_count"):
        raise ScrapeError("Vector-store record count differs from the verified manifest")
    return collection, manifest


def retrieve(
    question: str,
    config_path: Path = Path("config/app.yaml"),
    *,
    encoder_factory: Callable[[str], Encoder] = _load_encoder,
    chroma_factory: Callable[[str], Any] | None = None,
) -> list[RetrievedChunk]:
    if not isinstance(question, str) or not question.strip():
        raise ScrapeError("Question must not be empty")
    config = _read_runtime_config(config_path)
    collection, manifest = _load_collection(config, chroma_factory)
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
    if len(vector) != manifest.get("dimensions"):
        raise ScrapeError("Query embedding dimension does not match current index")
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
        raise ScrapeError("Query embedding is not normalized")

    source_id = identify_scheme(
        question,
        config.get("scheme_aliases", {}),
    )
    where = {"source_id": source_id} if source_id else None
    n_results = min(config["retrieval"]["top_k"], collection.count())
    if source_id and n_results == 0:
        return []
    try:
        result = collection.query(
            query_embeddings=[vector],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        raise ScrapeError(f"ChromaDB query failed: {exc}") from exc

    ids = result.get("ids", [[]])[0]
    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    retrieved: list[RetrievedChunk] = []
    threshold = float(config["retrieval"]["minimum_relevance"])
    approved_urls = {source["id"]: source["url"] for source in config["sources"]}
    for chunk_id, text, metadata, distance in zip(ids, documents, metadatas, distances):
        if not isinstance(metadata, dict) or not isinstance(text, str):
            continue
        try:
            distance_value = float(distance)
        except (TypeError, ValueError):
            continue
        if distance_value > threshold:
            continue
        url = metadata.get("url")
        source = metadata.get("source_id")
        if not isinstance(source, str) or approved_urls.get(source) != url:
            continue
        if source_id and source != source_id:
            continue
        retrieved.append(
            RetrievedChunk(
                chunk_id=chunk_id,
                text=text,
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
