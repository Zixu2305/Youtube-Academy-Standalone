from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import mysql.connector
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
DEFAULT_COLLECTION_NAME = "sf_skill_level_docs__bge_base__768"

router = APIRouter()


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


class SkillSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="User query text.")
    top_k: int = Field(10, ge=1, le=50)
    exclude_retired: bool = True
    skill_type: Literal["tsc", "ccs"] | None = None
    category: str | None = None
    include_details: bool = True


def build_query_filter(payload: SkillSearchRequest) -> models.Filter | None:
    must_conditions: list[models.FieldCondition] = []

    if payload.exclude_retired:
        must_conditions.append(
            models.FieldCondition(
                key="is_retired",
                match=models.MatchValue(value=False),
            )
        )
    if payload.skill_type:
        must_conditions.append(
            models.FieldCondition(
                key="skill_type",
                match=models.MatchValue(value=payload.skill_type),
            )
        )
    if payload.category:
        must_conditions.append(
            models.FieldCondition(
                key="category",
                match=models.MatchValue(value=payload.category),
            )
        )

    if not must_conditions:
        return None
    return models.Filter(must=must_conditions)


def fetch_skill_level_details(
    keys: list[tuple[int, str]],
) -> dict[str, dict]:
    if not keys:
        return {}

    placeholders = ", ".join(["(%s, %s)"] * len(keys))
    params = []
    for sf_skill_id, proficiency_level in keys:
        params.extend([sf_skill_id, proficiency_level])

    sql = f"""
    SELECT
      sl.sf_skill_id,
      sl.proficiency_level,
      sl.proficiency_description,
      s.description AS skill_description,
      s.sector_name,
      ci.item_type,
      ci.item_text
    FROM sf_skill_level sl
    INNER JOIN sf_skill s
      ON s.sf_skill_id = sl.sf_skill_id
    LEFT JOIN sf_competency_item ci
      ON ci.sf_skill_id = sl.sf_skill_id
      AND ci.proficiency_level = sl.proficiency_level
    WHERE (sl.sf_skill_id, sl.proficiency_level) IN ({placeholders})
    ORDER BY sl.sf_skill_id, sl.proficiency_level, ci.item_id;
    """

    conn = get_mysql_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    details_map: dict[str, dict] = {}
    for row in rows:
        point_id = f"{row['sf_skill_id']}:{row['proficiency_level']}"
        if point_id not in details_map:
            details_map[point_id] = {
                "sf_skill_id": row["sf_skill_id"],
                "proficiency_level": row["proficiency_level"],
                "proficiency_description": row["proficiency_description"] or "",
                "skill_description": row["skill_description"] or "",
                "sector_name": row["sector_name"] or "",
                "knowledge_items": [],
                "ability_items": [],
            }

        if row["item_type"] == "knowledge" and row["item_text"]:
            details_map[point_id]["knowledge_items"].append(row["item_text"])
        elif row["item_type"] == "ability" and row["item_text"]:
            details_map[point_id]["ability_items"].append(row["item_text"])

    return details_map


@router.post("/search/skills")
def search_skills(payload: SkillSearchRequest):
    query_text = payload.query.strip()
    if not query_text:
        raise HTTPException(status_code=400, detail="Query text cannot be empty.")

    model = get_embedding_model()
    query_vector = model.encode(
        query_text,
        normalize_embeddings=True,
    ).tolist()

    query_filter = build_query_filter(payload)
    collection_name = env("QDRANT_COLLECTION", DEFAULT_COLLECTION_NAME)
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
    detail_keys: list[tuple[int, str]] = []
    for hit in hits:
        hit_payload = hit.payload or {}
        point_id = hit_payload.get("point_id")
        if not point_id:
            sf_skill_id = hit_payload.get("sf_skill_id")
            proficiency_level = hit_payload.get("proficiency_level")
            if sf_skill_id is not None and proficiency_level is not None:
                point_id = f"{sf_skill_id}:{proficiency_level}"
            else:
                point_id = str(hit.id)
        result = {
            "point_id": str(point_id),
            "score": float(hit.score),
            "payload": {
                "sf_skill_id": hit_payload.get("sf_skill_id"),
                "proficiency_level": hit_payload.get("proficiency_level"),
                "tsc_ccs_code": hit_payload.get("tsc_ccs_code"),
                "skill_title": hit_payload.get("skill_title"),
                "category": hit_payload.get("category"),
                "skill_type": hit_payload.get("skill_type"),
                "is_retired": hit_payload.get("is_retired"),
            },
        }
        results.append(result)

        if payload.include_details:
            sf_skill_id = hit_payload.get("sf_skill_id")
            proficiency_level = hit_payload.get("proficiency_level")
            if sf_skill_id is not None and proficiency_level is not None:
                detail_keys.append((int(sf_skill_id), str(proficiency_level)))

    if payload.include_details and detail_keys:
        details_map = fetch_skill_level_details(detail_keys)
        for result in results:
            result["details"] = details_map.get(result["point_id"], {})

    return {
        "query": query_text,
        "collection": collection_name,
        "model_name": env("EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME),
        "count": len(results),
        "results": results,
    }
