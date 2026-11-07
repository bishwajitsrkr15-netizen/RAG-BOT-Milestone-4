"""Persist validated chunk embeddings in a versioned persistent ChromaDB collection."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import yaml

from src.ingestion.scrape_documents import ScrapeError, _atomic_write


class Collection(Protocol):
    metadata: dict[str, Any] | None

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None: ...

    def get(self, *, ids: list[str], include: list[str]) -> dict[str, Any]: ...

    def count(self) -> int: ...


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ScrapeError(f"Unable to read {label} {path}: {exc}") from exc
    records: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ScrapeError(f"{label} line {line_number} is not a JSON object")
            records.append(record)
    except json.JSONDecodeError as exc:
        raise ScrapeError(f"Invalid JSON in {label}: {exc}") from exc
    if not records:
        raise ScrapeError(f"{label} is empty")
    return records


def _validate_records(
    chunks: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
    embedding_manifest: dict[str, Any],
    configured_model: str,
    configured_metric: str,
) -> tuple[list[str], list[list[float]], list[str], list[dict[str, Any]], int]:
    if configured_metric != "cosine":
        raise ScrapeError(
            f"Unsupported Chroma distance metric {configured_metric!r}; expected 'cosine'"
        )
    if embedding_manifest.get("status") != "success":
        raise ScrapeError("Embedding manifest does not describe a successful run")
    if embedding_manifest.get("model") != configured_model:
        raise ScrapeError("Configured embedding model does not match embedding manifest")
    if embedding_manifest.get("normalized") is not True:
        raise ScrapeError("Embedding vectors are not marked as normalized")
    if embedding_manifest.get("chunk_count") != len(chunks):
        raise ScrapeError("Embedding manifest chunk count does not match chunk corpus")
    if embedding_manifest.get("vector_count") != len(vectors):
        raise ScrapeError("Embedding manifest vector count does not match vector file")

    chunks_by_id: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        chunk_id = chunk.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise ScrapeError("Chunk record has no valid chunk_id")
        if chunk_id in chunks_by_id:
            raise ScrapeError(f"Duplicate chunk ID: {chunk_id}")
        if not isinstance(chunk.get("text"), str) or not chunk["text"].strip():
            raise ScrapeError(f"Chunk {chunk_id} has no text")
        if not isinstance(chunk.get("source_id"), str) or not isinstance(
            chunk.get("url"), str
        ):
            raise ScrapeError(f"Chunk {chunk_id} is missing source metadata")
        chunks_by_id[chunk_id] = chunk

    vector_by_id: dict[str, list[float]] = {}
    dimension: int | None = None
    for record in vectors:
        chunk_id = record.get("chunk_id")
        if not isinstance(chunk_id, str) or chunk_id not in chunks_by_id:
            raise ScrapeError(f"Vector references an unknown chunk ID: {chunk_id}")
        if chunk_id in vector_by_id:
            raise ScrapeError(f"Duplicate vector for chunk ID: {chunk_id}")
        raw_vector = record.get("embedding")
        if not isinstance(raw_vector, list) or not raw_vector:
            raise ScrapeError(f"Vector for chunk {chunk_id} is empty or invalid")
        try:
            vector = [float(value) for value in raw_vector]
        except (TypeError, ValueError) as exc:
            raise ScrapeError(f"Vector for chunk {chunk_id} is not numeric") from exc
        if not all(math.isfinite(value) for value in vector):
            raise ScrapeError(f"Vector for chunk {chunk_id} contains non-finite values")
        norm = math.sqrt(sum(value * value for value in vector))
        if not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
            raise ScrapeError(f"Vector for chunk {chunk_id} is not normalized")
        if dimension is None:
            dimension = len(vector)
        elif len(vector) != dimension:
            raise ScrapeError("Embedding vectors have inconsistent dimensions")
        vector_by_id[chunk_id] = vector

    if set(vector_by_id) != set(chunks_by_id):
        missing = sorted(set(chunks_by_id) - set(vector_by_id))
        raise ScrapeError(f"Missing vectors for chunks: {missing[:5]}")
    expected_dimension = embedding_manifest.get("dimensions")
    if dimension is None or expected_dimension != dimension:
        raise ScrapeError("Vector dimension does not match embedding manifest")

    ids = list(chunks_by_id)
    documents = [chunks_by_id[chunk_id]["text"] for chunk_id in ids]
    aligned_vectors = [vector_by_id[chunk_id] for chunk_id in ids]
    metadatas = [
        {
            key: chunks_by_id[chunk_id][key]
            for key in (
                "source_id",
                "url",
                "title",
                "section",
                "position",
                "line_start",
                "line_end",
                "content_hash",
                "source_updated_at",
            )
            if chunks_by_id[chunk_id].get(key) is not None
        }
        for chunk_id in ids
    ]
    return ids, aligned_vectors, documents, metadatas, dimension


def _collection_name(base_name: str, model_name: str, chunk_hash: str) -> str:
    normalized_base = re.sub(r"[^a-zA-Z0-9._-]", "-", base_name).strip("._-")
    if not normalized_base:
        raise ScrapeError("ChromaDB collection name is invalid")
    model_hash = hashlib.sha256(model_name.encode("utf-8")).hexdigest()[:8]
    suffix = f"-{model_hash}-{chunk_hash[:12]}"
    name = f"{normalized_base[: 63 - len(suffix)]}{suffix}"
    if len(name) < 3 or len(name) > 63:
        raise ScrapeError("Generated ChromaDB collection name is invalid")
    return name


def _open_collection(
    persist_path: Path,
    name: str,
    model_name: str,
    client_factory: Callable[[str], Any] | None,
) -> Collection:
    if client_factory is None:
        try:
            import chromadb
        except ImportError as exc:
            raise ScrapeError(
                "chromadb is not installed; install dependencies from requirements.txt"
            ) from exc
        client_factory = lambda path: chromadb.PersistentClient(path=path)
    try:
        client = client_factory(str(persist_path))
        collection = client.get_or_create_collection(
            name=name,
            metadata={
                "hnsw:space": "cosine",
                "embedding_model": model_name,
            },
        )
    except Exception as exc:
        raise ScrapeError(f"Unable to open ChromaDB collection {name}: {exc}") from exc

    metadata = collection.metadata or {}
    if metadata.get("hnsw:space") != "cosine":
        raise ScrapeError("Existing ChromaDB collection does not use cosine distance")
    if metadata.get("embedding_model") != model_name:
        raise ScrapeError("Existing ChromaDB collection uses a different embedding model")
    return collection


def store_vectors(
    config_path: Path,
    client_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ScrapeError(f"Unable to read config {config_path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ScrapeError("Configuration must be a YAML object")
    artifacts = config.get("artifacts", {})
    embedding_config = config.get("embedding", {})
    vector_config = config.get("vector_store", {})
    if not all(isinstance(value, dict) for value in (artifacts, embedding_config, vector_config)):
        raise ScrapeError("Config must contain artifact, embedding, and vector-store settings")
    if vector_config.get("provider") != "chromadb":
        raise ScrapeError("Only the configured chromadb vector-store provider is supported")

    chunks_path = Path(artifacts.get("chunks", "data/chunks")) / "chunks.jsonl"
    embeddings_dir = Path(artifacts.get("embeddings", "data/embeddings"))
    vectors_path = embeddings_dir / "vectors.jsonl"
    embedding_manifest_path = Path(artifacts.get("manifests", "data/manifests")) / (
        "embedding-latest.json"
    )
    manifest_dir = Path(artifacts.get("manifests", "data/manifests"))
    manifest_path = manifest_dir / "vector-store-latest.json"
    current_manifest_path = manifest_dir / "vector-store-current.json"
    persist_path = Path(vector_config.get("path", "data/chroma"))
    collection_base = vector_config.get("collection", "hdfc-mutual-funds")
    distance_metric = vector_config.get("distance_metric", "cosine")
    model_name = embedding_config.get("model")
    if not isinstance(model_name, str) or not model_name.strip():
        raise ScrapeError("Config must specify embedding.model")
    if not isinstance(collection_base, str) or not collection_base:
        raise ScrapeError("Config must specify vector_store.collection")
    if not isinstance(distance_metric, str):
        raise ScrapeError("Config vector_store.distance_metric must be a string")

    started_at = datetime.now(timezone.utc).isoformat()
    try:
        chunks = _read_jsonl(chunks_path, "chunk corpus")
        vectors = _read_jsonl(vectors_path, "embedding vectors")
        try:
            embedding_manifest = json.loads(
                embedding_manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ScrapeError(f"Unable to read embedding manifest: {exc}") from exc
        if not isinstance(embedding_manifest, dict):
            raise ScrapeError("Embedding manifest must be a JSON object")

        ids, aligned_vectors, documents, metadatas, dimension = _validate_records(
            chunks,
            vectors,
            embedding_manifest,
            model_name,
            distance_metric,
        )
        chunks_hash = hashlib.sha256(chunks_path.read_bytes()).hexdigest()
        if embedding_manifest.get("input_sha256") != chunks_hash:
            raise ScrapeError("Embedding vectors were generated from a different chunk file")
        collection_name = _collection_name(collection_base, model_name, chunks_hash)
        collection = _open_collection(
            persist_path,
            collection_name,
            model_name,
            client_factory,
        )
        collection.upsert(
            ids=ids,
            embeddings=aligned_vectors,
            documents=documents,
            metadatas=metadatas,
        )
        stored = collection.get(ids=ids, include=["metadatas"])
        stored_ids = stored.get("ids", [])
        if set(stored_ids) != set(ids):
            raise ScrapeError("ChromaDB verification did not return every upserted ID")
        stored_count = collection.count()
        if stored_count != len(ids):
            raise ScrapeError(
                f"ChromaDB collection has {stored_count} records; expected {len(ids)}"
            )
    except ScrapeError as exc:
        manifest_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            manifest_path,
            json.dumps(
                {
                    "started_at": started_at,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "store_vectors",
                    "provider": "chromadb",
                    "status": "failed",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )
        raise
    except Exception as exc:
        manifest_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            manifest_path,
            json.dumps(
                {
                    "started_at": started_at,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "store_vectors",
                    "status": "failed",
                    "error": f"ChromaDB write or verification failed: {exc}",
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )
        raise ScrapeError(f"ChromaDB write or verification failed: {exc}") from exc

    manifest = {
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "mode": "store_vectors",
        "path": str(persist_path),
        "collection": collection_name,
        "collection_base": collection_base,
        "distance_metric": distance_metric,
        "embedding_model": model_name,
        "dimensions": dimension,
        "chunk_count": len(chunks),
        "vector_count": len(vectors),
        "stored_count": stored_count,
        "chunks_sha256": chunks_hash,
        "status": "success",
    }
    manifest_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        current_manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    _atomic_write(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/app.yaml"))
    args = parser.parse_args(argv)
    try:
        manifest = store_vectors(args.config)
    except ScrapeError as exc:
        print(f"Vector storage failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
