from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from pymongo import MongoClient
from pydantic import BaseModel, Field

from pipelines.youtube.youtube_vector_index import (
    normalize_video_mappings,
    sync_video_mapping_payload,
)


router = APIRouter()
ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


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


def get_mongo_client() -> MongoClient:
    host = env("MONGO_HOST", "127.0.0.1")
    port = int(env("MONGO_PORT", "27017"))
    username = env("MONGO_ROOT_USERNAME")
    password = env("MONGO_ROOT_PASSWORD")
    auth_source = env("MONGO_AUTH_SOURCE", "admin")
    auth_mechanism = env("MONGO_AUTH_MECHANISM", "SCRAM-SHA-256")
    if username and password:
        return MongoClient(
            host=host,
            port=port,
            username=username,
            password=password,
            authSource=auth_source,
            authMechanism=auth_mechanism,
        )
    return MongoClient(host=host, port=port)


def get_portal_videos_collection():
    client = get_mongo_client()
    database_name = env("MONGO_DATABASE", env("DB_NAME", "yta"))
    return client, client[database_name]["videos"]


class CompetencyMapping(BaseModel):
    """Single competency mapping for a video."""

    competency: str
    item_type: str
    proficiency_level: str
    proficiency_description: str = ""


class MultiLabelVideoSearchRequest(BaseModel):
    """Search for videos to apply multi-labelling."""

    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=100)
    search_type: str = Field(default="title", pattern="^(title|video_id|skill)$")


class VideoMappingInfo(BaseModel):
    """Current video details with mappings for multi-labelling UI."""

    video_id: str
    title: str
    sector: str
    skill_name: str
    current_mappings: list[CompetencyMapping] = Field(default_factory=list)
    description: str = ""
    channel_title: str = ""


class MultiLabelVideoSearchResponse(BaseModel):
    """Search results for multi-labelling."""

    count: int
    results: list[VideoMappingInfo] = Field(default_factory=list)


class MultiLabelUpdateRequest(BaseModel):
    """Update a video with multiple competency mappings."""

    video_id: str
    skill_name: str
    sector: str
    mappings_to_add: list[CompetencyMapping] = Field(default_factory=list)
    mappings_to_remove: list[CompetencyMapping | str] = Field(default_factory=list)


class MultiLabelUpdateResponse(BaseModel):
    """Response from multi-label update."""

    video_id: str
    message: str
    total_mappings: int
    added: int
    removed: int
    qdrant_synced: bool = True
    qdrant_error: str | None = None


class CompetencyListResponse(BaseModel):
    """Available competencies for a proficiency level."""

    proficiency_level: str
    proficiency_description: str
    knowledge_items: list[str] = Field(default_factory=list)
    ability_items: list[str] = Field(default_factory=list)


def mapping_key(mapping: dict | CompetencyMapping) -> tuple[str, str]:
    if isinstance(mapping, CompetencyMapping):
        return (mapping.proficiency_level.strip(), mapping.competency.strip())
    return (
        str(mapping.get("proficiency_level") or "").strip(),
        str(mapping.get("competency") or "").strip(),
    )


def mapping_from_single_fields(doc: dict) -> dict | None:
    competency = str(doc.get("competency") or "").strip()
    if not competency:
        return None
    return {
        "competency": competency,
        "item_type": str(doc.get("item_type") or ""),
        "proficiency_level": str(doc.get("proficiency_level") or ""),
        "proficiency_description": str(doc.get("proficiency_description") or ""),
    }


def current_mappings_from_doc(doc: dict) -> list[dict]:
    return normalize_video_mappings(doc)


def clear_recommendation_caches() -> None:
    try:
        from api.routes.recommend import get_bm25_index

        get_bm25_index.cache_clear()
    except Exception:
        pass

    try:
        from api.routes.learner_portal import (
            get_cached_portal_retrieval,
            get_cached_reranker_scores,
        )

        get_cached_portal_retrieval.cache_clear()
        get_cached_reranker_scores.cache_clear()
    except Exception:
        pass


@router.post("/public/multi-label/search-videos", response_model=MultiLabelVideoSearchResponse)
def multi_label_search_videos(payload: MultiLabelVideoSearchRequest):
    """Search for videos to apply multi-labelling."""

    query = payload.query.strip().lower()
    search_type = payload.search_type
    limit = payload.limit

    client, videos_collection = get_portal_videos_collection()
    try:
        if search_type == "video_id":
            filter_query = {"videoId": {"$regex": query, "$options": "i"}}
        elif search_type == "skill":
            filter_query = {"skill_name": {"$regex": query, "$options": "i"}}
        else:
            filter_query = {"title": {"$regex": query, "$options": "i"}}

        docs = list(
            videos_collection.find(
                filter_query,
                {
                    "_id": 0,
                    "videoId": 1,
                    "title": 1,
                    "sector": 1,
                    "skill_name": 1,
                    "description": 1,
                    "channelTitle": 1,
                    "competency": 1,
                    "item_type": 1,
                    "proficiency_level": 1,
                    "proficiency_description": 1,
                    "additional_mappings": 1,
                    "mappings": 1,
                },
            ).limit(limit)
        )

        results = []
        for doc in docs:
            current_mappings = [
                CompetencyMapping(**mapping)
                for mapping in current_mappings_from_doc(doc)
            ]

            results.append(
                VideoMappingInfo(
                    video_id=doc.get("videoId", ""),
                    title=doc.get("title", ""),
                    sector=doc.get("sector", ""),
                    skill_name=doc.get("skill_name", ""),
                    current_mappings=current_mappings,
                    description=doc.get("description", ""),
                    channel_title=doc.get("channelTitle", ""),
                )
            )

        return MultiLabelVideoSearchResponse(count=len(results), results=results)
    finally:
        client.close()


@router.get("/public/multi-label/competencies", response_model=CompetencyListResponse)
def get_multi_label_competencies(
    sector: str = Query(...),
    skill: str = Query(...),
    proficiency_level: str = Query(...),
):
    """Get available competencies for a proficiency level."""

    sql = """
    SELECT
      m.proficiency_level,
      sl.proficiency_description,
      ci.item_type,
      ci.item_text
    FROM map_sf_to_cat_skill m
    JOIN sf_skill_level sl
      ON m.sf_skill_id = sl.sf_skill_id
      AND m.proficiency_level = sl.proficiency_level
    JOIN sf_competency_item ci
      ON m.sf_skill_id = ci.sf_skill_id
      AND m.proficiency_level = ci.proficiency_level
    WHERE m.sector_name_raw = %s
      AND m.source_skill_title = %s
      AND m.proficiency_level = %s
    ORDER BY m.proficiency_level, ci.item_type, ci.item_id;
    """

    conn = get_mysql_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, (sector.strip(), skill.strip(), proficiency_level.strip()))
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No competency data found for this sector-skill-proficiency combination.",
        )

    knowledge_items = []
    ability_items = []
    proficiency_description = ""

    for row in rows:
        proficiency_description = str(row.get("proficiency_description") or "")
        item_type = str(row.get("item_type") or "").strip().lower()
        item_text = str(row.get("item_text") or "").strip()

        if not item_text:
            continue

        formatted_competency = f"{item_type}: {item_text}"

        if item_type == "knowledge" and formatted_competency not in knowledge_items:
            knowledge_items.append(formatted_competency)
        elif item_type == "ability" and formatted_competency not in ability_items:
            ability_items.append(formatted_competency)

    return CompetencyListResponse(
        proficiency_level=proficiency_level,
        proficiency_description=proficiency_description,
        knowledge_items=knowledge_items,
        ability_items=ability_items,
    )


@router.post("/public/multi-label/update-video", response_model=MultiLabelUpdateResponse)
def multi_label_update_video(payload: MultiLabelUpdateRequest):
    """Update a video with multiple competency mappings."""

    video_id = payload.video_id.strip()
    skill_name = payload.skill_name.strip()
    sector = payload.sector.strip()
    mappings_to_add = payload.mappings_to_add
    mappings_to_remove = payload.mappings_to_remove

    if not video_id or not skill_name:
        raise HTTPException(status_code=400, detail="video_id and skill_name are required.")

    if not mappings_to_add and not mappings_to_remove:
        raise HTTPException(status_code=400, detail="Either mappings_to_add or mappings_to_remove is required.")

    client, videos_collection = get_portal_videos_collection()
    try:
        doc = videos_collection.find_one({"videoId": video_id, "skill_name": skill_name})
        if not doc:
            raise HTTPException(
                status_code=404,
                detail="Video not found. Make sure it has been ingested first.",
            )

        current_mappings = current_mappings_from_doc(doc)

        removed_count = 0
        if mappings_to_remove:
            remove_keys = set()
            remove_competencies = set()
            for item in mappings_to_remove:
                if isinstance(item, CompetencyMapping):
                    key = mapping_key(item)
                    if all(key):
                        remove_keys.add(key)
                else:
                    competency = str(item or "").strip()
                    if competency:
                        remove_competencies.add(competency)

            kept_mappings = []
            for mapping in current_mappings:
                key = mapping_key(mapping)
                competency = key[1]
                should_remove = key in remove_keys or (
                    competency in remove_competencies and not remove_keys
                )
                if should_remove:
                    removed_count += 1
                else:
                    kept_mappings.append(mapping)
            current_mappings = kept_mappings

        existing_keys = {mapping_key(m) for m in current_mappings}
        added_count = 0
        for mapping in mappings_to_add:
            key = mapping_key(mapping)
            if all(key) and key not in existing_keys:
                current_mappings.append(
                    {
                        "competency": key[1],
                        "item_type": mapping.item_type,
                        "proficiency_level": key[0],
                        "proficiency_description": mapping.proficiency_description,
                    }
                )
                existing_keys.add(key)
                added_count += 1

        result = videos_collection.update_one(
            {"videoId": video_id, "skill_name": skill_name},
            {
                "$set": {
                    "additional_mappings": current_mappings,
                    "sector": sector,
                    "updated_at": datetime.now(),
                },
                "$unset": {"mappings": ""},
            },
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=500, detail="Failed to update video.")

        updated_doc = dict(doc)
        updated_doc["sector"] = sector
        updated_doc["additional_mappings"] = current_mappings

        qdrant_synced = True
        qdrant_error = None
        try:
            sync_result = sync_video_mapping_payload(updated_doc)
            qdrant_synced = sync_result.get("qdrant_payload_status") != "skipped"
            if qdrant_synced:
                clear_recommendation_caches()
        except Exception as exc:
            qdrant_synced = False
            qdrant_error = str(exc)

        return MultiLabelUpdateResponse(
            video_id=video_id,
            message="Successfully updated video mappings.",
            total_mappings=len(current_mappings),
            added=added_count,
            removed=removed_count,
            qdrant_synced=qdrant_synced,
            qdrant_error=qdrant_error,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating video mappings: {str(exc)}",
        ) from exc
    finally:
        client.close()
