from __future__ import annotations

import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from dotenv import load_dotenv
from pymongo import MongoClient
from qdrant_client import QdrantClient
from qdrant_client.http import models
from sentence_transformers import SentenceTransformer


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

DEFAULT_MODEL_NAME = "BAAI/bge-base-en-v1.5"
DEFAULT_VECTOR_DIM = 768
DEFAULT_COLLECTION_NAME = "youtube_videos__bge_base__768"
DEFAULT_BATCH_SIZE = 32


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def get_mongo_client() -> MongoClient:
    host = env("MONGO_HOST", "127.0.0.1")
    port = int(env("MONGO_PORT", "27017"))
    username = env("MONGO_ROOT_USERNAME")
    password = env("MONGO_ROOT_PASSWORD")
    auth_source = env("MONGO_AUTH_SOURCE", "admin")
    if username and password:
        return MongoClient(
            host=host,
            port=port,
            username=username,
            password=password,
            authSource=auth_source,
        )
    return MongoClient(host=host, port=port)


def get_qdrant_client() -> QdrantClient:
    host = env("QDRANT_HOST", "127.0.0.1")
    port = int(env("QDRANT_PORT", "6333"))
    api_key = env("QDRANT_API_KEY")
    return QdrantClient(
        host=host,
        port=port,
        api_key=api_key if api_key else None,
        timeout=60,
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
    raise RuntimeError("Unable to resolve vector params from collection info.")


def load_videos_from_mongo() -> list[dict]:
    client = get_mongo_client()
    db_name = env("MONGO_DATABASE", "yta")
    db = client[db_name]
    docs = list(db["videos"].find({}))
    client.close()
    print(f"Loaded {len(docs)} video documents from MongoDB.")
    return docs


def build_embed_text(doc: dict) -> str:
    """Construct the text to embed from a MongoDB video document."""
    parts = []

    title = (doc.get("title") or "").strip()
    if title:
        parts.append(f"Video title: {title}")

    description = (doc.get("description") or "").strip()
    if description:
        parts.append(f"Description: {description[:500]}")

    tags = doc.get("tags") or []
    if tags:
        parts.append(f"Tags: {', '.join(tags[:20])}")

    channel = (doc.get("channelTitle") or "").strip()
    if channel:
        parts.append(f"Channel: {channel}")

    sector = (doc.get("sector") or "").strip()
    if sector:
        parts.append(f"Sector: {sector}")

    skill = (doc.get("skill_name") or "").strip()
    if skill:
        parts.append(f"Skill: {skill}")

    competency = (doc.get("competency") or "").strip()
    if competency:
        parts.append(f"Competency: {competency}")

    prof_level = (doc.get("proficiency_level") or "").strip()
    if prof_level:
        parts.append(f"Proficiency level: {prof_level}")

    prof_desc = (doc.get("proficiency_description") or "").strip()
    if prof_desc:
        parts.append(f"Proficiency description: {prof_desc[:300]}")

    return "\n\n".join(parts)


def build_payload(doc: dict) -> dict:
    """Build the Qdrant payload (metadata stored alongside the vector)."""
    return {
        "video_id": doc.get("videoId", ""),
        "title": doc.get("title", ""),
        "description": (doc.get("description") or "")[:500],
        "channel_title": doc.get("channelTitle", ""),
        "thumbnail_url": doc.get("thumbnailUrl", ""),
        "published_at": doc.get("publishedAt", ""),
        "duration": doc.get("duration", ""),
        "view_count": doc.get("viewCount", 0),
        "like_count": doc.get("likeCount", 0),
        "comment_count": doc.get("commentCount", 0),
        "tags": (doc.get("tags") or [])[:20],
        "sector": doc.get("sector", ""),
        "skill_name": doc.get("skill_name", ""),
        "competency": doc.get("competency", ""),
        "proficiency_level": doc.get("proficiency_level", ""),
    }


def make_point_id(doc: dict) -> str:
    """Deterministic UUID5 from (videoId, skill_name) — matches the MongoDB unique key."""
    video_id = doc.get("videoId", "")
    skill_name = doc.get("skill_name", "")
    return str(uuid5(NAMESPACE_URL, f"yt_video::{video_id}::{skill_name}"))


def ensure_collection_compatibility(
    client: QdrantClient, collection_name: str, expected_dim: int
) -> None:
    if not client.collection_exists(collection_name=collection_name):
        raise RuntimeError(
            f"Collection '{collection_name}' does not exist. "
            "Run `python qdrant/create_collections.py` first."
        )

    info = client.get_collection(collection_name=collection_name)
    vector_params = resolve_vector_params(info)
    if vector_params.size != expected_dim:
        raise RuntimeError(
            f"Collection '{collection_name}' has vector dim {vector_params.size}, "
            f"expected {expected_dim}."
        )
    if vector_params.distance != models.Distance.COSINE:
        raise RuntimeError(
            f"Collection '{collection_name}' has distance {vector_params.distance}, "
            "expected cosine."
        )


def upsert_videos(
    client: QdrantClient,
    collection_name: str,
    model: SentenceTransformer,
    docs: list[dict],
    batch_size: int,
) -> None:
    total = len(docs)
    upserted = 0

    for start in range(0, total, batch_size):
        batch = docs[start : start + batch_size]
        texts = [build_embed_text(doc) for doc in batch]
        vectors = model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )

        points = []
        for doc, vector in zip(batch, vectors):
            points.append(
                models.PointStruct(
                    id=make_point_id(doc),
                    vector=vector.tolist(),
                    payload=build_payload(doc),
                )
            )

        client.upsert(
            collection_name=collection_name,
            wait=True,
            points=points,
        )
        upserted += len(points)
        print(f"Upserted {upserted}/{total} points")


def main() -> None:
    model_name = env("EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME)
    expected_dim = int(env("EMBEDDING_VECTOR_DIM", str(DEFAULT_VECTOR_DIM)))
    collection_name = env("QDRANT_YT_COLLECTION", DEFAULT_COLLECTION_NAME)
    batch_size = int(env("EMBEDDING_BATCH_SIZE", str(DEFAULT_BATCH_SIZE)))

    print("Loading videos from MongoDB...")
    docs = load_videos_from_mongo()
    if not docs:
        print("No documents found; exiting.")
        return

    print(f"Loading embedding model: {model_name}")
    model = SentenceTransformer(model_name)
    model_dim = model.get_sentence_embedding_dimension()
    if model_dim != expected_dim:
        raise RuntimeError(
            f"Model dimension is {model_dim}, but EMBEDDING_VECTOR_DIM is {expected_dim}."
        )

    client = get_qdrant_client()
    ensure_collection_compatibility(client, collection_name, expected_dim)
    print(f"Upserting into Qdrant collection: {collection_name}")

    upsert_videos(
        client=client,
        collection_name=collection_name,
        model=model,
        docs=docs,
        batch_size=batch_size,
    )
    print("YouTube embedding + upsert job completed.")


if __name__ == "__main__":
    main()
