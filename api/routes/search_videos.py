from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.http import models
from sentence_transformers import SentenceTransformer


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

DEFAULT_MODEL_NAME = "BAAI/bge-base-en-v1.5"
DEFAULT_VECTOR_DIM = 768
DEFAULT_COLLECTION_NAME = "youtube_videos__bge_base__768"

router = APIRouter()


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    model_name = env("EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME)
    model = SentenceTransformer(model_name)
    expected_dim = int(env("EMBEDDING_VECTOR_DIM", str(DEFAULT_VECTOR_DIM)))
    model_dim = model.get_sentence_embedding_dimension()
    if model_dim != expected_dim:
        raise RuntimeError(
            f"Model dimension is {model_dim}, but EMBEDDING_VECTOR_DIM is {expected_dim}."
        )
    return model


@lru_cache(maxsize=1)
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


class VideoSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Search query text.")
    top_k: int = Field(10, ge=1, le=50)
    sector: str | None = None
    skill_name: str | None = None
    channel_title: str | None = None


class VideoResult(BaseModel):
    score: float
    video_id: str
    title: str
    description: str
    channel_title: str
    thumbnail_url: str
    published_at: str
    duration: str
    view_count: int
    like_count: int
    comment_count: int
    tags: list[str]
    sector: str
    skill_name: str
    competency: str
    proficiency_level: str


class VideoSearchResponse(BaseModel):
    query: str
    collection: str
    model_name: str
    count: int
    results: list[VideoResult]


def build_query_filter(payload: VideoSearchRequest) -> models.Filter | None:
    must_conditions: list[models.FieldCondition] = []

    if payload.sector:
        must_conditions.append(
            models.FieldCondition(
                key="sector",
                match=models.MatchValue(value=payload.sector),
            )
        )
    if payload.skill_name:
        must_conditions.append(
            models.FieldCondition(
                key="skill_name",
                match=models.MatchValue(value=payload.skill_name),
            )
        )
    if payload.channel_title:
        must_conditions.append(
            models.FieldCondition(
                key="channel_title",
                match=models.MatchValue(value=payload.channel_title),
            )
        )

    if not must_conditions:
        return None
    return models.Filter(must=must_conditions)


@router.post("/search/videos", response_model=VideoSearchResponse)
def search_videos(payload: VideoSearchRequest):
    query_text = payload.query.strip()
    if not query_text:
        raise HTTPException(status_code=400, detail="Query text cannot be empty.")

    model = get_embedding_model()
    query_vector = model.encode(
        query_text,
        normalize_embeddings=True,
    ).tolist()

    query_filter = build_query_filter(payload)
    collection_name = env("QDRANT_YT_COLLECTION", DEFAULT_COLLECTION_NAME)
    qdrant_client = get_qdrant_client()

    try:
        hits = qdrant_client.search(
            collection_name=collection_name,
            query_vector=query_vector,
            query_filter=query_filter,
            limit=payload.top_k,
            with_payload=True,
            with_vectors=False,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Qdrant search failed: {exc}",
        ) from exc

    results = []
    for hit in hits:
        p = hit.payload or {}
        results.append(
            VideoResult(
                score=float(hit.score),
                video_id=p.get("video_id", ""),
                title=p.get("title", ""),
                description=p.get("description", ""),
                channel_title=p.get("channel_title", ""),
                thumbnail_url=p.get("thumbnail_url", ""),
                published_at=p.get("published_at", ""),
                duration=p.get("duration", ""),
                view_count=p.get("view_count", 0),
                like_count=p.get("like_count", 0),
                comment_count=p.get("comment_count", 0),
                tags=p.get("tags", []),
                sector=p.get("sector", ""),
                skill_name=p.get("skill_name", ""),
                competency=p.get("competency", ""),
                proficiency_level=p.get("proficiency_level", ""),
            )
        )

    return VideoSearchResponse(
        query=query_text,
        collection=collection_name,
        model_name=env("EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME),
        count=len(results),
        results=results,
    )
