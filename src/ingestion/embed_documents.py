"""Embed the persisted chunk corpus with the configured sentence-transformers model."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Protocol

import yaml

from src.ingestion.scrape_documents import ScrapeError, _atomic_write


DEFAULT_BATCH_SIZE = 32


class Encoder(Protocol):
    def encode(
        self,
        sentences: list[str],
        *,
        batch_size: int,
        convert_to_numpy: bool,
        normalize_embeddings: bool,
        show_progress_bar: bool,
    ) -> Any: ...


def _read_chunks(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ScrapeError(f"Unable to read chunk corpus {path}: {exc}") from exc

    chunks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            chunk = json.loads(line)
            if not isinstance(chunk, dict):
                raise ScrapeError(f"Chunk on line {line_number} is not an object")
            chunk_id = chunk.get("chunk_id")
            text = chunk.get("text")
            if not isinstance(chunk_id, str) or not chunk_id:
                raise ScrapeError(f"Chunk on line {line_number} has no chunk_id")
            if chunk_id in seen_ids:
                raise ScrapeError(f"Duplicate chunk ID: {chunk_id}")
            if not isinstance(text, str) or not text.strip():
                raise ScrapeError(f"Chunk {chunk_id} has no text")
            if not isinstance(chunk.get("source_id"), str) or not isinstance(
                chunk.get("url"), str
            ):
                raise ScrapeError(f"Chunk {chunk_id} is missing source metadata")
            seen_ids.add(chunk_id)
            chunks.append(chunk)
    except json.JSONDecodeError as exc:
        raise ScrapeError(f"Invalid JSON in chunk corpus: {exc}") from exc

    if not chunks:
        raise ScrapeError("Chunk corpus is empty")
    return chunks


@lru_cache(maxsize=2)
def _load_encoder(model_name: str) -> Encoder:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ScrapeError(
            "sentence-transformers is not installed; install dependencies from requirements.txt"
        ) from exc
    try:
        return SentenceTransformer(model_name)
    except Exception as exc:
        raise ScrapeError(f"Unable to load embedding model {model_name}: {exc}") from exc


def _to_rows(encoded: Any) -> list[list[float]]:
    if hasattr(encoded, "tolist"):
        encoded = encoded.tolist()
    if not isinstance(encoded, (list, tuple)):
        raise ScrapeError("Embedding model returned an unsupported vector container")
    rows: list[list[float]] = []
    for row in encoded:
        if hasattr(row, "tolist"):
            row = row.tolist()
        if not isinstance(row, (list, tuple)) or not row:
            raise ScrapeError("Embedding model returned an empty or malformed vector")
        try:
            vector = [float(value) for value in row]
        except (TypeError, ValueError) as exc:
            raise ScrapeError("Embedding vector contains a non-numeric value") from exc
        if not all(math.isfinite(value) for value in vector):
            raise ScrapeError("Embedding vector contains a non-finite value")
        rows.append(vector)
    return rows


def embed_chunks(
    chunks: list[dict[str, Any]],
    model_name: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    encoder_factory: Callable[[str], Encoder] = _load_encoder,
) -> tuple[list[dict[str, Any]], int]:
    if batch_size < 1:
        raise ScrapeError("batch_size must be greater than zero")
    if not chunks:
        raise ScrapeError("Cannot embed an empty chunk corpus")

    try:
        encoder = encoder_factory(model_name)
    except Exception as exc:
        raise ScrapeError(f"Unable to initialize embedding model {model_name}: {exc}") from exc
    output: list[dict[str, Any]] = []
    dimension: int | None = None
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        try:
            encoded = encoder.encode(
                [chunk["text"] for chunk in batch],
                batch_size=batch_size,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            raise ScrapeError(f"Embedding batch failed at chunk {start}: {exc}") from exc

        rows = _to_rows(encoded)
        if len(rows) != len(batch):
            raise ScrapeError(
                f"Embedding batch returned {len(rows)} vectors for {len(batch)} chunks"
            )
        for chunk, vector in zip(batch, rows):
            if dimension is None:
                dimension = len(vector)
            elif len(vector) != dimension:
                raise ScrapeError("Embedding model returned inconsistent vector dimensions")
            output.append({"chunk_id": chunk["chunk_id"], "embedding": vector})

    if dimension is None:
        raise ScrapeError("Embedding model returned no vectors")
    return output, dimension


def create_embeddings(
    config_path: Path,
    batch_size: int = DEFAULT_BATCH_SIZE,
    encoder_factory: Callable[[str], Encoder] = _load_encoder,
) -> dict[str, Any]:
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ScrapeError(f"Unable to read config {config_path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ScrapeError("Configuration must be a YAML object")
    artifacts = config.get("artifacts", {})
    embedding_config = config.get("embedding", {})
    if not isinstance(artifacts, dict) or not isinstance(embedding_config, dict):
        raise ScrapeError("Config must contain artifact and embedding settings")
    model_name = embedding_config.get("model")
    if not isinstance(model_name, str) or not model_name.strip():
        raise ScrapeError("Config must specify embedding.model")

    chunks_path = Path(artifacts.get("chunks", "data/chunks")) / "chunks.jsonl"
    embeddings_dir = Path(artifacts.get("embeddings", "data/embeddings"))
    manifest_dir = Path(artifacts.get("manifests", "data/manifests"))
    manifest_path = manifest_dir / "embedding-latest.json"
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        chunks = _read_chunks(chunks_path)
        vectors, dimension = embed_chunks(
            chunks,
            model_name,
            batch_size=batch_size,
            encoder_factory=encoder_factory,
        )
    except ScrapeError as exc:
        manifest_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(
            manifest_path,
            json.dumps(
                {
                    "started_at": started_at,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "embed_chunks",
                    "input": str(chunks_path),
                    "model": model_name,
                    "status": "failed",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )
        raise

    input_hash = hashlib.sha256(chunks_path.read_bytes()).hexdigest()
    vectors_jsonl = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in vectors
    )
    # Publish vectors only after all batches and validations have succeeded.
    _atomic_write(
        embeddings_dir / "vectors.jsonl",
        vectors_jsonl.encode("utf-8"),
    )
    manifest = {
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "mode": "embed_chunks",
        "input": str(chunks_path),
        "input_sha256": input_hash,
        "output": str(embeddings_dir / "vectors.jsonl"),
        "model": model_name,
        "normalized": True,
        "batch_size": batch_size,
        "chunk_count": len(chunks),
        "vector_count": len(vectors),
        "dimensions": dimension,
        "status": "success",
    }
    manifest_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/app.yaml"))
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args(argv)
    try:
        manifest = create_embeddings(args.config, batch_size=args.batch_size)
    except ScrapeError as exc:
        print(f"Embedding failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
