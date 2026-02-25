from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

DEFAULT_MODEL_NAME = "BAAI/bge-base-en-v1.5"
DEFAULT_VECTOR_DIM = 768
DEFAULT_SF_COLLECTION = "sf_skill_level_docs__bge_base__768"
DEFAULT_YT_COLLECTION = "youtube_videos__bge_base__768"

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


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RecommendRequest(BaseModel):
    query: str = Field(..., min_length=1, description="User query text.")
    num_videos: int = Field(3, ge=1, le=20)


class MatchedSkill(BaseModel):
    score: float
    skill_title: str
    proficiency_level: str
    category: str
    tsc_ccs_code: str
    skill_type: str


class RecommendedVideo(BaseModel):
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


class RecommendResponse(BaseModel):
    query: str
    matched_skill: MatchedSkill
    recommended_videos: list[RecommendedVideo]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/recommend/videos", response_model=RecommendResponse)
def recommend_videos(payload: RecommendRequest):
    query_text = payload.query.strip()
    if not query_text:
        raise HTTPException(status_code=400, detail="Query text cannot be empty.")

    model = get_embedding_model()
    qdrant_client = get_qdrant_client()

    # Step 1: Encode user query
    query_vector = model.encode(
        query_text,
        normalize_embeddings=True,
    ).tolist()

    # Step 2: Search SF skills collection → top 1 hit WITH its vector
    sf_collection = env("QDRANT_COLLECTION", DEFAULT_SF_COLLECTION)
    try:
        skill_hits = qdrant_client.search(
            collection_name=sf_collection,
            query_vector=query_vector,
            limit=1,
            with_payload=True,
            with_vectors=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Qdrant SF skill search failed: {exc}",
        ) from exc

    if not skill_hits:
        raise HTTPException(
            status_code=404,
            detail="No matching skill found for the given query.",
        )

    top_skill = skill_hits[0]
    skill_payload = top_skill.payload or {}

    matched_skill = MatchedSkill(
        score=float(top_skill.score),
        skill_title=skill_payload.get("skill_title", ""),
        proficiency_level=str(skill_payload.get("proficiency_level", "")),
        category=skill_payload.get("category", ""),
        tsc_ccs_code=skill_payload.get("tsc_ccs_code", ""),
        skill_type=skill_payload.get("skill_type", ""),
    )

    # Step 3: Extract the skill's stored embedding vector
    skill_vector = top_skill.vector
    if skill_vector is None:
        raise HTTPException(
            status_code=500,
            detail="Skill vector not returned from Qdrant.",
        )

    # Step 4: Search YT videos collection using the skill vector
    yt_collection = env("QDRANT_YT_COLLECTION", DEFAULT_YT_COLLECTION)
    try:
        video_hits = qdrant_client.search(
            collection_name=yt_collection,
            query_vector=skill_vector,
            limit=payload.num_videos,
            with_payload=True,
            with_vectors=False,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Qdrant YT video search failed: {exc}",
        ) from exc

    # Step 5: Build recommended videos list
    recommended_videos = []
    for hit in video_hits:
        p = hit.payload or {}
        recommended_videos.append(
            RecommendedVideo(
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

    return RecommendResponse(
        query=query_text,
        matched_skill=matched_skill,
        recommended_videos=recommended_videos,
    )
