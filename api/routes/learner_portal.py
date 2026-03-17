from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import mysql.connector
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.http import models

from api.routes.recommend import (
    get_embedding_model,
    get_bm25_index,
    get_reranker_model,
    reciprocal_rank_fusion,
    apply_metadata_boosts,
    build_yt_rerank_text,
    SEMANTIC_CANDIDATES,
    BM25_CANDIDATES,
    RERANK_TOP_N,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

ACADEMY_FRONTEND_DIR = ROOT_DIR / "api" / "frontend" / "academy"

DEFAULT_YT_COLLECTION = "youtube_videos__bge_base__768"
PORTAL_PREVIEW_LIMIT = 8
PORTAL_YOUTUBE_API_KEY_ENV_NAMES = (
    "YOUTUBE_API_KEY",
    "GOOGLE_API_KEY",
    "YOUTUBE_DATA_API_KEY",
)

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


def normalize_search_text(value: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in str(value or ""))
    return " ".join(normalized.split())


def tokenize_search_text(value: str) -> list[str]:
    normalized = normalize_search_text(value)
    return [token for token in normalized.split() if token]


def is_ordered_subsequence(query: str, target: str) -> bool:
    if not query or not target:
        return False

    query_index = 0
    for character in target:
        if character == query[query_index]:
            query_index += 1
            if query_index == len(query):
                return True
    return False


def levenshtein_distance(source: str, target: str) -> int:
    if source == target:
        return 0
    if not source:
        return len(target)
    if not target:
        return len(source)

    previous_row = list(range(len(target) + 1))
    for source_index, source_char in enumerate(source):
        current_row = [source_index + 1]
        for target_index, target_char in enumerate(target):
            substitution_cost = 0 if source_char == target_char else 1
            current_row.append(
                min(
                    current_row[target_index] + 1,
                    previous_row[target_index + 1] + 1,
                    previous_row[target_index] + substitution_cost,
                )
            )
        previous_row = current_row
    return previous_row[-1]


def similarity_ratio(source: str, target: str) -> float:
    longest_length = max(len(source), len(target))
    if longest_length == 0:
        return 1.0
    return 1.0 - levenshtein_distance(source, target) / longest_length


def score_search_token(query_token: str, candidate_token: str) -> float:
    if not query_token or not candidate_token:
        return 0.0
    if candidate_token == query_token:
        return 1.0
    if candidate_token.startswith(query_token):
        return max(0.82, 0.98 - (len(candidate_token) - len(query_token)) * 0.03)
    if query_token in candidate_token:
        return max(0.7, 0.84 - candidate_token.index(query_token) * 0.02)
    if len(query_token) >= 3 and is_ordered_subsequence(query_token, candidate_token):
        return max(0.58, 0.72 - max(0, len(candidate_token) - len(query_token)) * 0.02)
    if len(query_token) >= 4 and len(candidate_token) > len(query_token):
        shared_prefix_length = min(4, len(query_token))
        candidate_prefix = candidate_token[: len(query_token) + 1]
        prefix_ratio = similarity_ratio(query_token, candidate_prefix)
        if candidate_token.startswith(query_token[:shared_prefix_length]) and prefix_ratio >= 0.68:
            return min(0.78, prefix_ratio + 0.04)
    if len(query_token) >= 4:
        ratio = similarity_ratio(query_token, candidate_token)
        if ratio >= 0.72:
            return ratio * 0.84
    return 0.0


def score_label_match(query: str, label: str) -> float:
    normalized_query = normalize_search_text(query)
    normalized_label = normalize_search_text(label)
    if not normalized_query or not normalized_label:
        return 0.0

    label_tokens = tokenize_search_text(normalized_label)
    compact_query = normalized_query.replace(" ", "")
    compact_label = normalized_label.replace(" ", "")
    raw_query_tokens = tokenize_search_text(normalized_query)
    query_tokens = [token for token in raw_query_tokens if len(token) > 1] or [normalized_query]

    phrase_score = 0.0
    if normalized_label == normalized_query:
        phrase_score = 5.2
    elif normalized_label.startswith(normalized_query):
        phrase_score = 4.4
    elif normalized_query in normalized_label:
        phrase_score = 3.6
    elif len(compact_query) >= 3 and is_ordered_subsequence(compact_query, compact_label):
        phrase_score = 2.6

    token_scores = []
    for query_token in query_tokens:
        candidates = [normalized_label, *label_tokens]
        token_scores.append(max(score_search_token(query_token, candidate) for candidate in candidates))

    average_token_score = sum(token_scores) / len(token_scores) if token_scores else 0.0
    strong_token_matches = sum(1 for value in token_scores if value >= 0.7)
    compact_similarity = similarity_ratio(compact_query, compact_label) if len(compact_query) >= 4 else 0.0

    matched = (
        phrase_score >= 3.6
        or average_token_score >= 0.68
        or (len(query_tokens) > 1 and strong_token_matches >= max(1, len(query_tokens) - 1))
        or compact_similarity >= 0.74
    )
    if not matched:
        return 0.0

    return (
        phrase_score
        + average_token_score * 4
        + strong_token_matches * 0.35
        + compact_similarity * 1.8
    )


class SectorSummary(BaseModel):
    sector: str
    skill_count: int


class SkillSummary(BaseModel):
    skill: str
    mapped_proficiency_count: int


class SkillSuggestionSector(BaseModel):
    sector: str
    mapped_proficiency_count: int


class SkillSuggestion(BaseModel):
    skill: str
    sector_count: int
    total_mapped_proficiency_count: int
    match_score: float
    sectors: list[SkillSuggestionSector]


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
    rrf_score: float
    reranker_score: float
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


class PublicVideoPreviewRequest(BaseModel):
    sector: str = Field(..., min_length=1)
    skill: str = Field(..., min_length=1)
    proficiency_level: str | None = None
    competency: str | None = None
    extra_context: str | None = None


class PublicPreviewVideo(BaseModel):
    sector: str
    skill_name: str
    competency: str
    item_type: str
    proficiency_level: str
    proficiency_description: str
    video_id: str
    published_at: str
    title: str
    description: str
    view_count: int
    like_count: int
    comment_count: int
    tags: list[str]
    duration: str
    channel_title: str
    thumbnail_url: str
    already_ingested: bool


class PublicVideoPreviewResponse(BaseModel):
    query: str
    count: int
    already_ingested_count: int
    quota_exceeded: bool
    quota_message: str | None = None
    quota: dict[str, int | str]
    results: list[PublicPreviewVideo]


class PublicIngestVideo(BaseModel):
    sector: str
    skill_name: str
    competency: str = ""
    item_type: str = ""
    proficiency_level: str = ""
    proficiency_description: str = ""
    videoId: str = Field(..., min_length=1)
    publishedAt: str = ""
    title: str = ""
    description: str = ""
    viewCount: int = 0
    likeCount: int = 0
    commentCount: int = 0
    tags: list[str] = Field(default_factory=list)
    duration: str = ""
    channelTitle: str = ""
    thumbnailUrl: str = ""


class PublicVideoIngestRequest(BaseModel):
    videos: list[PublicIngestVideo] = Field(..., min_length=1, max_length=PORTAL_PREVIEW_LIMIT)


class PublicVideoIngestResponse(BaseModel):
    message: str
    count: int
    inserted: int
    updated: int
    unchanged: int
    error_count: int
    embedding_status: str
    embedding_indexed: int


@lru_cache(maxsize=1)
def get_skill_suggestion_index() -> list[dict[str, object]]:
    sql = """
    SELECT
      m.source_skill_title AS skill,
      m.sector_name_raw AS sector,
      COUNT(DISTINCT CONCAT(m.sf_skill_id, ':', m.proficiency_level)) AS mapped_proficiency_count
    FROM map_sf_to_cat_skill m
    WHERE m.source_skill_title IS NOT NULL
      AND m.source_skill_title <> ''
      AND m.sector_name_raw IS NOT NULL
      AND m.sector_name_raw <> ''
    GROUP BY m.source_skill_title, m.sector_name_raw
    ORDER BY m.source_skill_title, m.sector_name_raw;
    """

    conn = get_mysql_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql)
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        skill = str(row.get("skill") or "").strip()
        sector = str(row.get("sector") or "").strip()
        if not skill or not sector:
            continue

        entry = grouped.setdefault(
            skill,
            {
                "skill": skill,
                "sectors": [],
                "sector_count": 0,
                "total_mapped_proficiency_count": 0,
            },
        )

        mapped_count = parse_int(row.get("mapped_proficiency_count"))
        entry["sectors"].append(
            {
                "sector": sector,
                "mapped_proficiency_count": mapped_count,
            }
        )
        entry["total_mapped_proficiency_count"] += mapped_count

    for entry in grouped.values():
        entry["sectors"].sort(
            key=lambda item: (
                -parse_int(item.get("mapped_proficiency_count")),
                str(item.get("sector") or "").lower(),
            )
        )
        entry["sector_count"] = len(entry["sectors"])

    return list(grouped.values())


def get_portal_youtube_api_key() -> str:
    for env_name in PORTAL_YOUTUBE_API_KEY_ENV_NAMES:
        value = env(env_name, "")
        if value:
            return value
    raise HTTPException(
        status_code=503,
        detail=(
            "YouTube ingestion is not configured. Set one of "
            f"{', '.join(PORTAL_YOUTUBE_API_KEY_ENV_NAMES)} on the server."
        ),
    )


def get_portal_quota_context() -> dict[str, int | str]:
    from pipelines.youtube.youtube_ingestion_service import build_quota_estimate
    from pipelines.youtube.youtube_config import (
        DEFAULT_DAILY_QUOTA_LIMIT,
        DEFAULT_QUOTA_WARNING_THRESHOLD,
    )

    daily_limit = parse_int(
        env("YOUTUBE_DAILY_QUOTA_LIMIT", str(DEFAULT_DAILY_QUOTA_LIMIT)),
        DEFAULT_DAILY_QUOTA_LIMIT,
    )
    warning_threshold = parse_int(
        env("YOUTUBE_QUOTA_WARNING_THRESHOLD", str(DEFAULT_QUOTA_WARNING_THRESHOLD)),
        DEFAULT_QUOTA_WARNING_THRESHOLD,
    )
    return build_quota_estimate(
        skills_count=1,
        search_max_results=PORTAL_PREVIEW_LIMIT,
        daily_limit=daily_limit,
        warning_threshold=warning_threshold,
    )


def get_portal_videos_collection():
    from pipelines.youtube.youtube_vector_index import get_mongo_client

    client = get_mongo_client()
    database_name = env("MONGO_DATABASE", env("DB_NAME", "yta"))
    return client, client[database_name]["videos"]


def annotate_existing_ingestion(
    videos: list[dict],
    *,
    skill_name: str,
) -> tuple[list[dict], int]:
    video_ids = [str(item.get("videoId") or "").strip() for item in videos if item.get("videoId")]
    if not video_ids:
        return videos, 0

    client = None
    try:
        client, videos_collection = get_portal_videos_collection()
        existing_rows = videos_collection.find(
            {
                "skill_name": skill_name,
                "videoId": {"$in": video_ids},
            },
            {"videoId": 1},
        )
        existing_ids = {
            str(row.get("videoId") or "").strip()
            for row in existing_rows
            if row.get("videoId")
        }
    finally:
        if client:
            client.close()

    annotated = []
    already_ingested_count = 0
    for item in videos:
        current = dict(item)
        already_ingested = str(current.get("videoId") or "").strip() in existing_ids
        if already_ingested:
            already_ingested_count += 1
        current["already_ingested"] = already_ingested
        annotated.append(current)

    return annotated, already_ingested_count


def embed_portal_videos(summary: dict, docs: list[dict]) -> None:
    from pipelines.youtube.youtube_vector_index import (
        DEFAULT_COLLECTION_NAME,
        embed_and_upsert_videos,
    )

    summary.setdefault("errors", [])
    try:
        embedding_summary = embed_and_upsert_videos(docs)
    except Exception as exc:
        embedding_summary = {
            "embedding_status": "failed",
            "embedding_requested": len(docs),
            "embedding_indexed": 0,
            "embedding_skipped_invalid": 0,
            "embedding_batch_size": None,
            "embedding_collection": env("QDRANT_YT_COLLECTION", DEFAULT_COLLECTION_NAME),
        }
        summary["errors"].append(f"[embedding] {exc}")

    summary.update(embedding_summary)
    summary["error_count"] = len(summary.get("errors", []))


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


def payload_matches_video_filters(
    payload: dict,
    *,
    sector: str,
    skill: str,
    proficiency_level: str | None,
    strict_skill_match: bool,
) -> bool:
    if not payload:
        return False

    if sector and str(payload.get("sector") or "") != sector:
        return False
    if strict_skill_match and skill and str(payload.get("skill_name") or "") != skill:
        return False
    if proficiency_level and str(payload.get("proficiency_level") or "") != proficiency_level:
        return False
    return True


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


@api_router.get("/public/skill-suggestions", response_model=list[SkillSuggestion])
def list_skill_suggestions(
    q: str = Query(..., min_length=2, max_length=120),
    limit: int = Query(default=6, ge=1, le=12),
):
    query_value = q.strip()
    if not query_value:
        return []

    matches: list[tuple[float, dict[str, object]]] = []
    for entry in get_skill_suggestion_index():
        score = score_label_match(query_value, str(entry.get("skill") or ""))
        if score < 3:
            continue
        matches.append((score, entry))

    matches.sort(
        key=lambda item: (
            -item[0],
            -parse_int(item[1].get("sector_count")),
            -parse_int(item[1].get("total_mapped_proficiency_count")),
            str(item[1].get("skill") or "").lower(),
        )
    )

    response: list[SkillSuggestion] = []
    for score, entry in matches[:limit]:
        sectors = [
            SkillSuggestionSector(
                sector=str(sector_item.get("sector") or ""),
                mapped_proficiency_count=parse_int(sector_item.get("mapped_proficiency_count")),
            )
            for sector_item in entry.get("sectors", [])
            if str(sector_item.get("sector") or "").strip()
        ]
        response.append(
            SkillSuggestion(
                skill=str(entry.get("skill") or ""),
                sector_count=parse_int(entry.get("sector_count")),
                total_mapped_proficiency_count=parse_int(entry.get("total_mapped_proficiency_count")),
                match_score=round(float(score), 4),
                sectors=sectors,
            )
        )

    return response


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


@api_router.post("/public/videos/preview", response_model=PublicVideoPreviewResponse)
def public_preview_videos(payload: PublicVideoPreviewRequest):
    from pipelines.youtube.youtube_ingestion_service import fetch_videos_for_preview

    sector = payload.sector.strip()
    skill = payload.skill.strip()
    proficiency_level = (payload.proficiency_level or "").strip()
    competency = (payload.competency or "").strip()
    extra_context = (payload.extra_context or "").strip()
    api_key = get_portal_youtube_api_key()
    quota_context = get_portal_quota_context()

    try:
        videos, summary = fetch_videos_for_preview(
            sector=sector,
            api_key=api_key,
            search_max_results=PORTAL_PREVIEW_LIMIT,
            search_order="relevance",
            selected_skills=[skill],
            competency=competency,
            proficiency=proficiency_level,
            requirement="",
            additional_query=extra_context,
            min_view_count=0,
            min_like_count=0,
            min_video_length=0,
            max_video_length=0,
            min_comment_count=0,
            max_video_age=0,
            query_includes={
                "sector": True,
                "skill": True,
                "competency": True,
                "requirement": False,
            },
            search_constraints={},
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Video preview search failed: {exc}",
        ) from exc

    annotated_videos, already_ingested_count = annotate_existing_ingestion(
        videos,
        skill_name=skill,
    )

    results = [
        PublicPreviewVideo(
            sector=str(item.get("sector") or sector),
            skill_name=str(item.get("skill_name") or skill),
            competency=str(item.get("competency") or competency),
            item_type=str(item.get("item_type") or ""),
            proficiency_level=str(item.get("proficiency_level") or proficiency_level),
            proficiency_description=str(item.get("proficiency_description") or ""),
            video_id=str(item.get("videoId") or ""),
            published_at=str(item.get("publishedAt") or ""),
            title=str(item.get("title") or ""),
            description=str(item.get("description") or ""),
            view_count=parse_int(item.get("viewCount")),
            like_count=parse_int(item.get("likeCount")),
            comment_count=parse_int(item.get("commentCount")),
            tags=[str(tag) for tag in item.get("tags", []) if str(tag).strip()],
            duration=str(item.get("duration") or ""),
            channel_title=str(item.get("channelTitle") or ""),
            thumbnail_url=str(item.get("thumbnailUrl") or ""),
            already_ingested=bool(item.get("already_ingested")),
        )
        for item in annotated_videos
        if str(item.get("videoId") or "").strip()
    ]

    quota_message = None
    if summary.get("quota_exceeded"):
        quota_message = (
            "YouTube API daily credit limit was reached while searching for more videos. "
            "Try again later."
        )

    if summary.get("quota_exceeded") and not results:
        return JSONResponse(
            status_code=429,
            content={
                "detail": quota_message,
                "query": str(summary.get("constraints", {}).get("query") or ""),
                "count": 0,
                "already_ingested_count": 0,
                "quota_exceeded": True,
                "quota_message": quota_message,
                "quota": quota_context,
                "results": [],
            },
        )

    return PublicVideoPreviewResponse(
        query=str(summary.get("constraints", {}).get("query") or ""),
        count=len(results),
        already_ingested_count=already_ingested_count,
        quota_exceeded=bool(summary.get("quota_exceeded")),
        quota_message=quota_message,
        quota=quota_context,
        results=results,
    )


@api_router.post("/public/videos/ingest", response_model=PublicVideoIngestResponse)
def public_ingest_videos(payload: PublicVideoIngestRequest):
    from pipelines.youtube.youtube_ingestion_service import upsert_selected_videos

    client = None
    summary: dict = {
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "error_count": 0,
        "errors": [],
    }
    touched_docs: list[dict] = []

    try:
        client, videos_collection = get_portal_videos_collection()
        summary = upsert_selected_videos(
            videos_collection,
            [video.model_dump() for video in payload.videos],
            touched_docs=touched_docs,
        )
        embed_portal_videos(summary, touched_docs)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Video ingestion failed: {exc}",
        ) from exc
    finally:
        if client:
            client.close()

    message = "Selected videos ingested successfully."
    if summary.get("embedding_status") == "completed" and summary.get("embedding_indexed", 0) > 0:
        message = "Selected videos ingested and indexed successfully."
    elif summary.get("embedding_status") == "skipped":
        message = "Selected videos ingested. No valid videos required indexing."
    elif summary.get("embedding_status") == "failed":
        message = "Selected videos ingested, but embedding failed."
    elif summary.get("error_count", 0) > 0:
        message = "Selected videos ingested with errors."

    return PublicVideoIngestResponse(
        message=message,
        count=len(payload.videos),
        inserted=parse_int(summary.get("inserted")),
        updated=parse_int(summary.get("updated")),
        unchanged=parse_int(summary.get("unchanged")),
        error_count=parse_int(summary.get("error_count")),
        embedding_status=str(summary.get("embedding_status") or "skipped"),
        embedding_indexed=parse_int(summary.get("embedding_indexed")),
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

    try:
        model = get_embedding_model()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    query_vector = model.encode(
        query_text,
        normalize_embeddings=True,
    ).tolist()

    yt_collection = env("QDRANT_YT_COLLECTION", DEFAULT_YT_COLLECTION)
    qdrant = get_qdrant_client()

    # ------------------------------------------------------------------
    # Path A: Semantic search with sector filter (+ filter relaxation)
    # ------------------------------------------------------------------
    active_proficiency = proficiency_level
    active_strict = payload.strict_skill_match

    def run_semantic(prof: str | None, strict: bool):
        return qdrant.search(
            collection_name=yt_collection,
            query_vector=query_vector,
            query_filter=build_video_filter(sector, skill, prof, strict),
            limit=SEMANTIC_CANDIDATES,
            with_payload=True,
            with_vectors=False,
        )

    try:
        semantic_hits = run_semantic(active_proficiency, active_strict)
        if not semantic_hits and active_proficiency:
            active_proficiency = None
            semantic_hits = run_semantic(active_proficiency, active_strict)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Video recommendation search failed: {exc}",
        ) from exc

    semantic_ids = [str(hit.id) for hit in semantic_hits]
    semantic_payloads = {str(hit.id): hit.payload or {} for hit in semantic_hits}

    # ------------------------------------------------------------------
    # Path B: BM25 lexical search (skill name → YT title+tags)
    # ------------------------------------------------------------------
    bm25, bm25_point_ids, bm25_payloads = get_bm25_index()
    skill_tokens = skill.lower().split()
    bm25_scores = bm25.get_scores(skill_tokens)
    top_bm25_indices = np.argsort(bm25_scores)[::-1][:BM25_CANDIDATES]
    bm25_ranked_ids = [
        bm25_point_ids[i]
        for i in top_bm25_indices
        if bm25_scores[i] > 0
        and payload_matches_video_filters(
            bm25_payloads.get(bm25_point_ids[i], {}),
            sector=sector,
            skill=skill,
            proficiency_level=active_proficiency,
            strict_skill_match=active_strict,
        )
    ]

    # ------------------------------------------------------------------
    # RRF Fusion
    # ------------------------------------------------------------------
    rrf_scores = reciprocal_rank_fusion(semantic_ids, bm25_ranked_ids)

    all_payloads: dict[str, dict] = {}
    all_payloads.update(bm25_payloads)
    all_payloads.update(semantic_payloads)
    all_payloads = {
        pid: item
        for pid, item in all_payloads.items()
        if pid in rrf_scores
        and payload_matches_video_filters(
            item,
            sector=sector,
            skill=skill,
            proficiency_level=active_proficiency,
            strict_skill_match=active_strict,
        )
    }
    rrf_scores = {pid: score for pid, score in rrf_scores.items() if pid in all_payloads}

    # ------------------------------------------------------------------
    # Metadata boosts (sector + proficiency alignment)
    # ------------------------------------------------------------------
    boosted_scores = apply_metadata_boosts(
        rrf_scores,
        all_payloads,
        skill_category=sector,
        skill_proficiency=active_proficiency or "",
    )

    applied_filters = {"sector": sector}
    if active_strict:
        applied_filters["skill"] = skill
    if active_proficiency:
        applied_filters["proficiency_level"] = active_proficiency

    # ------------------------------------------------------------------
    # Cross-encoder reranking
    # ------------------------------------------------------------------
    sorted_candidates = sorted(boosted_scores.items(), key=lambda x: x[1], reverse=True)
    rerank_candidates = sorted_candidates[:RERANK_TOP_N]

    if not rerank_candidates:
        return PublicRecommendResponse(
            query=query_text,
            applied_filters=applied_filters,
            count=0,
            results=[],
        )

    try:
        reranker = get_reranker_model()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    sf_text = f"Skill: {skill}. Sector: {sector}. Competency: {competency}."

    rerank_pairs = []
    rerank_pids = []
    rerank_rrf_scores = []
    for pid, rrf_score in rerank_candidates:
        yt_text = build_yt_rerank_text(all_payloads.get(pid, {}))
        rerank_pairs.append((sf_text, yt_text))
        rerank_pids.append(pid)
        rerank_rrf_scores.append(rrf_score)

    reranker_scores = reranker.predict(rerank_pairs)
    ranked_indices = np.argsort(reranker_scores)[::-1]
    final_indices = ranked_indices[: payload.top_k]

    # ------------------------------------------------------------------
    # Build response
    # ------------------------------------------------------------------
    results: list[PublicRecommendedVideo] = []
    for idx in final_indices:
        pid = rerank_pids[idx]
        item = all_payloads.get(pid, {})
        results.append(
            PublicRecommendedVideo(
                score=float(reranker_scores[idx]),
                rrf_score=float(rerank_rrf_scores[idx]),
                reranker_score=float(reranker_scores[idx]),
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

    return PublicRecommendResponse(
        query=query_text,
        applied_filters=applied_filters,
        count=len(results),
        results=results,
    )
