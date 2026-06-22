"""
quiz_data_access.py
-------------------
All MySQL (and optional MongoDB) queries needed for quiz generation.
Everything is read-only; no writes to the skill/sector tables.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import mysql.connector
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _get_mysql_conn():
    host = _env("DB_HOST", "127.0.0.1")
    if _env("DB_PORT"):
        port = int(_env("DB_PORT"))
    else:
        port = 3306 if host == "mysql" else int(_env("MYSQL_PORT", "3306"))

    return mysql.connector.connect(
        host=host,
        port=port,
        user=_env("DB_USER", _env("MYSQL_USER", "yta")),
        password=_env("DB_PASSWORD", _env("MYSQL_PASSWORD", "")),
        database=_env("DB_NAME", _env("MYSQL_DATABASE", "yta")),
        autocommit=True,
    )


# ---------------------------------------------------------------------------
# Competency string helpers
# ---------------------------------------------------------------------------

def parse_competency_string(competency: str) -> tuple[str, str]:
    """
    Split 'knowledge: some text' → ('knowledge', 'some text').
    Falls back to ('ability', competency) if no prefix found.
    """
    for prefix in ("knowledge: ", "ability: "):
        if competency.lower().startswith(prefix):
            return prefix.strip(": "), competency[len(prefix):]
    return "ability", competency


# ---------------------------------------------------------------------------
# Core queries
# ---------------------------------------------------------------------------

def fetch_sf_skill_id(sector: str, skill_title: str) -> int | None:
    """Resolve the canonical sf_skill_id from sector + skill title."""
    sql = """
        SELECT DISTINCT m.sf_skill_id
        FROM map_sf_to_cat_skill m
        WHERE m.sector_name_raw = %s
          AND m.source_skill_title = %s
        LIMIT 1
    """
    conn = _get_mysql_conn()
    cur = conn.cursor()
    try:
        cur.execute(sql, (sector, skill_title))
        row = cur.fetchone()
        return int(row[0]) if row else None
    finally:
        cur.close()
        conn.close()


def fetch_all_proficiency_levels(sf_skill_id: int) -> list[dict[str, str]]:
    """Return all proficiency levels + descriptions for a given skill."""
    sql = """
        SELECT proficiency_level, COALESCE(proficiency_description, '') AS proficiency_description
        FROM sf_skill_level
        WHERE sf_skill_id = %s
        ORDER BY proficiency_level
    """
    conn = _get_mysql_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, (sf_skill_id,))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def fetch_competency_items_at_level(sf_skill_id: int, proficiency_level: str) -> list[dict[str, str]]:
    """Return all knowledge/ability items for a skill at a specific level."""
    sql = """
        SELECT item_type, item_text
        FROM sf_competency_item
        WHERE sf_skill_id = %s
          AND proficiency_level = %s
        ORDER BY item_type, item_id
    """
    conn = _get_mysql_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, (sf_skill_id, proficiency_level))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def fetch_competency_items_other_levels(sf_skill_id: int, proficiency_level: str) -> list[dict[str, str]]:
    """Return competency items from every OTHER proficiency level of the same skill."""
    sql = """
        SELECT ci.proficiency_level, ci.item_type, ci.item_text
        FROM sf_competency_item ci
        WHERE ci.sf_skill_id = %s
          AND ci.proficiency_level != %s
        ORDER BY ci.proficiency_level, ci.item_type, ci.item_id
    """
    conn = _get_mysql_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, (sf_skill_id, proficiency_level))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def fetch_other_skills_in_sector(sector: str, exclude_skill: str, limit: int = 20) -> list[str]:
    """Return other skill titles in the same sector (used for Q2 distractors)."""
    sql = """
        SELECT DISTINCT source_skill_title
        FROM map_sf_to_cat_skill
        WHERE sector_name_raw = %s
          AND source_skill_title != %s
          AND source_skill_title IS NOT NULL
        ORDER BY source_skill_title
        LIMIT %s
    """
    conn = _get_mysql_conn()
    cur = conn.cursor()
    try:
        cur.execute(sql, (sector, exclude_skill, limit))
        return [row[0] for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def fetch_video_descriptions(sector: str, skill: str, limit: int = 10) -> list[str]:
    """
    Fetch video descriptions/tags from MongoDB for this sector+skill combination.
    Returns an empty list if MongoDB is unavailable or has no data.
    """
    try:
        from pymongo import MongoClient  # optional dependency

        host = _env("MONGO_HOST", "127.0.0.1")
        port = _env("MONGO_PORT", "27017")
        user = _env("MONGO_ROOT_USERNAME", "")
        password = _env("MONGO_ROOT_PASSWORD", "")
        db_name = _env("MONGO_DATABASE", "yta")
        auth_source = _env("MONGO_AUTH_SOURCE", "admin")
        auth_mechanism = _env("MONGO_AUTH_MECHANISM", "SCRAM-SHA-256")

        if user and password:
            uri = (
                f"mongodb://{user}:{password}@{host}:{port}/"
                f"{db_name}?authSource={auth_source}&authMechanism={auth_mechanism}"
            )
        else:
            uri = f"mongodb://{host}:{port}/{db_name}"

        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        col = client[db_name]["videos"]

        docs = list(
            col.find(
                {"sector": sector, "skill_name": skill},
                {"_id": 0, "description": 1, "tags": 1, "title": 1},
            ).limit(limit)
        )
        client.close()

        descriptions: list[str] = []
        for doc in docs:
            if doc.get("description"):
                descriptions.append(str(doc["description"])[:300])
            elif doc.get("title"):
                descriptions.append(str(doc["title"]))
        return descriptions
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Aggregate fetch — single entry point used by the quiz engine
# ---------------------------------------------------------------------------

def fetch_quiz_context(
    sector: str,
    skill: str,
    competency: str,
    proficiency_level: str,
    quiz_mode: str = "competency",
) -> dict[str, Any]:
    """
    Gather everything the quiz engine needs in one call.
    
    Args:
        sector: Sector name
        skill: Skill name
        competency: Competency string (e.g., "knowledge: ...", "ability: ...")
        proficiency_level: Proficiency level
        quiz_mode: Quiz mode determining context filtering.
                  - "competency": Use the passed competency as-is
                  - "knowledge": Force item_type to "knowledge" (override competency string)
                  - "ability": Force item_type to "ability" (override competency string)
                  - "proficiency", "skill": Use competency string to determine item_type

    Returns:
        {
          "sector": str,
          "skill": str,
          "competency": str,
          "proficiency_level": str,
          "item_type": "knowledge"|"ability",
          "item_text": str,
          "proficiency_description": str,
          "knowledge_items": [str, ...],
          "ability_items": [str, ...],
          "all_levels": [{"proficiency_level": str, "proficiency_description": str}, ...],
          "other_level_competencies": [{"proficiency_level": str, "item_type": str, "item_text": str}, ...],
          "other_sector_skills": [str, ...],
          "video_descriptions": [str, ...],
        }
    """
    # Determine item_type based on quiz_mode
    if quiz_mode == "knowledge":
        item_type = "knowledge"
        # Parse to extract text without prefix
        _, item_text = parse_competency_string(competency)
    elif quiz_mode == "ability":
        item_type = "ability"
        # Parse to extract text without prefix
        _, item_text = parse_competency_string(competency)
    else:
        # For "competency", "proficiency", "skill" modes: parse from competency string
        item_type, item_text = parse_competency_string(competency)

    sf_skill_id = fetch_sf_skill_id(sector, skill)
    if sf_skill_id is None:
        raise ValueError(f"Skill not found: sector='{sector}' skill='{skill}'")

    all_levels = fetch_all_proficiency_levels(sf_skill_id)

    # proficiency_description for the selected level
    proficiency_description = ""
    for lvl in all_levels:
        if lvl["proficiency_level"] == proficiency_level:
            proficiency_description = lvl["proficiency_description"]
            break

    # Fetch all items at this level (for cross-filtering and distractors)
    items_at_level = fetch_competency_items_at_level(sf_skill_id, proficiency_level)
    knowledge_items = [r["item_text"] for r in items_at_level if r["item_type"] == "knowledge"]
    ability_items = [r["item_text"] for r in items_at_level if r["item_type"] == "ability"]

    other_level_competencies = fetch_competency_items_other_levels(sf_skill_id, proficiency_level)
    other_sector_skills = fetch_other_skills_in_sector(sector, skill)
    video_descriptions = fetch_video_descriptions(sector, skill)

    return {
        "sector": sector,
        "skill": skill,
        "competency": competency,
        "proficiency_level": proficiency_level,
        "item_type": item_type,
        "item_text": item_text,
        "proficiency_description": proficiency_description,
        "knowledge_items": knowledge_items,
        "ability_items": ability_items,
        "all_levels": all_levels,
        "other_level_competencies": other_level_competencies,
        "other_sector_skills": other_sector_skills,
        "video_descriptions": video_descriptions,
    }
