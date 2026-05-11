import os
import pandas as pd
import mysql.connector
from dotenv import load_dotenv

load_dotenv(".env")

XLSX_PATH = "data/raw/SkillsFuture Skills Framework Dataset.xlsx"
SOURCE_FILE = os.path.basename(XLSX_PATH)

S_JOB_DESC = "Job Role_Description"
S_JOB_CWF = "Job Role_CWF_KT"
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


def clean_text(value) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def column_exists(cur, table_name: str, column_name: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        LIMIT 1;
        """,
        (table_name, column_name),
    )
    return cur.fetchone() is not None


def ensure_job_role_lookup_schema(cur) -> None:
    """Backfill schema changes for existing MySQL volumes before seeding."""
    if not column_exists(cur, "sf_job_role", "performance_expectation"):
        cur.execute(
            """
            ALTER TABLE sf_job_role
            ADD COLUMN performance_expectation TEXT NULL
            AFTER role_description;
            """
        )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sf_role_work_function (
          work_function_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
          job_role_id        BIGINT UNSIGNED NOT NULL,
          work_function_name VARCHAR(512)     NOT NULL,
          source_file        VARCHAR(128)     NULL,
          source_sheet       VARCHAR(128)     NULL,
          source_row         INT              NULL,
          ingested_at        DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (work_function_id),
          UNIQUE KEY uk_sf_role_work_function (job_role_id, work_function_name),
          KEY idx_sf_role_work_function_role (job_role_id),
          CONSTRAINT fk_sf_role_work_function_role
            FOREIGN KEY (job_role_id) REFERENCES sf_job_role(job_role_id)
            ON UPDATE CASCADE ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sf_role_key_task (
          key_task_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
          work_function_id   BIGINT UNSIGNED NOT NULL,
          key_task_text      TEXT             NOT NULL,
          source_file        VARCHAR(128)     NULL,
          source_sheet       VARCHAR(128)     NULL,
          source_row         INT              NULL,
          ingested_at        DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (key_task_id),
          UNIQUE KEY uk_sf_role_key_task (work_function_id, key_task_text(255)),
          KEY idx_sf_role_key_task_work_function (work_function_id),
          CONSTRAINT fk_sf_role_key_task_work_function
            FOREIGN KEY (work_function_id) REFERENCES sf_role_work_function(work_function_id)
            ON UPDATE CASCADE ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
        """
    )


def main():
    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    missing = 0
    try:
        ensure_job_role_lookup_schema(cur)

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
        roles = job_df[
            ["Sector", "Track", "Job Role", "Job Role Description", "Performance Expectation"]
        ].drop_duplicates()
        role_rows = []
        for _, r in roles.iterrows():
            tid = track_id[(sector_id[r["Sector"]], r["Track"])]
            role_rows.append(
                (
                    tid,
                    r["Job Role"],
                    clean_text(r.get("Job Role Description")),
                    clean_text(r.get("Performance Expectation")),
                )
            )
        cur.executemany(
            """INSERT INTO sf_job_role (track_id, job_role_name, role_description, performance_expectation)
               VALUES (%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE
                 role_description=VALUES(role_description),
                 performance_expectation=VALUES(performance_expectation);""",
            role_rows,
        )
        cur.execute("SELECT job_role_id, track_id, job_role_name FROM sf_job_role;")
        job_role_id = {(r["track_id"], r["job_role_name"]): r["job_role_id"] for r in cur.fetchall()}

        # ---------- Load critical work functions + key tasks ----------
        cwf_df = pd.read_excel(XLSX_PATH, sheet_name=S_JOB_CWF).dropna(
            subset=["Sector", "Track", "Job Role", "Critical Work Function"]
        )
        cwf_df["Sector"] = cwf_df["Sector"].astype(str).str.strip()
        cwf_df["Track"] = cwf_df["Track"].astype(str).str.strip()
        cwf_df["Job Role"] = cwf_df["Job Role"].astype(str).str.strip()
        cwf_df["Critical Work Function"] = cwf_df["Critical Work Function"].astype(str).str.strip()

        cur.execute(
            "DELETE FROM sf_role_key_task WHERE source_file=%s AND source_sheet=%s;",
            (SOURCE_FILE, S_JOB_CWF),
        )
        cur.execute(
            "DELETE FROM sf_role_work_function WHERE source_file=%s AND source_sheet=%s;",
            (SOURCE_FILE, S_JOB_CWF),
        )

        work_function_rows = []
        work_functions = cwf_df[
            ["Sector", "Track", "Job Role", "Critical Work Function"]
        ].drop_duplicates()
        for idx, r in work_functions.iterrows():
            sid = sector_id.get(r["Sector"])
            tid = track_id.get((sid, r["Track"])) if sid else None
            jr = job_role_id.get((tid, r["Job Role"])) if tid else None
            if not jr:
                continue
            work_function_rows.append(
                (
                    jr,
                    r["Critical Work Function"],
                    SOURCE_FILE,
                    S_JOB_CWF,
                    int(idx) + 2,
                )
            )

        if work_function_rows:
            cur.executemany(
                """INSERT INTO sf_role_work_function
                   (job_role_id, work_function_name, source_file, source_sheet, source_row)
                   VALUES (%s,%s,%s,%s,%s);""",
                work_function_rows,
            )

        cur.execute(
            "SELECT work_function_id, job_role_id, work_function_name FROM sf_role_work_function;"
        )
        work_function_id = {
            (r["job_role_id"], r["work_function_name"]): r["work_function_id"]
            for r in cur.fetchall()
        }

        key_task_rows = []
        for idx, r in cwf_df.iterrows():
            sid = sector_id.get(r["Sector"])
            tid = track_id.get((sid, r["Track"])) if sid else None
            jr = job_role_id.get((tid, r["Job Role"])) if tid else None
            if not jr:
                continue
            key_task_text = clean_text(r.get("Key Tasks"))
            if not key_task_text:
                continue
            wf_id = work_function_id.get((jr, r["Critical Work Function"]))
            if not wf_id:
                continue
            key_task_rows.append(
                (
                    wf_id,
                    key_task_text,
                    SOURCE_FILE,
                    S_JOB_CWF,
                    int(idx) + 2,
                )
            )

        if key_task_rows:
            cur.executemany(
                """INSERT INTO sf_role_key_task
                   (work_function_id, key_task_text, source_file, source_sheet, source_row)
                   VALUES (%s,%s,%s,%s,%s);""",
                key_task_rows,
            )

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
                    clean_text(r.get("TSC_CCS Description")),
                    clean_text(r.get("TSC_CCS Category")),
                    clean_text(r.get("Sector")),
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
                    clean_text(r.get("Proficiency Description")),
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
            item_text = clean_text(r.get("Knowledge / Ability Items"))
            if not item_text:
                continue
            item_rows.append(
                (
                    code_to_id[code],
                    r["Proficiency Level"],
                    item_type,
                    item_text,
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
