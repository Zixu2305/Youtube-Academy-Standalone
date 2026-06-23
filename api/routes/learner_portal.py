from __future__ import annotations

import json
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import numpy as np
import mysql.connector
from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.http import models

from api.routes.job_role_lookup_utils import (
    build_job_role_skill_groups,
    build_work_function_groups,
)
from api.routes.recommend import (
    get_cached_query_embedding,
    get_bm25_index,
    get_reranker_model,
    reciprocal_rank_fusion,
    apply_metadata_boosts,
    build_yt_rerank_text,
    payload_proficiency_matches,
    SEMANTIC_CANDIDATES,
    BM25_CANDIDATES,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

ACADEMY_FRONTEND_DIR = ROOT_DIR / "api" / "frontend" / "academy"

DEFAULT_YT_COLLECTION = "youtube_videos__bge_base__768"
PORTAL_PREVIEW_LIMIT = 8
PORTAL_DIRECT_SEARCH_LIMIT = 80
PORTAL_RETRIEVAL_CACHE_SIZE = 512
PORTAL_DEFAULT_RERANK_CANDIDATES = 8
PORTAL_RERANK_CACHE_SIZE = 512
PORTAL_YOUTUBE_API_KEY_ENV_NAMES = (
    "YOUTUBE_API_KEY",
    "GOOGLE_API_KEY",
    "YOUTUBE_DATA_API_KEY",
)
MAPPING_SUGGESTION_MIN_SCORE = 2.0

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


class JobRoleSummary(BaseModel):
    job_role_id: int
    sector: str
    track: str
    job_role_name: str
    skill_requirement_count: int
    critical_work_function_count: int
    has_skill_requirements: bool


class JobRoleCriticalWorkFunction(BaseModel):
    name: str
    key_tasks: list[str]


class JobRoleSkillDetail(BaseModel):
    skill_title: str
    skill_type: str
    tsc_ccs_codes: list[str]
    proficiency_level: str
    proficiency_description: str
    knowledge_items: list[str]
    ability_items: list[str]


class JobRoleDetailResponse(BaseModel):
    job_role_id: int
    sector: str
    track: str
    job_role_name: str
    role_description: str
    performance_expectation: str
    critical_work_functions: list[JobRoleCriticalWorkFunction]
    skills: list[JobRoleSkillDetail]


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
    user_votes: int = 0


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


class PublicDirectVideoSearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=240)
    max_results: int = Field(default=PORTAL_PREVIEW_LIMIT, ge=1, le=PORTAL_DIRECT_SEARCH_LIMIT)
    order: str = Field(default="relevance", max_length=30)
    source: Literal["youtube", "library"] = "youtube"
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)


class PublicPreviewVideo(BaseModel):
    source: str = "youtube"
    score: float | None = None
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
    quota: dict[str, int | str] | None = None
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
    search_query: str = ""


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
    embedding_requested: int = 0
    embedding_indexed: int
    embedding_skipped_invalid: int = 0
    pending_review_count: int = 0
    approved_ingested_count: int = 0


class PublicMappingVideo(BaseModel):
    video_id: str = ""
    title: str = ""
    description: str = ""
    channel_title: str = ""
    tags: list[str] = Field(default_factory=list)


class PublicVideoMappingSuggestRequest(BaseModel):
    video: PublicMappingVideo
    search_query: str | None = None
    use_ai: bool = False


class PublicVideoMappingSuggestion(BaseModel):
    sector: str
    skill: str
    proficiency_level: str
    competency: str
    item_type: str
    proficiency_description: str = ""
    confidence: float = 0.0
    reason: str = ""


class PublicVideoMappingSuggestResponse(BaseModel):
    suggestion: PublicVideoMappingSuggestion
    alternatives: list[PublicVideoMappingSuggestion] = Field(default_factory=list)


class VideoCompetencyMapping(BaseModel):
    sector: str = Field(..., min_length=1)
    skill: str = Field(..., min_length=1)
    proficiency_level: str = Field(..., min_length=1)
    competency: str = Field(..., min_length=1)
    item_type: str = Field(..., min_length=1)
    proficiency_description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    source: Literal["ai", "admin", "fallback"] = "admin"


class VideoMappingReviewRequest(BaseModel):
    video_id: str
    review_status: str
    title: str = ""
    channel_title: str = ""
    thumbnail_url: str = ""
    search_query: str = ""
    suggestion_status: str = ""
    suggestion_error: str = ""
    suggested_mappings: list[VideoCompetencyMapping] = Field(default_factory=list)
    approved_mappings: list[VideoCompetencyMapping] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    reviewed_at: str = ""


class VideoMappingReviewListResponse(BaseModel):
    count: int
    requests: list[VideoMappingReviewRequest]


class AdminMappingUpdateRequest(BaseModel):
    mappings: list[VideoCompetencyMapping] = Field(..., min_length=1, max_length=20)
    reviewer: str = "admin"


class AdminRejectMappingRequest(BaseModel):
    reviewer: str = "admin"
    reason: str = ""


class AdminMappingActionResponse(BaseModel):
    message: str
    video_id: str
    review_status: str
    approved_mappings: list[VideoCompetencyMapping] = Field(default_factory=list)
    embedding_status: str = "skipped"
    embedding_indexed: int = 0
    approved_deleted_count: int = 0
    qdrant_delete_status: str = "skipped"


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


def score_text_overlap(query: str, label: str) -> float:
    query_tokens = set(tokenize_search_text(query))
    label_tokens = set(tokenize_search_text(label))
    if not query_tokens or not label_tokens:
        return 0.0
    overlap = query_tokens & label_tokens
    if not overlap:
        return 0.0
    return len(overlap) / max(1, len(query_tokens))


def expand_mapping_query_text(query_text: str) -> str:
    tokens = set(tokenize_search_text(query_text))
    expansions: list[str] = []
    if "ai" in tokens:
        expansions.extend(["artificial intelligence", "machine learning"])
    if "ethic" in tokens or "ethics" in tokens or "ethical" in tokens:
        expansions.extend(["ethics", "ethical", "responsible", "governance", "risk"])
    if "safety" in tokens or "safe" in tokens:
        expansions.extend(["security", "risk", "governance"])
    if "data" in tokens:
        expansions.extend(["data governance", "responsible data use"])
    return " ".join([query_text, *expansions]).strip()


def build_mapping_search_tokens(query_text: str, limit: int = 10) -> list[str]:
    expanded = expand_mapping_query_text(query_text)
    stop_tokens = {
        "and", "for", "the", "with", "from", "this", "that", "what", "when", "video",
        "youtube", "tutorial", "learn", "learning", "news", "daily",
    }
    tokens: list[str] = []
    for token in tokenize_search_text(expanded):
        if len(token) < 3 and token != "ai":
            continue
        if token in stop_tokens:
            continue
        normalized_tokens = ["artificial", "intelligence"] if token == "ai" else [token]
        for normalized in normalized_tokens:
            if normalized not in tokens:
                tokens.append(normalized)
        if len(tokens) >= limit:
            break
    return tokens


def score_mapping_candidate_row(query_text: str, row: dict[str, object]) -> float:
    expanded_query = expand_mapping_query_text(query_text)
    skill = str(row.get("skill") or "")
    sector = str(row.get("sector") or "")
    item_text = str(row.get("item_text") or "")
    description = str(row.get("proficiency_description") or "")
    combined_competency = f"{item_text}. {description}"

    score = (
        score_label_match(expanded_query, item_text) * 3.5
        + score_text_overlap(expanded_query, item_text) * 8.0
        + score_label_match(expanded_query, description) * 1.3
        + score_text_overlap(expanded_query, description) * 3.2
        + score_label_match(expanded_query, skill) * 1.4
        + score_text_overlap(expanded_query, skill) * 2.4
        + score_label_match(expanded_query, sector) * 0.4
        + score_text_overlap(expanded_query, combined_competency) * 2.0
    )

    query_tokens = set(tokenize_search_text(expanded_query))
    candidate_tokens = set(tokenize_search_text(" ".join([sector, skill, item_text, description])))
    if {"ethics", "ethical", "responsible", "governance"} & query_tokens and candidate_tokens & {"ethics", "ethical", "responsible", "governance"}:
        score += 4.0
    if {"artificial", "intelligence", "machine", "learning"} & query_tokens and candidate_tokens & {"artificial", "intelligence", "machine", "learning", "ai"}:
        score += 3.0
    if (
        {"artificial", "intelligence"} <= query_tokens
        and {"ethics", "governance"} & query_tokens
        and sector.strip().lower() == "infocomm technology"
    ):
        score += 8.0
    broad_ai_ethics_query = {"artificial", "intelligence"} <= query_tokens and {"ethics", "ethical", "governance", "responsible"} & query_tokens
    if "financial" in candidate_tokens and not (query_tokens & {"financial", "finance", "banking", "payment", "insurance"}):
        score -= 14.0 if broad_ai_ethics_query else 5.0
    if {"accountancy", "audit", "auditor", "accounting"} & candidate_tokens and not (
        query_tokens & {"accountancy", "audit", "auditor", "accounting", "finance", "financial"}
    ):
        score -= 8.0 if broad_ai_ethics_query else 4.0
    if "security" in candidate_tokens and not (query_tokens & {"security", "secure", "cybersecurity", "privacy", "safety", "risk"}):
        score -= 2.0
    return score


def get_mapping_competency_candidate_rows(query_text: str, limit: int = 700) -> list[dict[str, object]]:
    tokens = build_mapping_search_tokens(query_text)
    if not tokens:
        return []

    query_tokens = set(tokenize_search_text(expand_mapping_query_text(query_text)))
    preferred_skill_filters: list[str] = []
    if {"artificial", "intelligence"} <= query_tokens and {"ethics", "governance"} & query_tokens:
        preferred_skill_filters.extend([
            "%Artificial Intelligence Ethics and Governance%",
            "%Responsible AI and Generative AI Practices%",
        ])
    elif {"artificial", "intelligence"} <= query_tokens and {"responsible", "ethical", "ethics"} & query_tokens:
        preferred_skill_filters.append("%Responsible AI and Generative AI Practices%")

    preferred_rows: list[dict[str, object]] = []
    if preferred_skill_filters:
        preferred_where = " OR ".join(["m.source_skill_title LIKE %s" for _ in preferred_skill_filters])
        preferred_sql = f"""
        SELECT
          m.sector_name_raw AS sector,
          m.source_skill_title AS skill,
          m.proficiency_level AS proficiency_level,
          COALESCE(sl.proficiency_description, '') AS proficiency_description,
          sci.item_type AS item_type,
          sci.item_text AS item_text
        FROM map_sf_to_cat_skill m
        JOIN sf_competency_item sci
          ON sci.sf_skill_id = m.sf_skill_id
         AND sci.proficiency_level = m.proficiency_level
        LEFT JOIN sf_skill_level sl
          ON sl.sf_skill_id = m.sf_skill_id
         AND sl.proficiency_level = m.proficiency_level
        WHERE ({preferred_where})
        LIMIT 240;
        """
        conn = get_mysql_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(preferred_sql, preferred_skill_filters)
            preferred_rows = cur.fetchall()
        finally:
            cur.close()
            conn.close()

    rows: list[dict[str, object]] = []
    if not preferred_rows:
        where_clauses = []
        params: list[object] = []
        for token in tokens:
            like_value = f"%{token}%"
            where_clauses.append(
                "("
                "m.sector_name_raw LIKE %s OR "
                "m.source_skill_title LIKE %s OR "
                "sci.item_text LIKE %s OR "
                "sl.proficiency_description LIKE %s"
                ")"
            )
            params.extend([like_value, like_value, like_value, like_value])

        sql = f"""
        SELECT
          m.sector_name_raw AS sector,
          m.source_skill_title AS skill,
          m.proficiency_level AS proficiency_level,
          COALESCE(sl.proficiency_description, '') AS proficiency_description,
          sci.item_type AS item_type,
          sci.item_text AS item_text
        FROM map_sf_to_cat_skill m
        JOIN sf_competency_item sci
          ON sci.sf_skill_id = m.sf_skill_id
         AND sci.proficiency_level = m.proficiency_level
        LEFT JOIN sf_skill_level sl
          ON sl.sf_skill_id = m.sf_skill_id
         AND sl.proficiency_level = m.proficiency_level
        WHERE m.source_skill_title IS NOT NULL
          AND m.source_skill_title <> ''
          AND m.sector_name_raw IS NOT NULL
          AND m.sector_name_raw <> ''
          AND sci.item_text IS NOT NULL
          AND sci.item_text <> ''
          AND ({' OR '.join(where_clauses)})
        ORDER BY
          CASE
            WHEN m.source_skill_title LIKE '%Artificial Intelligence Ethics%' THEN 0
            WHEN m.source_skill_title LIKE '%Responsible AI%' THEN 1
            WHEN m.source_skill_title LIKE '%Generative AI%' THEN 2
            ELSE 3
          END,
          m.sector_name_raw,
          m.source_skill_title,
          m.proficiency_level
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

    scored_rows: list[tuple[float, dict[str, object]]] = []
    for row in [*preferred_rows, *rows]:
        normalized = {
            "sector": str(row.get("sector") or "").strip(),
            "skill": str(row.get("skill") or "").strip(),
            "proficiency_level": str(row.get("proficiency_level") or "").strip(),
            "proficiency_description": str(row.get("proficiency_description") or "").strip(),
            "item_type": str(row.get("item_type") or "").strip().lower(),
            "item_text": str(row.get("item_text") or "").strip(),
        }
        score = score_mapping_candidate_row(query_text, normalized)
        if score <= 0:
            continue
        scored_rows.append((score, normalized))

    scored_rows.sort(
        key=lambda item: (
            -item[0],
            str(item[1].get("sector") or "").lower(),
            str(item[1].get("skill") or "").lower(),
            str(item[1].get("proficiency_level") or "").lower(),
        )
    )
    return [{**row, "row_score": round(score, 4)} for score, row in scored_rows]


def get_mapping_candidate_headers(query_text: str, limit: int = 80) -> list[dict[str, object]]:
    tokens = build_mapping_search_tokens(query_text, limit=8)
    if not tokens:
        return []

    where_clauses = []
    params: list[object] = []
    for token in tokens:
        like_value = f"%{token}%"
        where_clauses.append("(m.sector_name_raw LIKE %s OR m.source_skill_title LIKE %s)")
        params.extend([like_value, like_value])

    sql = f"""
    SELECT
      m.source_skill_title AS skill,
      m.sector_name_raw AS sector,
      COUNT(DISTINCT CONCAT(m.sf_skill_id, ':', m.proficiency_level)) AS mapped_proficiency_count
    FROM map_sf_to_cat_skill m
    WHERE m.source_skill_title IS NOT NULL
      AND m.source_skill_title <> ''
      AND m.sector_name_raw IS NOT NULL
      AND m.sector_name_raw <> ''
      AND ({' OR '.join(where_clauses)})
    GROUP BY m.source_skill_title, m.sector_name_raw
    ORDER BY mapped_proficiency_count DESC, m.sector_name_raw, m.source_skill_title
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
        {
            "sector": str(row.get("sector") or "").strip(),
            "skill": str(row.get("skill") or "").strip(),
            "mapped_skill_count": parse_int(row.get("mapped_proficiency_count")),
        }
        for row in rows
        if str(row.get("sector") or "").strip() and str(row.get("skill") or "").strip()
    ]


def get_mapping_candidates_for_video(
    *,
    video: PublicMappingVideo,
    search_query: str,
    limit: int = 4,
) -> list[dict[str, object]]:
    query_text = " ".join(
        [
            search_query,
            video.title,
            video.description,
            video.channel_title,
            " ".join(video.tags[:12]),
        ]
    ).strip()
    base_search_text = search_query.strip() or query_text
    scored_entries: list[tuple[float, dict[str, object]]] = []
    competency_rows = get_mapping_competency_candidate_rows(query_text)
    header_entries = get_mapping_candidate_headers(base_search_text)
    for row in competency_rows:
        entry = {
            "sector": row["sector"],
            "skill": row["skill"],
            "mapped_skill_count": 1,
            "competency_rows": [row],
        }
        scored_entries.append((float(row.get("row_score") or 0), entry))

    for entry in header_entries:
        skill = str(entry.get("skill") or "")
        skill_score = score_label_match(expand_mapping_query_text(base_search_text), skill)
        sector = str(entry.get("sector") or "")
        sector_score = score_label_match(expand_mapping_query_text(base_search_text), sector)
        mapped_count = parse_int(entry.get("mapped_skill_count"))
        score = sector_score * 2.4 + skill_score * 2.8 + min(1.0, mapped_count / 8) * 0.25
        if score < MAPPING_SUGGESTION_MIN_SCORE:
            continue
        scored_entries.append((score, entry))

    grouped_entries: dict[tuple[str, str], tuple[float, dict[str, object]]] = {}
    for score, entry in scored_entries:
        key = (
            str(entry.get("sector") or "").strip().lower(),
            str(entry.get("skill") or "").strip().lower(),
        )
        if not key[0] or not key[1]:
            continue
        existing = grouped_entries.get(key)
        if existing:
            existing_score, existing_entry = existing
            existing_rows = list(existing_entry.get("competency_rows") or [])
            existing_rows.extend(entry.get("competency_rows") or [])
            existing_entry["competency_rows"] = existing_rows
            existing_entry["mapped_skill_count"] = max(
                parse_int(existing_entry.get("mapped_skill_count")),
                parse_int(entry.get("mapped_skill_count")),
            )
            grouped_entries[key] = (max(existing_score, score), existing_entry)
        else:
            grouped_entries[key] = (score, dict(entry))

    candidates = []
    sorted_entries = list(grouped_entries.values())
    sorted_entries.sort(
        key=lambda item: (
            -item[0],
            -parse_int(item[1].get("mapped_skill_count")),
            str(item[1].get("sector") or "").lower(),
            str(item[1].get("skill") or "").lower(),
        )
    )

    for score, entry in sorted_entries[:limit]:
        try:
            skill_map = get_skill_map(
                sector=str(entry.get("sector") or ""),
                skill=str(entry.get("skill") or ""),
            )
        except HTTPException:
            continue

        rows_by_level: dict[str, list[dict[str, object]]] = {}
        for row in entry.get("competency_rows") or []:
            rows_by_level.setdefault(str(row.get("proficiency_level") or ""), []).append(row)

        mappings = []
        for mapping in skill_map.mappings:
            ranked_competencies: list[tuple[float, dict[str, str]]] = []
            for row in rows_by_level.get(mapping.proficiency_level, []):
                ranked_competencies.append(
                    (
                        float(row.get("row_score") or 0),
                        {
                            "item_type": str(row.get("item_type") or ""),
                            "item_text": str(row.get("item_text") or ""),
                        },
                    )
                )
            if not ranked_competencies:
                ranked_competencies = [
                    (
                        score_mapping_candidate_row(
                            query_text,
                            {
                                "sector": entry.get("sector"),
                                "skill": entry.get("skill"),
                                "proficiency_level": mapping.proficiency_level,
                                "proficiency_description": mapping.proficiency_description,
                                "item_type": "knowledge",
                                "item_text": item,
                            },
                        ),
                        {"item_type": "knowledge", "item_text": item},
                    )
                    for item in mapping.knowledge_items
                ] + [
                    (
                        score_mapping_candidate_row(
                            query_text,
                            {
                                "sector": entry.get("sector"),
                                "skill": entry.get("skill"),
                                "proficiency_level": mapping.proficiency_level,
                                "proficiency_description": mapping.proficiency_description,
                                "item_type": "ability",
                                "item_text": item,
                            },
                        ),
                        {"item_type": "ability", "item_text": item},
                    )
                    for item in mapping.ability_items
                ]

            ranked_competencies.sort(key=lambda item: -item[0])
            competencies = [
                competency
                for competency_score, competency in ranked_competencies[:6]
                if competency_score > 0 or rows_by_level.get(mapping.proficiency_level)
            ]
            if not competencies:
                continue
            mappings.append({
                "proficiency_level": mapping.proficiency_level,
                "proficiency_description": mapping.proficiency_description,
                "competencies": competencies,
            })
            if len(mappings) >= 4:
                break
        if not mappings:
            continue

        candidates.append({
            "candidate_id": len(candidates),
            "sector": entry["sector"],
            "skill": entry["skill"],
            "score": round(float(score), 4),
            "mappings": mappings,
        })
    return candidates


def _parse_mapping_suggestion(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        raise


def _fallback_mapping_suggestion(
    candidates: list[dict[str, object]],
    query_text: str = "",
) -> PublicVideoMappingSuggestion | None:
    best_match: tuple[float, dict[str, object], dict[str, object], dict[str, object]] | None = None
    for candidate in candidates:
        mappings = candidate.get("mappings", [])
        for mapping in mappings:
            competencies = mapping.get("competencies", [])
            for competency in competencies:
                competency_text = str(competency.get("item_text") or "")
                score = (
                    score_label_match(query_text, competency_text)
                    + score_text_overlap(query_text, competency_text) * 3.2
                    + score_text_overlap(query_text, str(mapping.get("proficiency_description") or "")) * 0.7
                )
                if best_match is None or score > best_match[0]:
                    best_match = (score, candidate, mapping, competency)

    if best_match is not None:
        score, candidate, mapping, competency = best_match
        confidence = 0.42 if score > 0 else 0.32
        return PublicVideoMappingSuggestion(
            sector=str(candidate.get("sector") or ""),
            skill=str(candidate.get("skill") or ""),
            proficiency_level=str(mapping.get("proficiency_level") or ""),
            proficiency_description=str(mapping.get("proficiency_description") or ""),
            item_type=str(competency.get("item_type") or ""),
            competency=f"{competency.get('item_type')}: {competency.get('item_text')}",
            confidence=confidence,
            reason="Suggested from the closest seeded SkillsFuture mapping.",
        )
    return None


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


def get_video_votes_collection():
    from pipelines.youtube.youtube_vector_index import get_mongo_client

    client = get_mongo_client()
    database_name = env("MONGO_DATABASE", env("DB_NAME", "yta"))
    return client, client[database_name]["video_votes"]


def utc_now() -> datetime:
    return datetime.utcnow()


def to_iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    return str(value or "")


def normalize_video_id(value: str) -> str:
    video_id = str(value or "").strip()
    if not video_id:
        raise HTTPException(status_code=400, detail="Video ID is required.")
    return video_id


def normalize_mapping_dict(mapping: VideoCompetencyMapping | dict[str, Any]) -> dict[str, Any]:
    raw = mapping.model_dump() if isinstance(mapping, VideoCompetencyMapping) else dict(mapping)
    item_type = str(raw.get("item_type") or "").strip().lower()
    competency = str(raw.get("competency") or "").strip()
    source = str(raw.get("source") or "admin").strip()
    if source not in {"ai", "admin", "fallback"}:
        source = "admin"
    if competency and ":" in competency:
        prefix, text = competency.split(":", 1)
        if not item_type:
            item_type = prefix.strip().lower()
        competency = f"{item_type or prefix.strip().lower()}: {text.strip()}"
    return {
        "sector": str(raw.get("sector") or "").strip(),
        "skill": str(raw.get("skill") or raw.get("skill_name") or "").strip(),
        "proficiency_level": str(raw.get("proficiency_level") or raw.get("proficiency") or "").strip(),
        "competency": competency,
        "item_type": item_type,
        "proficiency_description": str(raw.get("proficiency_description") or "").strip(),
        "confidence": max(0.0, min(1.0, float(raw.get("confidence") or 0.0))),
        "reason": str(raw.get("reason") or "").strip(),
        "source": source,
    }


def mapping_key(mapping: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(mapping.get("sector") or "").lower(),
        str(mapping.get("skill") or "").lower(),
        str(mapping.get("proficiency_level") or "").lower(),
        str(mapping.get("item_type") or "").lower(),
        str(mapping.get("competency") or "").lower(),
    )


def dedupe_mappings(mappings: list[VideoCompetencyMapping | dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for mapping in mappings:
        normalized = normalize_mapping_dict(mapping)
        if not all(
            normalized.get(field)
            for field in ("sector", "skill", "proficiency_level", "competency", "item_type")
        ):
            continue
        key = mapping_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def validate_seeded_mappings(mappings: list[VideoCompetencyMapping | dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_mappings = dedupe_mappings(mappings)
    for mapping in normalized_mappings:
        skill_map = get_skill_map(sector=mapping["sector"], skill=mapping["skill"])
        selected_level = next(
            (
                entry
                for entry in skill_map.mappings
                if entry.proficiency_level == mapping["proficiency_level"]
            ),
            None,
        )
        if not selected_level:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Mapping does not match a seeded proficiency level: "
                    f"{mapping['sector']} / {mapping['skill']} / {mapping['proficiency_level']}"
                ),
            )

        item_text = mapping["competency"].split(":", 1)[1].strip() if ":" in mapping["competency"] else mapping["competency"]
        allowed_items = (
            selected_level.knowledge_items
            if mapping["item_type"] == "knowledge"
            else selected_level.ability_items
            if mapping["item_type"] == "ability"
            else []
        )
        if item_text not in allowed_items:
            raise HTTPException(
                status_code=400,
                detail=f"Mapping competency is not seeded for this skill/level: {mapping['competency']}",
            )
        if not mapping["proficiency_description"]:
            mapping["proficiency_description"] = selected_level.proficiency_description
    return normalized_mappings


def make_mapping_review_response(doc: dict[str, Any]) -> VideoMappingReviewRequest:
    review = doc.get("mapping_review") or {}
    suggested_mappings = review.get("suggested_mappings") or []
    suggestion_status = str(review.get("suggestion_status") or "").strip()
    if not suggestion_status:
        suggestion_status = "ready" if suggested_mappings else "none"
    return VideoMappingReviewRequest(
        video_id=str(doc.get("videoId") or ""),
        review_status=str(doc.get("review_status") or review.get("status") or "pending"),
        title=str(doc.get("title") or ""),
        channel_title=str(doc.get("channelTitle") or ""),
        thumbnail_url=str(doc.get("thumbnailUrl") or ""),
        search_query=str(review.get("search_query") or ""),
        suggestion_status=suggestion_status,
        suggestion_error=str(review.get("suggestion_error") or ""),
        suggested_mappings=[
            VideoCompetencyMapping(**normalize_mapping_dict(item))
            for item in suggested_mappings
        ],
        approved_mappings=[
            VideoCompetencyMapping(**normalize_mapping_dict(item))
            for item in (review.get("approved_mappings") or [])
        ],
        created_at=to_iso(review.get("created_at") or doc.get("ingested_timing")),
        updated_at=to_iso(review.get("updated_at")),
        reviewed_at=to_iso(review.get("reviewed_at")),
    )


def build_mapping_suggestions_for_video(
    video: dict[str, Any],
    *,
    search_query: str = "",
    use_ai: bool = True,
) -> list[dict[str, Any]]:
    request = PublicVideoMappingSuggestRequest(
        video=PublicMappingVideo(
            video_id=str(video.get("videoId") or video.get("video_id") or ""),
            title=str(video.get("title") or ""),
            description=str(video.get("description") or ""),
            channel_title=str(video.get("channelTitle") or video.get("channel_title") or ""),
            tags=[str(tag) for tag in (video.get("tags") or [])],
        ),
        search_query=search_query,
        use_ai=use_ai,
    )
    try:
        response = public_suggest_video_mapping(request)
    except HTTPException:
        return []
    suggestion = response.suggestion.model_dump()
    suggestion["source"] = "ai" if use_ai and suggestion.get("confidence", 0) > 0 else "fallback"
    return dedupe_mappings([suggestion])


def prepare_review_doc(
    video: dict[str, Any],
    suggested_mappings: list[dict[str, Any]],
    search_query: str,
    *,
    suggestion_status: str = "ready",
    suggestion_job_id: str = "",
) -> dict[str, Any]:
    now = utc_now()
    review = {
        "status": "pending",
        "is_request": True,
        "search_query": search_query,
        "suggested_mappings": suggested_mappings,
        "approved_mappings": [],
        "suggestion_status": suggestion_status,
        "suggestion_error": "",
        "suggestion_job_id": suggestion_job_id,
        "created_at": now,
        "updated_at": now,
        "reviewed_at": None,
        "reviewer": "",
        "rejection_reason": "",
    }
    return {
        "sector": "",
        "skill_name": "",
        "competency": "",
        "item_type": "",
        "proficiency_level": "",
        "proficiency_description": "",
        "videoId": str(video.get("videoId") or "").strip(),
        "publishedAt": str(video.get("publishedAt") or ""),
        "title": str(video.get("title") or ""),
        "description": str(video.get("description") or ""),
        "viewCount": parse_int(video.get("viewCount")),
        "likeCount": parse_int(video.get("likeCount")),
        "commentCount": parse_int(video.get("commentCount")),
        "tags": [str(tag) for tag in (video.get("tags") or [])],
        "duration": str(video.get("duration") or ""),
        "channelTitle": str(video.get("channelTitle") or ""),
        "thumbnailUrl": str(video.get("thumbnailUrl") or ""),
        "ingested_timing": now,
        "review_status": "pending",
        "suggested_mappings": suggested_mappings,
        "approved_mappings": [],
        "mapping_review": review,
    }


def populate_pending_review_suggestions(
    video_id: str,
    video: dict[str, Any],
    search_query: str,
    suggestion_job_id: str,
) -> None:
    client = None
    try:
        client, videos_collection = get_portal_videos_collection()
        running_filter = {
            "videoId": video_id,
            "review_status": "pending",
            "mapping_review.is_request": True,
            "mapping_review.suggestion_job_id": suggestion_job_id,
            "mapping_review.suggestion_status": {"$in": ["queued", "running"]},
        }
        result = videos_collection.update_one(
            running_filter,
            {
                "$set": {
                    "mapping_review.suggestion_status": "running",
                    "mapping_review.suggestion_error": "",
                    "mapping_review.updated_at": utc_now(),
                }
            },
        )
        if result.matched_count == 0:
            return

        suggested_mappings = build_mapping_suggestions_for_video(
            video,
            search_query=search_query,
            use_ai=True,
        )
        videos_collection.update_one(
            running_filter,
            {
                "$set": {
                    "suggested_mappings": suggested_mappings,
                    "mapping_review.suggested_mappings": suggested_mappings,
                    "mapping_review.suggestion_status": "ready",
                    "mapping_review.suggestion_error": "",
                    "mapping_review.updated_at": utc_now(),
                }
            },
        )
    except Exception as exc:
        try:
            if client is None:
                client, videos_collection = get_portal_videos_collection()
            videos_collection.update_one(
                {
                    "videoId": video_id,
                    "review_status": "pending",
                    "mapping_review.is_request": True,
                    "mapping_review.suggestion_job_id": suggestion_job_id,
                    "mapping_review.suggestion_status": {"$in": ["queued", "running"]},
                },
                {
                    "$set": {
                        "mapping_review.suggestion_status": "failed",
                        "mapping_review.suggestion_error": str(exc),
                        "mapping_review.updated_at": utc_now(),
                    }
                },
            )
        except Exception:
            pass
    finally:
        if client:
            client.close()


def mapping_to_video_doc(base_video: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    doc = dict(base_video)
    doc.update(
        {
            "sector": mapping["sector"],
            "skill_name": mapping["skill"],
            "competency": mapping["competency"],
            "item_type": mapping["item_type"],
            "proficiency_level": mapping["proficiency_level"],
            "proficiency_description": mapping.get("proficiency_description", ""),
            "review_status": "approved",
            "approved_mappings": [mapping],
            "suggested_mappings": base_video.get("suggested_mappings") or [],
            "mapping_review": {
                **(base_video.get("mapping_review") or {}),
                "status": "approved",
                "is_request": False,
                "approved_mappings": [mapping],
            },
            "approved_at": utc_now(),
        }
    )
    return doc


def unpublish_approved_video(videos_collection, video_id: str) -> dict[str, Any]:
    from pipelines.youtube.youtube_vector_index import delete_video_points

    qdrant_summary = delete_video_points(video_id)
    delete_result = videos_collection.delete_many(
        {
            "videoId": video_id,
            "review_status": "approved",
            "mapping_review.is_request": {"$ne": True},
        }
    )
    get_bm25_index.cache_clear()
    get_cached_portal_retrieval.cache_clear()
    get_cached_reranker_scores.cache_clear()
    return {
        "approved_deleted_count": delete_result.deleted_count,
        **qdrant_summary,
    }


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
    except Exception:
        existing_ids = set()
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


def find_existing_video_ids(video_ids: list[str]) -> set[str]:
    normalized_ids = {
        str(video_id or "").strip()
        for video_id in video_ids
        if str(video_id or "").strip()
    }
    if not normalized_ids:
        return set()

    client = None
    try:
        client, videos_collection = get_portal_videos_collection()
        existing_rows = videos_collection.find(
            {"videoId": {"$in": list(normalized_ids)}},
            {"videoId": 1},
        )
        return {
            str(row.get("videoId") or "").strip()
            for row in existing_rows
            if row.get("videoId")
        }
    finally:
        if client:
            client.close()


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
        return models.Filter(
            must=must_conditions,
            min_should=models.MinShould(
                conditions=[
                    models.FieldCondition(
                        key="proficiency_level",
                        match=models.MatchValue(value=proficiency_level),
                    ),
                    models.FieldCondition(
                        key="mapped_proficiency_levels",
                        match=models.MatchValue(value=proficiency_level),
                    ),
                ],
                min_count=1,
            ),
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
    if proficiency_level and not payload_proficiency_matches(payload, proficiency_level):
        return False
    return True


@lru_cache(maxsize=PORTAL_RETRIEVAL_CACHE_SIZE)
def get_cached_portal_retrieval(
    *,
    sector: str,
    skill: str,
    proficiency_level: str | None,
    competency: str,
    strict_skill_match: bool,
    query_text: str,
) -> tuple[str, tuple[tuple[str, float], ...]]:
    """Cache retrieval-stage output (semantic + BM25 + boosts) for repeated portal queries."""
    query_vector = list(get_cached_query_embedding(query_text))
    qdrant = get_qdrant_client()
    yt_collection = env("QDRANT_YT_COLLECTION", DEFAULT_YT_COLLECTION)

    active_proficiency = proficiency_level

    def run_semantic(prof: str | None):
        return qdrant.search(
            collection_name=yt_collection,
            query_vector=query_vector,
            query_filter=build_video_filter(sector, skill, prof, strict_skill_match),
            limit=SEMANTIC_CANDIDATES,
            with_payload=True,
            with_vectors=False,
        )

    semantic_hits = run_semantic(active_proficiency)
    if not semantic_hits and active_proficiency:
        active_proficiency = None
        semantic_hits = run_semantic(active_proficiency)

    semantic_ids = [str(hit.id) for hit in semantic_hits]
    semantic_payloads = {str(hit.id): hit.payload or {} for hit in semantic_hits}

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
            strict_skill_match=strict_skill_match,
        )
    ]

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
            strict_skill_match=strict_skill_match,
        )
    }
    rrf_scores = {pid: score for pid, score in rrf_scores.items() if pid in all_payloads}

    boosted_scores = apply_metadata_boosts(
        rrf_scores,
        all_payloads,
        skill_category=sector,
        skill_proficiency=active_proficiency or "",
        skill_competency=competency,
    )

    sorted_candidates = tuple(
        sorted(boosted_scores.items(), key=lambda item: item[1], reverse=True)
    )
    return active_proficiency or "", sorted_candidates


@lru_cache(maxsize=PORTAL_RERANK_CACHE_SIZE)
def get_cached_reranker_scores(
    sf_text: str,
    rerank_pairs: tuple[tuple[str, str], ...],
) -> tuple[float, ...]:
    """Cache cross-encoder scores for repeated identical rerank batches."""
    if not rerank_pairs:
        return tuple()
    reranker = get_reranker_model()
    scores = reranker.predict(list(rerank_pairs))
    return tuple(float(score) for score in scores)


@page_router.get("/", include_in_schema=False)
@page_router.get("/academy", include_in_schema=False)
def academy_page() -> FileResponse:
    index_file = ACADEMY_FRONTEND_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=500, detail="Academy page is not available.")
    return FileResponse(index_file)


@page_router.get("/academy/", include_in_schema=False)
def academy_page_with_trailing_slash() -> FileResponse:
    return academy_page()


@page_router.get("/job-roles", include_in_schema=False)
@page_router.get("/academy/job-roles", include_in_schema=False)
def academy_job_roles_page() -> FileResponse:
    index_file = ACADEMY_FRONTEND_DIR / "job_roles.html"
    if not index_file.exists():
        raise HTTPException(status_code=500, detail="Job role lookup page is not available.")
    return FileResponse(index_file)


@page_router.get("/job-roles/", include_in_schema=False)
@page_router.get("/academy/job-roles/", include_in_schema=False)
def academy_job_roles_page_with_trailing_slash() -> FileResponse:
    return academy_job_roles_page()


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


@api_router.get("/public/job-roles", response_model=list[JobRoleSummary])
def list_job_roles(
    sector: str | None = Query(default=None),
    track: str | None = Query(default=None),
    q: str = Query(default="", max_length=120),
    limit: int = Query(default=2500, ge=1, le=5000),
):
    where_clauses: list[str] = []
    params: list[object] = []

    sector_value = (sector or "").strip()
    if sector_value:
        where_clauses.append("s.sector_name = %s")
        params.append(sector_value)

    track_value = (track or "").strip()
    if track_value:
        where_clauses.append("t.track_name = %s")
        params.append(track_value)

    query_value = q.strip()
    if query_value:
        like_value = f"%{query_value}%"
        where_clauses.append(
            "(jr.job_role_name LIKE %s OR t.track_name LIKE %s OR s.sector_name LIKE %s)"
        )
        params.extend([like_value, like_value, like_value])

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    sql = f"""
    SELECT
      jr.job_role_id,
      s.sector_name AS sector,
      t.track_name AS track,
      jr.job_role_name,
      COALESCE(req.skill_requirement_count, 0) AS skill_requirement_count,
      COALESCE(wf.critical_work_function_count, 0) AS critical_work_function_count
    FROM sf_job_role jr
    JOIN sf_track t
      ON t.track_id = jr.track_id
    JOIN sf_sector s
      ON s.sector_id = t.sector_id
    LEFT JOIN (
      SELECT
        job_role_id,
        COUNT(*) AS skill_requirement_count
      FROM sf_role_skill_req
      GROUP BY job_role_id
    ) req
      ON req.job_role_id = jr.job_role_id
    LEFT JOIN (
      SELECT
        job_role_id,
        COUNT(*) AS critical_work_function_count
      FROM sf_role_work_function
      GROUP BY job_role_id
    ) wf
      ON wf.job_role_id = jr.job_role_id
    {where_sql}
    ORDER BY s.sector_name, t.track_name, jr.job_role_name
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
        JobRoleSummary(
            job_role_id=parse_int(row.get("job_role_id")),
            sector=str(row.get("sector") or ""),
            track=str(row.get("track") or ""),
            job_role_name=str(row.get("job_role_name") or ""),
            skill_requirement_count=parse_int(row.get("skill_requirement_count")),
            critical_work_function_count=parse_int(row.get("critical_work_function_count")),
            has_skill_requirements=parse_int(row.get("skill_requirement_count")) > 0,
        )
        for row in rows
    ]


@api_router.get("/public/job-roles/{job_role_id}", response_model=JobRoleDetailResponse)
def get_job_role_detail(job_role_id: int):
    conn = get_mysql_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT
              jr.job_role_id,
              s.sector_name AS sector,
              t.track_name AS track,
              jr.job_role_name,
              COALESCE(jr.role_description, '') AS role_description,
              COALESCE(jr.performance_expectation, '') AS performance_expectation
            FROM sf_job_role jr
            JOIN sf_track t
              ON t.track_id = jr.track_id
            JOIN sf_sector s
              ON s.sector_id = t.sector_id
            WHERE jr.job_role_id = %s;
            """,
            (job_role_id,),
        )
        role_row = cur.fetchone()
        if not role_row:
            raise HTTPException(status_code=404, detail="Job role not found.")

        cur.execute(
            """
            SELECT
              wf.work_function_name,
              kt.key_task_text
            FROM sf_role_work_function wf
            LEFT JOIN sf_role_key_task kt
              ON kt.work_function_id = wf.work_function_id
            WHERE wf.job_role_id = %s
            ORDER BY wf.work_function_name, kt.key_task_id;
            """,
            (job_role_id,),
        )
        work_function_rows = cur.fetchall()

        cur.execute(
            """
            SELECT
              sk.title AS skill_title,
              COALESCE(sk.skill_type, '') AS skill_type,
              sk.tsc_ccs_code,
              req.proficiency_level,
              COALESCE(sl.proficiency_description, '') AS proficiency_description,
              ci.item_type,
              ci.item_text
            FROM sf_role_skill_req req
            JOIN sf_skill sk
              ON sk.sf_skill_id = req.sf_skill_id
            LEFT JOIN sf_skill_level sl
              ON sl.sf_skill_id = req.sf_skill_id
              AND sl.proficiency_level = req.proficiency_level
            LEFT JOIN sf_competency_item ci
              ON ci.sf_skill_id = req.sf_skill_id
              AND ci.proficiency_level = req.proficiency_level
            WHERE req.job_role_id = %s
            ORDER BY sk.title, req.proficiency_level, sk.tsc_ccs_code, ci.item_type, ci.item_id;
            """,
            (job_role_id,),
        )
        skill_rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    work_functions = [
        JobRoleCriticalWorkFunction(**item)
        for item in build_work_function_groups(work_function_rows)
    ]
    skills = [
        JobRoleSkillDetail(**item)
        for item in build_job_role_skill_groups(skill_rows)
    ]

    return JobRoleDetailResponse(
        job_role_id=parse_int(role_row.get("job_role_id")),
        sector=str(role_row.get("sector") or ""),
        track=str(role_row.get("track") or ""),
        job_role_name=str(role_row.get("job_role_name") or ""),
        role_description=str(role_row.get("role_description") or ""),
        performance_expectation=str(role_row.get("performance_expectation") or ""),
        critical_work_functions=work_functions,
        skills=skills,
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


@api_router.post("/public/videos/search", response_model=PublicVideoPreviewResponse)
def public_search_videos(payload: PublicDirectVideoSearchRequest):
    from pipelines.youtube.youtube_ingestion_service import fetch_direct_youtube_search

    query = payload.query.strip()
    if payload.source == "library":
        return search_saved_library_videos(
            query=query,
            max_results=payload.max_results,
            min_score=0.02,
        )

    api_key = get_portal_youtube_api_key()
    quota_context = get_portal_quota_context()
    allowed_orders = {"date", "rating", "relevance", "title", "videoCount", "viewCount"}
    search_order = payload.order if payload.order in allowed_orders else "relevance"

    try:
        videos, summary = fetch_direct_youtube_search(
            query=query,
            api_key=api_key,
            search_max_results=payload.max_results,
            search_order=search_order,
            search_constraints={},
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Direct YouTube search failed: {exc}",
        ) from exc

    results = [
        PublicPreviewVideo(
            source="youtube",
            score=None,
            sector="",
            skill_name="",
            competency="",
            item_type="",
            proficiency_level="",
            proficiency_description="",
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
            already_ingested=False,
        )
        for item in videos
        if str(item.get("videoId") or "").strip()
    ]
    result_video_ids = [str(item.video_id or "").strip() for item in results if str(item.video_id or "").strip()]
    existing_video_ids: set[str] = set()
    if result_video_ids:
        client = None
        try:
            client, videos_collection = get_portal_videos_collection()
            existing_video_ids = {
                str(row.get("videoId") or "").strip()
                for row in videos_collection.find(
                    {"videoId": {"$in": result_video_ids}},
                    {"videoId": 1},
                )
                if row.get("videoId")
            }
        finally:
            if client:
                client.close()
    try:
        library_matches = search_saved_library_videos(query=query, max_results=200, min_score=0.0)
        existing_video_ids.update(
            str(item.video_id or "").strip()
            for item in library_matches.results
            if str(item.video_id or "").strip()
        )
    except Exception:
        pass
    if existing_video_ids:
        results = [item for item in results if item.video_id not in existing_video_ids]

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
                "query": query,
                "count": 0,
                "already_ingested_count": 0,
                "quota_exceeded": True,
                "quota_message": quota_message,
                "quota": quota_context,
                "results": [],
            },
        )

    return PublicVideoPreviewResponse(
        query=query,
        count=len(results),
        already_ingested_count=len(existing_video_ids),
        quota_exceeded=bool(summary.get("quota_exceeded")),
        quota_message=quota_message,
        quota=quota_context,
        results=results,
    )


def search_saved_library_videos(*, query: str, max_results: int, min_score: float = 0.0) -> PublicVideoPreviewResponse:
    query_text = query.strip()
    if not query_text:
        raise HTTPException(status_code=400, detail="Search query cannot be empty.")

    try:
        query_vector = list(get_cached_query_embedding(query_text))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    qdrant = get_qdrant_client()
    yt_collection = env("QDRANT_YT_COLLECTION", DEFAULT_YT_COLLECTION)
    try:
        semantic_hits = qdrant.search(
            collection_name=yt_collection,
            query_vector=query_vector,
            limit=SEMANTIC_CANDIDATES,
            with_payload=True,
            with_vectors=False,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Saved video semantic search failed: {exc}",
        ) from exc

    semantic_ids = [str(hit.id) for hit in semantic_hits]
    semantic_payloads = {str(hit.id): hit.payload or {} for hit in semantic_hits}

    bm25, bm25_point_ids, bm25_payloads = get_bm25_index()
    query_tokens = query_text.lower().split()
    bm25_scores = bm25.get_scores(query_tokens)
    top_bm25_indices = np.argsort(bm25_scores)[::-1][:BM25_CANDIDATES]
    bm25_ranked_ids = [bm25_point_ids[i] for i in top_bm25_indices if bm25_scores[i] > 0]

    rrf_scores = reciprocal_rank_fusion(semantic_ids, bm25_ranked_ids)
    all_payloads: dict[str, dict] = {}
    all_payloads.update(bm25_payloads)
    all_payloads.update(semantic_payloads)
    all_payloads = {pid: payload for pid, payload in all_payloads.items() if pid in rrf_scores}

    sorted_candidates = sorted(rrf_scores.items(), key=lambda item: item[1], reverse=True)
    rerank_pool = sorted_candidates[: max_results * 3]

    boosted_candidates: list[tuple[str, float]] = []
    for pid, rrf_score in rerank_pool:
        item = all_payloads.get(pid, {})
        video_title = str(item.get("title") or "")
        text_match_score = score_label_match(query_text, video_title)
        final_score = float(rrf_score) + (text_match_score * 0.05)
        boosted_candidates.append((pid, final_score))

    boosted_candidates.sort(key=lambda item: item[1], reverse=True)
    ranked_candidates = boosted_candidates[:max_results]

    if not ranked_candidates:
        return PublicVideoPreviewResponse(
            query=query_text,
            count=0,
            already_ingested_count=0,
            quota_exceeded=False,
            quota_message=None,
            quota=None,
            results=[],
        )

    results: list[PublicPreviewVideo] = []
    seen_video_ids: set[str] = set()
    for pid, final_score in ranked_candidates:
        item = all_payloads.get(pid, {})
        video_id = str(item.get("video_id") or "").strip()
        if not video_id or video_id in seen_video_ids:
            continue
        if final_score < min_score:
            continue
        seen_video_ids.add(video_id)
        results.append(
            PublicPreviewVideo(
                source="library",
                score=float(final_score),
                sector=str(item.get("sector") or ""),
                skill_name=str(item.get("skill_name") or ""),
                competency=str(item.get("competency") or ""),
                item_type=str(item.get("item_type") or ""),
                proficiency_level=str(item.get("proficiency_level") or ""),
                proficiency_description=str(item.get("proficiency_description") or ""),
                video_id=video_id,
                published_at=str(item.get("published_at") or ""),
                title=str(item.get("title") or ""),
                description=str(item.get("description") or ""),
                view_count=parse_int(item.get("view_count")),
                like_count=parse_int(item.get("like_count")),
                comment_count=parse_int(item.get("comment_count")),
                tags=[str(tag) for tag in (item.get("tags") or [])],
                duration=str(item.get("duration") or ""),
                channel_title=str(item.get("channel_title") or ""),
                thumbnail_url=str(item.get("thumbnail_url") or ""),
                already_ingested=True,
            )
        )

    no_results_message = (
        "No relevant videos found. Try a different search term or lower the relevance threshold."
        if not results
        else None
    )

    return PublicVideoPreviewResponse(
        query=query_text,
        count=len(results),
        already_ingested_count=len(results),
        quota_exceeded=False,
        quota_message=no_results_message,
        quota=None,
        results=results,
    )


@api_router.post("/public/videos/suggest-mapping", response_model=PublicVideoMappingSuggestResponse)
def public_suggest_video_mapping(payload: PublicVideoMappingSuggestRequest):
    search_query = (payload.search_query or "").strip()
    candidates = get_mapping_candidates_for_video(
        video=payload.video,
        search_query=search_query,
    )
    if not candidates:
        raise HTTPException(status_code=404, detail="No seeded SkillsFuture mapping candidates were found for this video.")

    query_text = " ".join(
        [
            search_query,
            payload.video.title,
            payload.video.description,
            payload.video.channel_title,
            " ".join(payload.video.tags[:12]),
        ]
    ).strip()
    fallback = _fallback_mapping_suggestion(candidates, query_text)
    if not payload.use_ai:
        if not fallback:
            raise HTTPException(status_code=404, detail="No seeded competency candidates were found for this video.")
        return PublicVideoMappingSuggestResponse(suggestion=fallback)

    from pipelines.llm_client import call_llm_chat

    candidate_payload = json.dumps(candidates, ensure_ascii=False)
    prompt = f"""
You suggest SkillsFuture mappings for YouTube learning videos.

Rules:
- Choose only from the provided candidates.
- Do not invent sectors, skills, levels, or competencies.
- Prefer the competency item_text and proficiency_description that best match the video topic.
- Do not choose a sector-specific mapping such as Financial Services, Accountancy, Aerospace, or Healthcare unless the video title, channel, description, tags, or search query clearly mention that domain.
- For broad topics such as AI ethics, responsible AI, safety, bias, governance, or risk, prefer broad governance/data/technology/responsible-use mappings over narrow security or finance mappings unless those narrow terms are explicit.
- If no candidate is a strong conceptual fit, return the closest candidate but keep confidence below 0.45.
- Return JSON only.
- Use this schema:
{{
  "candidate_id": 0,
  "proficiency_level": "3",
  "item_type": "knowledge",
  "item_text": "Programming and coding languages, logics and styles",
  "confidence": 0.0,
  "reason": "Short reason"
}}

Video:
Title: {payload.video.title}
Description: {payload.video.description[:1200]}
Channel: {payload.video.channel_title}
Tags: {", ".join(payload.video.tags[:12])}
Search query: {search_query}

Seeded candidates:
{candidate_payload}
""".strip()

    try:
        raw = call_llm_chat(
            prompt,
            temperature=0.1,
            max_tokens=500,
            timeout=20,
            expect_json=True,
            rate_limit_retries=0,
        )
        parsed = _parse_mapping_suggestion(raw)
    except Exception as exc:
        if fallback:
            return PublicVideoMappingSuggestResponse(suggestion=fallback)
        raise HTTPException(status_code=503, detail=f"AI mapping suggestion is unavailable: {exc}") from exc

    candidate_id = parse_int(parsed.get("candidate_id"), default=-1)
    selected_candidate = next(
        (candidate for candidate in candidates if parse_int(candidate.get("candidate_id"), default=-2) == candidate_id),
        None,
    )
    if not selected_candidate:
        if not fallback:
            raise HTTPException(status_code=500, detail="AI returned a mapping that did not match seeded candidates.")
        return PublicVideoMappingSuggestResponse(suggestion=fallback)

    selected_level = str(parsed.get("proficiency_level") or "").strip()
    selected_type = str(parsed.get("item_type") or "").strip().lower()
    selected_text = str(parsed.get("item_text") or "").strip()
    matched_mapping = None
    matched_competency = None
    for mapping in selected_candidate.get("mappings", []):
        if str(mapping.get("proficiency_level") or "") != selected_level:
            continue
        for competency in mapping.get("competencies", []):
            if (
                str(competency.get("item_type") or "").strip().lower() == selected_type
                and str(competency.get("item_text") or "").strip() == selected_text
            ):
                matched_mapping = mapping
                matched_competency = competency
                break
        if matched_competency:
            break

    if not matched_mapping or not matched_competency:
        fallback = _fallback_mapping_suggestion([selected_candidate], query_text)
        if not fallback:
            raise HTTPException(status_code=500, detail="AI returned a competency that did not match seeded candidates.")
        return PublicVideoMappingSuggestResponse(suggestion=fallback)

    confidence = max(0.0, min(1.0, float(parsed.get("confidence") or 0)))
    suggestion = PublicVideoMappingSuggestion(
        sector=str(selected_candidate.get("sector") or ""),
        skill=str(selected_candidate.get("skill") or ""),
        proficiency_level=str(matched_mapping.get("proficiency_level") or ""),
        proficiency_description=str(matched_mapping.get("proficiency_description") or ""),
        item_type=str(matched_competency.get("item_type") or ""),
        competency=f"{matched_competency.get('item_type')}: {matched_competency.get('item_text')}",
        confidence=confidence,
        reason=str(parsed.get("reason") or "").strip(),
    )
    return PublicVideoMappingSuggestResponse(suggestion=suggestion)


@api_router.post("/public/videos/ingest", response_model=PublicVideoIngestResponse)
def public_ingest_videos(payload: PublicVideoIngestRequest, background_tasks: BackgroundTasks):
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
    pending_count = 0
    approved_ingested_count = 0

    try:
        requested_docs = [video.model_dump() for video in payload.videos]
        client, videos_collection = get_portal_videos_collection()
        mapped_docs = [
            doc
            for doc in requested_docs
            if str(doc.get("skill_name") or "").strip()
            and str(doc.get("sector") or "").strip()
            and str(doc.get("competency") or "").strip()
        ]
        pending_docs = [
            doc
            for doc in requested_docs
            if doc not in mapped_docs
        ]

        if mapped_docs:
            summary = upsert_selected_videos(
                videos_collection,
                mapped_docs,
                touched_docs=touched_docs,
            )
            approved_ingested_count = len(mapped_docs)

        for video in pending_docs:
            video_id = str(video.get("videoId") or "").strip()
            if not video_id:
                summary["errors"].append("Missing videoId in pending review video document")
                continue
            search_query = str(video.get("search_query") or video.get("title") or "")
            suggestion_job_id = uuid4().hex
            review_doc = prepare_review_doc(
                video,
                [],
                search_query,
                suggestion_status="queued",
                suggestion_job_id=suggestion_job_id,
            )
            result = videos_collection.update_one(
                {"videoId": video_id, "mapping_review.is_request": True},
                {"$set": review_doc},
                upsert=True,
            )
            background_tasks.add_task(
                populate_pending_review_suggestions,
                video_id,
                video,
                search_query,
                suggestion_job_id,
            )
            pending_count += 1
            if result.upserted_id is not None:
                summary["inserted"] += 1
            elif result.modified_count > 0:
                summary["updated"] += 1
            else:
                summary["unchanged"] += 1

        summary["error_count"] = len(summary.get("errors", []))
        docs_for_embedding = touched_docs or [
            doc
            for doc in mapped_docs
            if str(doc.get("videoId") or "").strip() and str(doc.get("skill_name") or "").strip()
        ]
        if docs_for_embedding:
            embed_portal_videos(summary, docs_for_embedding)
            get_bm25_index.cache_clear()
            get_cached_portal_retrieval.cache_clear()
            get_cached_reranker_scores.cache_clear()
        else:
            summary.update(
                {
                    "embedding_status": "skipped",
                    "embedding_requested": 0,
                    "embedding_indexed": 0,
                    "embedding_skipped_invalid": 0,
                }
            )
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
    if pending_count and not approved_ingested_count:
        message = "Selected videos saved for admin mapping review. AI suggestions are being prepared."
    elif pending_count:
        message = "Selected videos ingested; unmapped videos were saved for admin review and AI suggestions are being prepared."
    if summary.get("embedding_status") == "failed":
        message = "Selected videos ingested, but embedding failed."
    elif summary.get("embedding_status") == "completed" and summary.get("embedding_indexed", 0) > 0:
        message = "Selected videos ingested and indexed successfully."
    elif summary.get("error_count", 0) > 0:
        message = "Selected videos ingested with warnings."
    elif summary.get("embedding_status") == "skipped" and not pending_count:
        message = "Selected videos ingested. No valid videos required indexing."

    return PublicVideoIngestResponse(
        message=message,
        count=len(payload.videos),
        inserted=parse_int(summary.get("inserted")),
        updated=parse_int(summary.get("updated")),
        unchanged=parse_int(summary.get("unchanged")),
        error_count=parse_int(summary.get("error_count")),
        embedding_status=str(summary.get("embedding_status") or "skipped"),
        embedding_requested=parse_int(summary.get("embedding_requested")),
        embedding_indexed=parse_int(summary.get("embedding_indexed")),
        embedding_skipped_invalid=parse_int(summary.get("embedding_skipped_invalid")),
        pending_review_count=pending_count,
        approved_ingested_count=approved_ingested_count,
    )


@api_router.get("/admin/video-mapping-requests", response_model=VideoMappingReviewListResponse)
def list_video_mapping_requests(
    status: Literal["pending", "approved", "rejected", "all"] = "pending",
    limit: int = Query(50, ge=1, le=200),
):
    client = None
    try:
        client, videos_collection = get_portal_videos_collection()
        query: dict[str, Any] = {"mapping_review.is_request": True}
        if status != "all":
            query["review_status"] = status
        cursor = videos_collection.find(query).sort("mapping_review.updated_at", -1)
        requests = []
        seen_video_ids: set[str] = set()
        for doc in cursor:
            video_id = str(doc.get("videoId") or "").strip()
            if not video_id or video_id in seen_video_ids:
                continue
            seen_video_ids.add(video_id)
            requests.append(make_mapping_review_response(doc))
            if len(requests) >= limit:
                break
        return VideoMappingReviewListResponse(count=len(requests), requests=requests)
    finally:
        if client:
            client.close()


@api_router.get("/admin/video-mapping-requests/{video_id}", response_model=VideoMappingReviewRequest)
def get_video_mapping_request(video_id: str):
    normalized_video_id = normalize_video_id(video_id)
    client = None
    try:
        client, videos_collection = get_portal_videos_collection()
        doc = videos_collection.find_one(
            {
                "videoId": normalized_video_id,
                "mapping_review.is_request": True,
            },
            sort=[("mapping_review.updated_at", -1)],
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Video mapping review request not found.")
        return make_mapping_review_response(doc)
    finally:
        if client:
            client.close()


@api_router.put("/admin/video-mapping-requests/{video_id}", response_model=VideoMappingReviewRequest)
def update_video_mapping_request(video_id: str, payload: AdminMappingUpdateRequest):
    normalized_video_id = normalize_video_id(video_id)
    mappings = validate_seeded_mappings(payload.mappings)
    for mapping in mappings:
        mapping["source"] = "admin"

    client = None
    try:
        client, videos_collection = get_portal_videos_collection()
        existing = videos_collection.find_one(
            {
                "videoId": normalized_video_id,
                "mapping_review.is_request": True,
            }
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Video mapping review request not found.")

        now = utc_now()
        videos_collection.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "review_status": "pending",
                    "suggested_mappings": mappings,
                    "mapping_review.status": "pending",
                    "mapping_review.suggested_mappings": mappings,
                    "mapping_review.suggestion_status": "manual",
                    "mapping_review.suggestion_error": "",
                    "mapping_review.updated_at": now,
                    "mapping_review.reviewer": payload.reviewer,
                }
            },
        )
        updated = videos_collection.find_one({"_id": existing["_id"]})
        return make_mapping_review_response(updated or existing)
    finally:
        if client:
            client.close()


@api_router.post("/admin/video-mapping-requests/{video_id}/approve", response_model=AdminMappingActionResponse)
def approve_video_mapping_request(video_id: str, payload: AdminMappingUpdateRequest):
    normalized_video_id = normalize_video_id(video_id)
    approved_mappings = validate_seeded_mappings(payload.mappings)
    for mapping in approved_mappings:
        mapping["source"] = "admin"

    client = None
    summary: dict[str, Any] = {
        "errors": [],
        "embedding_status": "skipped",
        "embedding_indexed": 0,
    }
    approved_docs: list[dict[str, Any]] = []
    try:
        client, videos_collection = get_portal_videos_collection()
        base_video = videos_collection.find_one(
            {
                "videoId": normalized_video_id,
                "mapping_review.is_request": True,
            }
        ) or videos_collection.find_one({"videoId": normalized_video_id})
        if not base_video:
            raise HTTPException(status_code=404, detail="Video mapping review request not found.")

        videos_collection.delete_many(
            {
                "videoId": normalized_video_id,
                "review_status": "approved",
                "mapping_review.is_request": {"$ne": True},
            }
        )

        now = utc_now()
        for mapping in approved_mappings:
            doc = mapping_to_video_doc(base_video, mapping)
            doc["mapping_review"]["reviewed_at"] = now
            doc["mapping_review"]["reviewer"] = payload.reviewer
            doc["mapping_review"]["updated_at"] = now
            approved_docs.append(doc)
            videos_collection.update_one(
                {
                    "videoId": normalized_video_id,
                    "skill_name": mapping["skill"],
                    "proficiency_level": mapping["proficiency_level"],
                    "competency": mapping["competency"],
                    "review_status": "approved",
                },
                {"$set": doc},
                upsert=True,
            )

        videos_collection.update_many(
            {
                "videoId": normalized_video_id,
                "mapping_review.is_request": True,
                "review_status": {"$ne": "approved"},
            },
            {
                "$set": {
                    "review_status": "approved",
                    "approved_mappings": approved_mappings,
                    "mapping_review.status": "approved",
                    "mapping_review.approved_mappings": approved_mappings,
                    "mapping_review.reviewed_at": now,
                    "mapping_review.updated_at": now,
                    "mapping_review.reviewer": payload.reviewer,
                }
            },
        )

        embed_portal_videos(summary, approved_docs)
        get_bm25_index.cache_clear()
        get_cached_portal_retrieval.cache_clear()
        get_cached_reranker_scores.cache_clear()
    finally:
        if client:
            client.close()

    return AdminMappingActionResponse(
        message="Video mapping approved and indexed.",
        video_id=normalized_video_id,
        review_status="approved",
        approved_mappings=[VideoCompetencyMapping(**mapping) for mapping in approved_mappings],
        embedding_status=str(summary.get("embedding_status") or "skipped"),
        embedding_indexed=parse_int(summary.get("embedding_indexed")),
    )


@api_router.post("/admin/video-mapping-requests/{video_id}/reject", response_model=AdminMappingActionResponse)
def reject_video_mapping_request(video_id: str, payload: AdminRejectMappingRequest):
    normalized_video_id = normalize_video_id(video_id)
    client = None
    try:
        client, videos_collection = get_portal_videos_collection()
        existing = videos_collection.find_one(
            {
                "videoId": normalized_video_id,
                "mapping_review.is_request": True,
            }
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Video mapping review request not found.")
        if str(existing.get("review_status") or existing.get("mapping_review", {}).get("status") or "") == "approved":
            raise HTTPException(
                status_code=409,
                detail="Approved videos must be unpublished so approved docs and Qdrant points are removed.",
            )

        now = utc_now()
        videos_collection.update_many(
            {
                "videoId": normalized_video_id,
                "mapping_review.is_request": True,
            },
            {
                "$set": {
                    "review_status": "rejected",
                    "mapping_review.status": "rejected",
                    "mapping_review.reviewed_at": now,
                    "mapping_review.updated_at": now,
                    "mapping_review.reviewer": payload.reviewer,
                    "mapping_review.rejection_reason": payload.reason,
                    "mapping_review.suggestion_status": "cancelled",
                }
            },
        )
    finally:
        if client:
            client.close()

    return AdminMappingActionResponse(
        message="Video mapping rejected.",
        video_id=normalized_video_id,
        review_status="rejected",
        approved_mappings=[],
        embedding_status="skipped",
        embedding_indexed=0,
    )


@api_router.post("/admin/video-mapping-requests/{video_id}/unpublish", response_model=AdminMappingActionResponse)
def unpublish_video_mapping_request(video_id: str, payload: AdminRejectMappingRequest):
    normalized_video_id = normalize_video_id(video_id)
    client = None
    try:
        client, videos_collection = get_portal_videos_collection()
        existing = videos_collection.find_one(
            {
                "videoId": normalized_video_id,
                "mapping_review.is_request": True,
            }
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Video mapping review request not found.")

        cleanup_summary = unpublish_approved_video(videos_collection, normalized_video_id)
        now = utc_now()
        videos_collection.update_many(
            {
                "videoId": normalized_video_id,
                "mapping_review.is_request": True,
            },
            {
                "$set": {
                    "review_status": "rejected",
                    "mapping_review.status": "rejected",
                    "mapping_review.reviewed_at": now,
                    "mapping_review.updated_at": now,
                    "mapping_review.reviewer": payload.reviewer,
                    "mapping_review.rejection_reason": payload.reason or "Unpublished by admin.",
                    "mapping_review.unpublished_at": now,
                }
            },
        )
    finally:
        if client:
            client.close()

    return AdminMappingActionResponse(
        message="Video unpublished and review request rejected.",
        video_id=normalized_video_id,
        review_status="rejected",
        approved_mappings=[],
        embedding_status="skipped",
        embedding_indexed=0,
        approved_deleted_count=parse_int(cleanup_summary.get("approved_deleted_count")),
        qdrant_delete_status=str(cleanup_summary.get("qdrant_delete_status") or "skipped"),
    )


@api_router.post("/admin/video-mapping-requests/{video_id}/reopen", response_model=VideoMappingReviewRequest)
def reopen_video_mapping_request(video_id: str, payload: AdminRejectMappingRequest):
    normalized_video_id = normalize_video_id(video_id)
    client = None
    try:
        client, videos_collection = get_portal_videos_collection()
        existing = videos_collection.find_one(
            {
                "videoId": normalized_video_id,
                "mapping_review.is_request": True,
            }
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Video mapping review request not found.")
        if str(existing.get("review_status") or existing.get("mapping_review", {}).get("status") or "") == "approved":
            raise HTTPException(
                status_code=409,
                detail="Approved videos are already live. Unpublish them before reopening.",
            )

        now = utc_now()
        videos_collection.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "review_status": "pending",
                    "mapping_review.status": "pending",
                    "mapping_review.updated_at": now,
                    "mapping_review.reviewer": payload.reviewer,
                    "mapping_review.rejection_reason": "",
                },
                "$unset": {
                    "mapping_review.reviewed_at": "",
                    "mapping_review.unpublished_at": "",
                },
            },
        )
        updated = videos_collection.find_one({"_id": existing["_id"]})
        return make_mapping_review_response(updated or existing)
    finally:
        if client:
            client.close()


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

    active_strict = payload.strict_skill_match

    try:
        active_proficiency_key, sorted_candidates = get_cached_portal_retrieval(
            sector=sector,
            skill=skill,
            proficiency_level=proficiency_level,
            competency=competency,
            strict_skill_match=active_strict,
            query_text=query_text,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Video recommendation search failed: {exc}",
        ) from exc

    active_proficiency = active_proficiency_key or None
    _, _, bm25_payloads = get_bm25_index()
    all_payloads = {
        pid: bm25_payloads.get(pid, {})
        for pid, _ in sorted_candidates
    }

    applied_filters = {"sector": sector}
    if active_strict:
        applied_filters["skill"] = skill
    if active_proficiency:
        applied_filters["proficiency_level"] = active_proficiency

    # ------------------------------------------------------------------
    # Cross-encoder reranking
    # ------------------------------------------------------------------
    rerank_target = parse_int(
        env("PORTAL_RERANK_TOP_N", str(PORTAL_DEFAULT_RERANK_CANDIDATES)),
        PORTAL_DEFAULT_RERANK_CANDIDATES,
    )
    if rerank_target < 1:
        rerank_target = PORTAL_DEFAULT_RERANK_CANDIDATES

    rerank_pool_size = min(
        len(sorted_candidates),
        max(payload.top_k, rerank_target),
    )
    rerank_candidates = list(sorted_candidates[:rerank_pool_size])

    if not rerank_candidates:
        return PublicRecommendResponse(
            query=query_text,
            applied_filters=applied_filters,
            count=0,
            results=[],
        )

    sf_text = f"Skill: {skill}. Sector: {sector}. Competency: {competency}."

    rerank_pairs = []
    rerank_pids = []
    rerank_rrf_scores = []
    for pid, rrf_score in rerank_candidates:
        yt_text = build_yt_rerank_text(all_payloads.get(pid, {}))
        rerank_pairs.append((sf_text, yt_text))
        rerank_pids.append(pid)
        rerank_rrf_scores.append(rrf_score)

    reranker_pairs_tuple = tuple(rerank_pairs)
    try:
        reranker_scores = np.array(
            get_cached_reranker_scores(sf_text, reranker_pairs_tuple),
            dtype=float,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    ranked_indices = np.argsort(reranker_scores)[::-1]
    final_indices = ranked_indices[: payload.top_k]

    # ------------------------------------------------------------------
    # Vote-based re-ranking (post cross-encoder)
    # ------------------------------------------------------------------
    # Collect video IDs for the final candidates
    final_video_ids = [
        str(all_payloads.get(rerank_pids[idx], {}).get("video_id") or "")
        for idx in final_indices
    ]
    votes_map: dict[str, int] = {}
    try:
        client, votes_col = get_video_votes_collection()
        try:
            for doc in votes_col.find(
                {"video_id": {"$in": final_video_ids}},
                {"_id": 0, "video_id": 1, "votes": 1},
            ):
                votes_map[doc["video_id"]] = doc.get("votes", 0)
        finally:
            client.close()
    except Exception:
        pass  # non-critical: proceed without vote data

    # Bubble-sort re-rank: swap adjacent if lower-ranked has >= 3 more votes
    final_indices_list = list(final_indices)
    changed = True
    while changed:
        changed = False
        for i in range(len(final_indices_list) - 1):
            vid_upper = str(all_payloads.get(rerank_pids[final_indices_list[i]], {}).get("video_id") or "")
            vid_lower = str(all_payloads.get(rerank_pids[final_indices_list[i + 1]], {}).get("video_id") or "")
            if votes_map.get(vid_lower, 0) - votes_map.get(vid_upper, 0) >= 3:
                final_indices_list[i], final_indices_list[i + 1] = final_indices_list[i + 1], final_indices_list[i]
                changed = True

    # ------------------------------------------------------------------
    # Build response
    # ------------------------------------------------------------------
    results: list[PublicRecommendedVideo] = []
    for idx in final_indices_list:
        pid = rerank_pids[idx]
        item = all_payloads.get(pid, {})
        vid = str(item.get("video_id") or "")
        results.append(
            PublicRecommendedVideo(
                score=float(reranker_scores[idx]),
                rrf_score=float(rerank_rrf_scores[idx]),
                reranker_score=float(reranker_scores[idx]),
                video_id=vid,
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
                user_votes=votes_map.get(vid, 0),
            )
        )

    return PublicRecommendResponse(
        query=query_text,
        applied_filters=applied_filters,
        count=len(results),
        results=results,
    )


# ---------------------------------------------------------------------------
# Video voting endpoints
# ---------------------------------------------------------------------------


class VoteRequest(BaseModel):
    vote: int = Field(..., ge=-1, le=1, description="1 (upvote), -1 (downvote), or 0 (remove)")


class VoteResponse(BaseModel):
    video_id: str
    votes: int


@api_router.post("/public/videos/{video_id}/vote", response_model=VoteResponse)
def cast_vote(video_id: str, payload: VoteRequest):
    """Atomically increment or decrement the global vote total for a video."""
    increment = payload.vote
    _, collection = get_video_votes_collection()
    collection.update_one(
        {"video_id": video_id},
        {"$inc": {"votes": increment}},
        upsert=True,
    )
    doc = collection.find_one({"video_id": video_id}, {"_id": 0, "votes": 1})
    return VoteResponse(video_id=video_id, votes=doc.get("votes", 0) if doc else 0)


class BulkVotesResponse(BaseModel):
    votes: dict[str, int]


@api_router.get("/public/videos/votes", response_model=BulkVotesResponse)
def get_votes(ids: str = Query("", description="Comma-separated video IDs")):
    """Fetch vote totals for multiple videos at once."""
    video_ids = [v.strip() for v in ids.split(",") if v.strip()]
    if not video_ids:
        return BulkVotesResponse(votes={})
    _, collection = get_video_votes_collection()
    cursor = collection.find(
        {"video_id": {"$in": video_ids}},
        {"_id": 0, "video_id": 1, "votes": 1},
    )
    votes_map = {doc["video_id"]: doc.get("votes", 0) for doc in cursor}
    for vid in video_ids:
        if vid not in votes_map:
            votes_map[vid] = 0
    return BulkVotesResponse(votes=votes_map)
