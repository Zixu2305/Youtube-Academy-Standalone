from datetime import datetime, timedelta

import mysql.connector
from pymongo import MongoClient

from youtube_config import RECENT_ROWS_LIMIT, env, to_int


def get_mysql_conn():
    host = env("DB_HOST", "")
    port = to_int(env("DB_PORT", "3306"), 3306)
    return mysql.connector.connect(
        host=host,
        port=port,
        user=env("DB_USER", ""),
        password=env("DB_PASSWORD", ""),
        database=env("DB_NAME", ""),
        autocommit=False,
    )


def get_mongo_client():
    mongo_host = env("MONGO_HOST", "")
    mongo_port = env("MONGO_PORT", "")
    mongo_user = env("MONGO_ROOT_USERNAME", "")
    mongo_pass = env("MONGO_ROOT_PASSWORD", "")
    mongo_db = env("MONGO_DATABASE", "")
    mongo_auth_source = env("MONGO_AUTH_SOURCE", "admin")

    if mongo_user and mongo_pass:
        uri = (
            f"mongodb://{mongo_user}:{mongo_pass}@{mongo_host}:{mongo_port}/"
            f"{mongo_db}?authSource={mongo_auth_source}"
        )
    else:
        uri = f"mongodb://{mongo_host}:{mongo_port}/{mongo_db}"

    return MongoClient(uri)


def fetch_sectors_and_skills():
    try:
        conn = get_mysql_conn()
        cur = conn.cursor()
        query = """
            SELECT DISTINCT sector_name_raw, source_skill_title
            FROM map_sf_to_cat_skill
            WHERE sector_name_raw IS NOT NULL AND source_skill_title IS NOT NULL
            ORDER BY sector_name_raw, source_skill_title
        """
        cur.execute(query)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        sectors = {}
        for sector, skill in rows:
            if sector not in sectors:
                sectors[sector] = []
            sectors[sector].append(skill)
        return sectors
    except Exception:
        return {}


def search_skills(sector, search_term):
    try:
        conn = get_mysql_conn()
        cur = conn.cursor()
        query = """
            SELECT DISTINCT source_skill_title
            FROM map_sf_to_cat_skill
            WHERE sector_name_raw = %s AND source_skill_title LIKE %s
            ORDER BY source_skill_title
        """
        cur.execute(query, (sector, f"%{search_term}%"))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [row[0] for row in rows]
    except Exception:
        return []


def search_competencies(sector, skill, proficiency_level):
    try:
        conn = get_mysql_conn()
        cur = conn.cursor()
        query = """
            SELECT DISTINCT CONCAT(sci.item_type, ': ', sci.item_text) AS competency
            FROM map_sf_to_cat_skill m
            JOIN sf_competency_item sci ON m.sf_skill_id = sci.sf_skill_id
            WHERE m.sector_name_raw = %s 
              AND m.source_skill_title = %s
              AND sci.proficiency_level = %s
            ORDER BY competency
        """
        cur.execute(query, (sector, skill, proficiency_level))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [row[0] for row in rows]
    except Exception:
        return []


def search_proficiency_levels(sector, skill):
    try:
        conn = get_mysql_conn()
        cur = conn.cursor()
        query = """
            SELECT DISTINCT m.proficiency_level
            FROM map_sf_to_cat_skill m
            WHERE m.sector_name_raw = %s
              AND m.source_skill_title = %s
            ORDER BY m.proficiency_level
        """
        cur.execute(query, (sector, skill))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [row[0] for row in rows]
    except Exception:
        return []


def get_requirement(sector, skill, proficiency_level, competency):
    try:
        conn = get_mysql_conn()
        cur = conn.cursor()
        query = """
            SELECT DISTINCT sl.proficiency_description
            FROM map_sf_to_cat_skill m
            JOIN sf_skill_level sl ON m.sf_skill_id = sl.sf_skill_id
            WHERE m.sector_name_raw = %s
              AND m.source_skill_title = %s
              AND sl.proficiency_level = %s
        """
        cur.execute(query, (sector, skill, proficiency_level))
        row = cur.fetchone()
        cur.close()
        conn.close()
        description = row[0] if row and row[0] else ""
        return [description] if description else []
    except Exception:
        return []
        return [description] if description else []
    except Exception:
        return []


def get_proficiency_description(sector, skill, proficiency):
    try:
        conn = get_mysql_conn()
        cur = conn.cursor()
        query = """
                        SELECT DISTINCT sl.proficiency_description
            FROM map_sf_to_cat_skill m
                        JOIN sf_skill_level sl
                            ON sl.sf_skill_id = m.sf_skill_id
                         AND sl.proficiency_level = m.proficiency_level
            WHERE m.sector_name_raw = %s
              AND m.source_skill_title = %s
                            AND sl.proficiency_level = %s
        """
        cur.execute(query, (sector, skill, proficiency))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row and row[0] else ""
    except Exception:
        return ""


def mongo_status_snapshot(collection):
    total_documents = collection.count_documents({})
    total_sectors = len(collection.distinct("sector"))
    total_skills = len(collection.distinct("skill_name"))

    last_24h_cutoff = datetime.now() - timedelta(hours=24)
    ingested_last_24h = collection.count_documents(
        {"ingested_timing": {"$gte": last_24h_cutoff}}
    )

    duplicate_cursor = collection.aggregate(
        [
            {
                "$group": {
                    "_id": {"videoId": "$videoId", "skill_name": "$skill_name"},
                    "count": {"$sum": 1},
                }
            },
            {"$match": {"count": {"$gt": 1}}},
            {"$count": "duplicate_groups"},
        ]
    )
    duplicate_result = list(duplicate_cursor)
    duplicate_groups = duplicate_result[0]["duplicate_groups"] if duplicate_result else 0

    recent_docs = []
    recent_cursor = collection.find(
        {},
        {
            "_id": 0,
            "videoId": 1,
            "skill_name": 1,
            "sector": 1,
            "competency": 1,
            "proficiency": 1,
            "requirement": 1,
            "title": 1,
            "ingested_timing": 1,
        },
    ).sort("ingested_timing", -1).limit(RECENT_ROWS_LIMIT)

    for doc in recent_cursor:
        ingested_timing = doc.get("ingested_timing")
        if isinstance(ingested_timing, datetime):
            doc["ingested_timing"] = ingested_timing.isoformat()
        recent_docs.append(doc)

    return {
        "database": env("MONGO_DATABASE", ""),
        "collection": "videos",
        "total_documents": total_documents,
        "total_sectors": total_sectors,
        "total_skills": total_skills,
        "ingested_last_24h": ingested_last_24h,
        "duplicate_groups": duplicate_groups,
        "recent_docs": recent_docs,
    }
