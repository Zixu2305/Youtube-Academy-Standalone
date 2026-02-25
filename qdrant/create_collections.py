from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

DEFAULT_COLLECTION_NAME = "sf_skill_level_docs__bge_base__768"
DEFAULT_VECTOR_DIM = 768


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def get_qdrant_client() -> QdrantClient:
    host = env("QDRANT_HOST", "127.0.0.1")
    port = int(env("QDRANT_PORT", "6333"))
    api_key = env("QDRANT_API_KEY")
    return QdrantClient(
        host=host,
        port=port,
        api_key=api_key if api_key else None,
        timeout=30,
    )


def resolve_vector_params(collection_info: models.CollectionInfo) -> models.VectorParams:
    vectors = collection_info.config.params.vectors
    if isinstance(vectors, models.VectorParams):
        return vectors
    if isinstance(vectors, dict):
        if "" in vectors:
            return vectors[""]
        first_key = next(iter(vectors.keys()))
        return vectors[first_key]
    raise RuntimeError("Unable to resolve vector params for existing collection.")


def ensure_collection(client: QdrantClient, collection_name: str, vector_dim: int) -> None:
    if client.collection_exists(collection_name=collection_name):
        info = client.get_collection(collection_name=collection_name)
        vector_params = resolve_vector_params(info)
        if vector_params.size != vector_dim:
            raise RuntimeError(
                f"Collection '{collection_name}' exists with size {vector_params.size}, "
                f"expected {vector_dim}."
            )
        if vector_params.distance != models.Distance.COSINE:
            raise RuntimeError(
                f"Collection '{collection_name}' exists with distance "
                f"{vector_params.distance}, expected cosine."
            )
        print(f"Collection '{collection_name}' already exists with compatible config.")
        return

    client.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=vector_dim,
            distance=models.Distance.COSINE,
        ),
        hnsw_config=models.HnswConfigDiff(
            m=16,
            ef_construct=128,
        ),
    )
    print(f"Created collection '{collection_name}'.")


def ensure_payload_indexes(client: QdrantClient, collection_name: str) -> None:
    payload_indexes = (
        ("is_retired", models.PayloadSchemaType.BOOL),
        ("skill_type", models.PayloadSchemaType.KEYWORD),
        ("category", models.PayloadSchemaType.KEYWORD),
    )

    for field_name, field_schema in payload_indexes:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=field_schema,
            wait=True,
        )
        print(f"Ensured payload index: {field_name}")


def ensure_yt_payload_indexes(client: QdrantClient, collection_name: str) -> None:
    payload_indexes = (
        ("sector", models.PayloadSchemaType.KEYWORD),
        ("skill_name", models.PayloadSchemaType.KEYWORD),
        ("video_id", models.PayloadSchemaType.KEYWORD),
        ("channel_title", models.PayloadSchemaType.KEYWORD),
    )

    for field_name, field_schema in payload_indexes:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=field_schema,
            wait=True,
        )
        print(f"  Ensured payload index: {field_name}")


def main() -> None:
    vector_dim = int(env("EMBEDDING_VECTOR_DIM", str(DEFAULT_VECTOR_DIM)))

    client = get_qdrant_client()
    # Simple connectivity check.
    collections = client.get_collections()
    print(f"Connected to Qdrant. Existing collections: {len(collections.collections)}")

    # SkillsFuture collection
    sf_collection = env("QDRANT_COLLECTION", DEFAULT_COLLECTION_NAME)
    ensure_collection(client, sf_collection, vector_dim)
    ensure_payload_indexes(client, sf_collection)

    # YouTube videos collection
    yt_collection = env("QDRANT_YT_COLLECTION", "youtube_videos__bge_base__768")
    ensure_collection(client, yt_collection, vector_dim)
    ensure_yt_payload_indexes(client, yt_collection)

    print("Qdrant collection setup complete.")


if __name__ == "__main__":
    main()
