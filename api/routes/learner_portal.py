from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.http import models
from sentence_transformers import SentenceTransformer


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

ACADEMY_FRONTEND_DIR = ROOT_DIR / "api" / "frontend" / "academy"

DEFAULT_MODEL_NAME = "BAAI/bge-base-en-v1.5"
DEFAULT_VECTOR_DIM = 768
DEFAULT_YT_COLLECTION = "youtube_videos__bge_base__768"

page_router = APIRouter()
api_router = APIRouter()


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def get_mysql_connection():
    host = env("DB_HOST", "127.0.0.1")
    if env("DB_PORT"):
        port = int(env("DB_PORT", "3306"))
    else:
        port = 3306 if host == "mysql" else int(env("MYSQL_PORT", "3306"))

    return mysql.connector.connect(
        host=host,
        port=port,
        user=env("DB_USER", env("MYSQL_USER", "yta")),
        password=env("DB_PASSWORD", env("MYSQL_PASSWORD", "")),
        database=env("DB_NAME", env("MYSQL_DATABASE", "yta")),
        autocommit=True,
    )


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


def parse_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def proficiency_sort_key(value: str) -> tuple[int, str]:
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits:
        return int(digits), value.lower()
    return 9999, value.lower()


class SectorSummary(BaseModel):
    sector: str
    skill_count: int


class SkillSummary(BaseModel):
    skill: str
    mapped_proficiency_count: int


class SkillProficiencyMap(BaseModel):
    proficiency_level: str
    proficiency_description: str
    mapped_skill_ids: list[int]
    knowledge_items: list[str]
    ability_items: list[str]


class SkillMapResponse(BaseModel):
    sector: str
    skill: str
    count: int
    mappings: list[SkillProficiencyMap]


class PublicRecommendRequest(BaseModel):
    sector: str = Field(..., min_length=1)
    skill: str = Field(..., min_length=1)
    proficiency_level: str | None = None
    competency: str | None = None
    top_k: int = Field(6, ge=1, le=20)
    strict_skill_match: bool = True


class PublicRecommendedVideo(BaseModel):
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


class PublicRecommendResponse(BaseModel):
    query: str
    applied_filters: dict[str, str]
    count: int
    results: list[PublicRecommendedVideo]


def build_video_filter(
    sector: str,
    skill: str,
    proficiency_level: str | None,
    strict_skill_match: bool,
) -> models.Filter:
    must_conditions: list[models.FieldCondition] = [
        models.FieldCondition(
            key="sector",
            match=models.MatchValue(value=sector),
        )
    ]

    if strict_skill_match:
        must_conditions.append(
            models.FieldCondition(
                key="skill_name",
                match=models.MatchValue(value=skill),
            )
        )

    if proficiency_level:
        must_conditions.append(
            models.FieldCondition(
                key="proficiency_level",
                match=models.MatchValue(value=proficiency_level),
            )
        )

    return models.Filter(must=must_conditions)


@page_router.get("/academy", include_in_schema=False)
def academy_page() -> FileResponse:
    index_file = ACADEMY_FRONTEND_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=500, detail="Academy page is not available.")
    return FileResponse(index_file)


@page_router.get("/academy/", include_in_schema=False)
def academy_page_with_trailing_slash() -> FileResponse:
    return academy_page()


@api_router.get("/public/sectors", response_model=list[SectorSummary])
def list_sectors():
    sql = """
    SELECT
      m.sector_name_raw AS sector,
      COUNT(DISTINCT m.source_skill_title) AS skill_count
    FROM map_sf_to_cat_skill m
    WHERE m.sector_name_raw IS NOT NULL
      AND m.sector_name_raw <> ''
      AND m.source_skill_title IS NOT NULL
      AND m.source_skill_title <> ''
    GROUP BY m.sector_name_raw
    ORDER BY m.sector_name_raw;
    """

    conn = get_mysql_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql)
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    return [
        SectorSummary(
            sector=str(row["sector"]),
            skill_count=parse_int(row["skill_count"]),
        )
        for row in rows
    ]


@api_router.get("/public/skills", response_model=list[SkillSummary])
def list_skills(
    sector: str | None = Query(default=None),
    q: str = Query(default="", max_length=120),
    limit: int = Query(default=400, ge=1, le=1000),
):
    where_clauses = [
        "m.source_skill_title IS NOT NULL",
        "m.source_skill_title <> ''",
    ]
    params: list[object] = []

    sector_value = (sector or "").strip()
    if sector_value:
        where_clauses.append("m.sector_name_raw = %s")
        params.append(sector_value)

    query_value = q.strip()
    if query_value:
        where_clauses.append("m.source_skill_title LIKE %s")
        params.append(f"%{query_value}%")

    sql = f"""
    SELECT
      m.source_skill_title AS skill,
      COUNT(DISTINCT CONCAT(m.sf_skill_id, ':', m.proficiency_level)) AS mapped_proficiency_count
    FROM map_sf_to_cat_skill m
    WHERE {' AND '.join(where_clauses)}
    GROUP BY m.source_skill_title
    ORDER BY m.source_skill_title
    LIMIT %s;
    """
    params.append(limit)

    conn = get_mysql_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    return [
        SkillSummary(
            skill=str(row["skill"]),
            mapped_proficiency_count=parse_int(row["mapped_proficiency_count"]),
        )
        for row in rows
    ]


@api_router.get("/public/skill-map", response_model=SkillMapResponse)
def get_skill_map(
    sector: str = Query(..., min_length=1),
    skill: str = Query(..., min_length=1),
):
    sql = """
    SELECT
      m.sf_skill_id,
      m.proficiency_level,
      COALESCE(sl.proficiency_description, '') AS proficiency_description,
      ci.item_type,
      ci.item_text
    FROM map_sf_to_cat_skill m
    LEFT JOIN sf_skill_level sl
      ON sl.sf_skill_id = m.sf_skill_id
      AND sl.proficiency_level = m.proficiency_level
    LEFT JOIN sf_competency_item ci
      ON ci.sf_skill_id = m.sf_skill_id
      AND ci.proficiency_level = m.proficiency_level
    WHERE m.sector_name_raw = %s
      AND m.source_skill_title = %s
    ORDER BY m.proficiency_level, ci.item_type, ci.item_id;
    """

    conn = get_mysql_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, (sector.strip(), skill.strip()))
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No mapped competency data found for this sector-skill pair.",
        )

    grouped: dict[str, dict] = {}
    for row in rows:
        level = str(row.get("proficiency_level") or "").strip()
        if not level:
            continue

        entry = grouped.setdefault(
            level,
            {
                "proficiency_level": level,
                "proficiency_description": str(row.get("proficiency_description") or ""),
                "mapped_skill_ids": [],
                "knowledge_items": [],
                "ability_items": [],
                "_seen_skill_ids": set(),
                "_seen_knowledge": set(),
                "_seen_ability": set(),
            },
        )

        sf_skill_id = parse_int(row.get("sf_skill_id"), default=0)
        if sf_skill_id > 0 and sf_skill_id not in entry["_seen_skill_ids"]:
            entry["_seen_skill_ids"].add(sf_skill_id)
            entry["mapped_skill_ids"].append(sf_skill_id)

        item_type = str(row.get("item_type") or "").strip()
        item_text = str(row.get("item_text") or "").strip()
        if not item_type or not item_text:
            continue

        if item_type == "knowledge" and item_text not in entry["_seen_knowledge"]:
            entry["_seen_knowledge"].add(item_text)
            entry["knowledge_items"].append(item_text)
        elif item_type == "ability" and item_text not in entry["_seen_ability"]:
            entry["_seen_ability"].add(item_text)
            entry["ability_items"].append(item_text)

    mappings: list[SkillProficiencyMap] = []
    for level in sorted(grouped.keys(), key=proficiency_sort_key):
        entry = grouped[level]
        mappings.append(
            SkillProficiencyMap(
                proficiency_level=entry["proficiency_level"],
                proficiency_description=entry["proficiency_description"],
                mapped_skill_ids=entry["mapped_skill_ids"],
                knowledge_items=entry["knowledge_items"],
                ability_items=entry["ability_items"],
            )
        )

    return SkillMapResponse(
        sector=sector.strip(),
        skill=skill.strip(),
        count=len(mappings),
        mappings=mappings,
    )


@api_router.post("/public/recommend/videos", response_model=PublicRecommendResponse)
def public_recommend_videos(payload: PublicRecommendRequest):
    sector = payload.sector.strip()
    skill = payload.skill.strip()
    proficiency_level = (payload.proficiency_level or "").strip() or None
    competency = (payload.competency or "").strip()

    query_parts = [skill]
    if proficiency_level:
        query_parts.append(f"Proficiency level {proficiency_level}")
    if competency:
        query_parts.append(competency)
    query_text = ". ".join(query_parts)

    model = get_embedding_model()
    query_vector = model.encode(
        query_text,
        normalize_embeddings=True,
    ).tolist()

    yt_collection = env("QDRANT_YT_COLLECTION", DEFAULT_YT_COLLECTION)
    qdrant = get_qdrant_client()
    search_limit = max(payload.top_k * 4, payload.top_k)

    def run_search(
        current_proficiency: str | None,
        strict_skill_match: bool,
    ):
        search_filter = build_video_filter(
            sector=sector,
            skill=skill,
            proficiency_level=current_proficiency,
            strict_skill_match=strict_skill_match,
        )
        return qdrant.search(
            collection_name=yt_collection,
            query_vector=query_vector,
            query_filter=search_filter,
            limit=search_limit,
            with_payload=True,
            with_vectors=False,
        )

    active_proficiency = proficiency_level
    active_strict = payload.strict_skill_match

    try:
        hits = run_search(
            current_proficiency=active_proficiency,
            strict_skill_match=active_strict,
        )

        if not hits and active_proficiency:
            active_proficiency = None
            hits = run_search(
                current_proficiency=active_proficiency,
                strict_skill_match=active_strict,
            )

        if not hits and active_strict:
            active_strict = False
            hits = run_search(
                current_proficiency=active_proficiency,
                strict_skill_match=active_strict,
            )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Video recommendation search failed: {exc}",
        ) from exc

    results: list[PublicRecommendedVideo] = []
    for hit in hits[: payload.top_k]:
        item = hit.payload or {}
        results.append(
            PublicRecommendedVideo(
                score=float(hit.score),
                video_id=str(item.get("video_id") or ""),
                title=str(item.get("title") or ""),
                description=str(item.get("description") or ""),
                channel_title=str(item.get("channel_title") or ""),
                thumbnail_url=str(item.get("thumbnail_url") or ""),
                published_at=str(item.get("published_at") or ""),
                duration=str(item.get("duration") or ""),
                view_count=parse_int(item.get("view_count")),
                like_count=parse_int(item.get("like_count")),
                comment_count=parse_int(item.get("comment_count")),
                tags=[str(tag) for tag in (item.get("tags") or [])],
                sector=str(item.get("sector") or ""),
                skill_name=str(item.get("skill_name") or ""),
                competency=str(item.get("competency") or ""),
                proficiency_level=str(item.get("proficiency_level") or ""),
            )
        )

    applied_filters = {"sector": sector}
    if active_strict:
        applied_filters["skill"] = skill
    if active_proficiency:
        applied_filters["proficiency_level"] = active_proficiency

    return PublicRecommendResponse(
        query=query_text,
        applied_filters=applied_filters,
        count=len(results),
        results=results,
    )
