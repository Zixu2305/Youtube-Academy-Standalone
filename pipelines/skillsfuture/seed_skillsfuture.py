import os
import pandas as pd
import mysql.connector
from dotenv import load_dotenv

load_dotenv(".env")

XLSX_PATH = "data/raw/SkillsFuture Skills Framework Dataset.xlsx"
SOURCE_FILE = os.path.basename(XLSX_PATH)

S_JOB_DESC = "Job Role_Description"
S_JOB_SKILL = "Job Role_TCS_CCS"
S_KEY = "TSC_CCS_Key"
S_RETIRED = "TSC_CCS_Key_Retired"
S_KA = "TSC_CCS_K&A"


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def get_conn():
    # Local dev typically uses 127.0.0.1:3307; docker-to-docker uses mysql:3306.
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
        autocommit=False,
    )


def fetch_map(cur, sql, key_cols):
    """Utility if you ever need keyed lookups from SELECTs.

    Note: requires cursor(dictionary=True).
    """
    cur.execute(sql)
    m = {}
    for row in cur.fetchall():
        key = tuple(row[c] for c in key_cols)
        m[key] = row
    return m

def main():
    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    missing = 0
    try:
        # ---------- Load sectors / tracks / job roles ----------
        job_df = pd.read_excel(XLSX_PATH, sheet_name=S_JOB_DESC).dropna(subset=["Sector", "Track", "Job Role"])
        job_df["Sector"] = job_df["Sector"].astype(str).str.strip()
        job_df["Track"] = job_df["Track"].astype(str).str.strip()
        job_df["Job Role"] = job_df["Job Role"].astype(str).str.strip()

        # sectors
        sectors = sorted(job_df["Sector"].unique().tolist())
        cur.executemany(
            "INSERT INTO sf_sector (sector_name) VALUES (%s) ON DUPLICATE KEY UPDATE sector_name=VALUES(sector_name);",
            [(s,) for s in sectors],
        )
        cur.execute("SELECT sector_id, sector_name FROM sf_sector;")
        sector_id = {r["sector_name"]: r["sector_id"] for r in cur.fetchall()}

        # tracks
        tracks = job_df[["Sector", "Track"]].drop_duplicates()
        track_rows = [(sector_id[r["Sector"]], r["Track"]) for _, r in tracks.iterrows()]
        cur.executemany(
            """INSERT INTO sf_track (sector_id, track_name)
               VALUES (%s,%s)
               ON DUPLICATE KEY UPDATE track_name=VALUES(track_name);""",
            track_rows,
        )
        cur.execute("SELECT track_id, sector_id, track_name FROM sf_track;")
        track_id = {(r["sector_id"], r["track_name"]): r["track_id"] for r in cur.fetchall()}

        # job roles
        roles = job_df[["Sector", "Track", "Job Role", "Job Role Description"]].drop_duplicates()
        role_rows = []
        for _, r in roles.iterrows():
            tid = track_id[(sector_id[r["Sector"]], r["Track"])]
            role_rows.append((tid, r["Job Role"], r.get("Job Role Description")))
        cur.executemany(
            """INSERT INTO sf_job_role (track_id, job_role_name, role_description)
               VALUES (%s,%s,%s)
               ON DUPLICATE KEY UPDATE role_description=VALUES(role_description);""",
            role_rows,
        )
        cur.execute("SELECT job_role_id, track_id, job_role_name FROM sf_job_role;")
        job_role_id = {(r["track_id"], r["job_role_name"]): r["job_role_id"] for r in cur.fetchall()}

        # ---------- Load sf_skill (TSC_CCS_Key + retired) ----------
        key_df = pd.read_excel(XLSX_PATH, sheet_name=S_KEY).dropna(subset=["TSC Code", "TSC_CCS Title"])
        key_df["TSC Code"] = key_df["TSC Code"].astype(str).str.strip()
        retired_df = pd.read_excel(XLSX_PATH, sheet_name=S_RETIRED).dropna(subset=["TSC Code"])
        retired_codes = set(retired_df["TSC Code"].astype(str).str.strip().tolist())

        skill_rows = []
        for idx, r in key_df.iterrows():
            code = r["TSC Code"]
            skill_rows.append(
                (
                    code,
                    str(r.get("TSC_CCS Title", "")).strip(),
                    r.get("TSC_CCS Description"),
                    r.get("TSC_CCS Category"),
                    r.get("Sector"),
                    str(r.get("TSC_CCS Type", "")).strip().lower()
                    if pd.notna(r.get("TSC_CCS Type"))
                    else None,
                    1 if code in retired_codes else 0,
                    SOURCE_FILE,
                    S_KEY,
                    int(idx) + 2,
                )
            )

        cur.executemany(
            """INSERT INTO sf_skill
               (tsc_ccs_code,title,description,category,sector_name,skill_type,is_retired,
                source_file,source_sheet,source_row)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE
                 title=VALUES(title),
                 description=VALUES(description),
                 category=VALUES(category),
                 sector_name=VALUES(sector_name),
                 skill_type=VALUES(skill_type),
                 is_retired=VALUES(is_retired),
                 source_file=VALUES(source_file),
                 source_sheet=VALUES(source_sheet),
                 source_row=VALUES(source_row);""",
            skill_rows,
        )

        # map code -> sf_skill_id
        cur.execute("SELECT sf_skill_id, tsc_ccs_code FROM sf_skill;")
        code_to_id = {r["tsc_ccs_code"]: r["sf_skill_id"] for r in cur.fetchall()}

        # ---------- Load sf_skill_level + sf_competency_item (TSC_CCS_K&A) ----------
        ka_df = pd.read_excel(XLSX_PATH, sheet_name=S_KA).dropna(
            subset=["TSC_CCS Code", "Proficiency Level", "Knowledge / Ability Items"]
        )
        ka_df["TSC_CCS Code"] = ka_df["TSC_CCS Code"].astype(str).str.strip()
        ka_df["Proficiency Level"] = ka_df["Proficiency Level"].astype(str).str.strip()
        ka_df["Knowledge / Ability Classification"] = (
            ka_df["Knowledge / Ability Classification"].astype(str).str.strip().str.lower()
        )

        # upsert skill levels
        levels = ka_df[["TSC_CCS Code", "Proficiency Level", "Proficiency Description"]].drop_duplicates()
        level_rows = []
        for idx, r in levels.iterrows():
            code = r["TSC_CCS Code"]
            if code not in code_to_id:
                continue
            level_rows.append(
                (
                    code_to_id[code],
                    r["Proficiency Level"],
                    r.get("Proficiency Description"),
                    SOURCE_FILE,
                    S_KA,
                    int(idx) + 2,
                )
            )
        cur.executemany(
            """INSERT INTO sf_skill_level
               (sf_skill_id, proficiency_level, proficiency_description, source_file, source_sheet, source_row)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE
                 proficiency_description=VALUES(proficiency_description),
                 source_file=VALUES(source_file),
                 source_sheet=VALUES(source_sheet),
                 source_row=VALUES(source_row);""",
            level_rows,
        )

        # idempotency for competency items (no unique key)
        cur.execute(
            "DELETE FROM sf_competency_item WHERE source_file=%s AND source_sheet=%s;",
            (SOURCE_FILE, S_KA),
        )

        item_rows = []
        for idx, r in ka_df.iterrows():
            code = r["TSC_CCS Code"]
            if code not in code_to_id:
                continue
            cls = r["Knowledge / Ability Classification"]
            item_type = "knowledge" if cls == "knowledge" else "ability"
            item_rows.append(
                (
                    code_to_id[code],
                    r["Proficiency Level"],
                    item_type,
                    str(r["Knowledge / Ability Items"]).strip(),
                    SOURCE_FILE,
                    S_KA,
                    int(idx) + 2,
                )
            )
        cur.executemany(
            """INSERT INTO sf_competency_item
               (sf_skill_id, proficiency_level, item_type, item_text, source_file, source_sheet, source_row)
               VALUES (%s,%s,%s,%s,%s,%s,%s);""",
            item_rows,
        )

        # ---------- Load role → skill requirements ----------
        rs_df = pd.read_excel(XLSX_PATH, sheet_name=S_JOB_SKILL).dropna(
            subset=["Sector", "Track", "Job Role", "TSC_CCS Code", "Proficiency Level"]
        )
        rs_df["Sector"] = rs_df["Sector"].astype(str).str.strip()
        rs_df["Track"] = rs_df["Track"].astype(str).str.strip()
        rs_df["Job Role"] = rs_df["Job Role"].astype(str).str.strip()
        rs_df["TSC_CCS Code"] = rs_df["TSC_CCS Code"].astype(str).str.strip()
        rs_df["Proficiency Level"] = rs_df["Proficiency Level"].astype(str).str.strip()

        req_rows = []
        missing = 0
        for idx, r in rs_df.iterrows():
            sid = sector_id.get(r["Sector"])
            tid = track_id.get((sid, r["Track"])) if sid else None
            jr = job_role_id.get((tid, r["Job Role"])) if tid else None
            sk = code_to_id.get(r["TSC_CCS Code"])
            if not (jr and sk):
                missing += 1
                continue
            req_rows.append(
                (jr, sk, r["Proficiency Level"], None, SOURCE_FILE, S_JOB_SKILL, int(idx) + 2)
            )

        cur.executemany(
            """INSERT INTO sf_role_skill_req
               (job_role_id, sf_skill_id, proficiency_level, req_type, source_file, source_sheet, source_row)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE
                 req_type=VALUES(req_type),
                 source_file=VALUES(source_file),
                 source_sheet=VALUES(source_sheet),
                 source_row=VALUES(source_row);""",
            req_rows,
        )

        conn.commit()
        print("✅ SkillsFuture seeded.")
        if missing:
            print(
                f"⚠️ Skipped {missing} role-skill rows due to missing role/skill id (usually naming drift)."
            )
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()

if __name__ == "__main__":
    main()
