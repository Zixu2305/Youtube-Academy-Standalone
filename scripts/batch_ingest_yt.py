"""
Batch YouTube ingestion for Infocomm Technology skills.

Iterates over all competencies for 8 target skills, fetching 5 videos
per competency (minimum 5 minutes each) and upserting to MongoDB.

Usage:
    python scripts/batch_ingest_yt.py --api-key YOUR_YOUTUBE_API_KEY
    python scripts/batch_ingest_yt.py              # uses YOUTUBE_API_KEY env var
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add pipelines/youtube to path so we can import the existing modules
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "pipelines" / "youtube"))

from youtube_config import env, to_int
from youtube_data_access import get_mongo_client, get_mysql_conn


SECTOR = "Infocomm Technology"
TARGET_SKILLS = [
    "Data Analytics",
    "User Interface Design",
    "Software Testing",
    "Networking",
    "Data Engineering",
    "Design Thinking Practice",
    "Solution Architecture",
    "Artificial Intelligence Ethics and Governance",
]

VIDEOS_PER_COMPETENCY = 5
MIN_VIDEO_LENGTH_MINUTES = 5


def fetch_competency_tuples(sector: str, skills: list[str]) -> list[dict]:
    """
    Query MySQL for all (skill, competency, proficiency_level, proficiency_description)
    tuples for the given sector and skills.
    """
    conn = get_mysql_conn()
    cur = conn.cursor()

    placeholders = ", ".join(["%s"] * len(skills))
    query = f"""
        SELECT DISTINCT
            m.source_skill_title                          AS skill_name,
            CONCAT(sci.item_type, ': ', sci.item_text)    AS competency,
            sci.proficiency_level                         AS proficiency_level,
            COALESCE(sl.proficiency_description, '')      AS proficiency_description
        FROM map_sf_to_cat_skill m
        JOIN sf_competency_item sci
            ON sci.sf_skill_id = m.sf_skill_id
        JOIN sf_skill_level sl
            ON sl.sf_skill_id  = sci.sf_skill_id
           AND sl.proficiency_level = sci.proficiency_level
        WHERE m.sector_name_raw = %s
          AND m.source_skill_title IN ({placeholders})
        ORDER BY m.source_skill_title, sci.proficiency_level, competency
    """
    params = [sector] + skills
    cur.execute(query, params)
    columns = [desc[0] for desc in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def main():
    parser = argparse.ArgumentParser(description="Batch YouTube ingestion for Infocomm Technology skills")
    parser.add_argument("--api-key", default=None, help="YouTube Data API v3 key (or set YOUTUBE_API_KEY env var)")
    args = parser.parse_args()

    api_key = args.api_key or env("YOUTUBE_API_KEY", "")
    if not api_key:
        print("ERROR: No API key provided. Use --api-key or set YOUTUBE_API_KEY env var.")
        sys.exit(1)

    # Import run_ingestion here (after sys.path is set)
    from youtube_ingestion_service import run_ingestion

    # Connect to MongoDB
    mongo_client = get_mongo_client()
    db_name = env("MONGO_DATABASE", "yta")
    db = mongo_client[db_name]
    collection = db["videos"]

    # Fetch all competency tuples from MySQL
    print(f"Fetching competencies for {len(TARGET_SKILLS)} skills in '{SECTOR}'...")
    tuples = fetch_competency_tuples(SECTOR, TARGET_SKILLS)
    print(f"Found {len(tuples)} competency tuples to process.\n")

    if not tuples:
        print("No competencies found. Check that SkillsFuture data is seeded in MySQL.")
        sys.exit(1)

    # Track overall stats
    total_inserted = 0
    total_updated = 0
    total_unchanged = 0
    total_errors = 0
    quota_exceeded = False
    processed = 0

    for t in tuples:
        if quota_exceeded:
            print("\nQuota exceeded — stopping.")
            break

        processed += 1
        skill = t["skill_name"]
        competency = t["competency"]
        prof_level = t["proficiency_level"]
        prof_desc = t["proficiency_description"]

        print(f"[{processed}/{len(tuples)}] {skill} | Level {prof_level} | {competency[:60]}...")

        summary = run_ingestion(
            collection,
            sector=SECTOR,
            api_key=api_key,
            search_max_results=VIDEOS_PER_COMPETENCY,
            search_order="relevance",
            selected_skills=[skill],
            competency=competency,
            proficiency=prof_level,
            requirement=prof_desc,
            min_video_length=MIN_VIDEO_LENGTH_MINUTES,
        )

        ins = summary.get("inserted", 0)
        upd = summary.get("updated", 0)
        unc = summary.get("unchanged", 0)
        errs = summary.get("error_count", 0)

        total_inserted += ins
        total_updated += upd
        total_unchanged += unc
        total_errors += errs

        if summary.get("quota_exceeded"):
            quota_exceeded = True

        print(f"  -> inserted={ins}, updated={upd}, unchanged={unc}, errors={errs}")

        if summary.get("errors"):
            for e in summary["errors"][:3]:
                print(f"     ! {e}")

    mongo_client.close()

    # Final summary
    print("\n" + "=" * 60)
    print("BATCH INGESTION COMPLETE")
    print("=" * 60)
    print(f"Sector:           {SECTOR}")
    print(f"Skills:           {len(TARGET_SKILLS)}")
    print(f"Competencies:     {len(tuples)}")
    print(f"Processed:        {processed}/{len(tuples)}")
    print(f"Videos inserted:  {total_inserted}")
    print(f"Videos updated:   {total_updated}")
    print(f"Videos unchanged: {total_unchanged}")
    print(f"Errors:           {total_errors}")
    print(f"Quota exceeded:   {quota_exceeded}")


if __name__ == "__main__":
    main()
