import os
import re

import pandas as pd
import mysql.connector
from dotenv import load_dotenv

# Load env from repo root if present (works both locally and in the seed container)
load_dotenv(".env")

XLSX_PATH = "data/raw/Unique Skills List.xlsx"
SHEET = "Unique Skills List"
SOURCE_FILE = os.path.basename(XLSX_PATH)

def norm_title(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

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


def main():
    df = pd.read_excel(XLSX_PATH, sheet_name=SHEET)
    # expected columns:
    # parent_skill_title, parent_skill_description, skill_type, Emerging Skills, CASL Skills

    df = df.dropna(subset=["parent_skill_title"]).copy()
    df["title"] = df["parent_skill_title"].astype(str).str.strip()
    df["title_norm"] = df["title"].map(norm_title)
    df["description"] = df["parent_skill_description"].astype(str).replace({"nan": None})
    df["skill_type"] = df["skill_type"].astype(str).str.strip().str.lower()

    # booleans can come as True/False already; normalize to 0/1
    df["is_emerging"] = df["Emerging Skills"].fillna(False).astype(bool).astype(int)
    df["is_casl"] = df["CASL Skills"].fillna(False).astype(bool).astype(int)

    rows = []
    for idx, r in df.iterrows():
        rows.append((
            r["title"],
            r["title_norm"],
            None if r["description"] in (None, "None", "nan") else r["description"],
            r["skill_type"],
            int(r["is_emerging"]),
            int(r["is_casl"]),
            SOURCE_FILE,
            SHEET,
            int(idx) + 2,  # +2 because excel header row is 1
        ))

    sql = """
    INSERT INTO cat_skill
      (title, title_norm, description, skill_type, is_emerging, is_casl,
       source_file, source_sheet, source_row)
    VALUES
      (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
      title=VALUES(title),
      description=VALUES(description),
      skill_type=VALUES(skill_type),
      is_emerging=VALUES(is_emerging),
      is_casl=VALUES(is_casl),
      source_file=VALUES(source_file),
      source_sheet=VALUES(source_sheet),
      source_row=VALUES(source_row);
    """

    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        # chunked insert
        CHUNK = 2000
        for i in range(0, len(rows), CHUNK):
            cur.executemany(sql, rows[i:i+CHUNK])
        conn.commit()
        print(f"✅ cat_skill upserted: {len(rows)} rows")
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
