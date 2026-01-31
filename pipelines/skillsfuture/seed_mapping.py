import os
import re
import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env")

XLSX_PATH = "data/raw/Skills Mapping Framework.xlsx"
SHEET = "TSC to Unique Skill Mapping"
SOURCE_FILE = os.path.basename(XLSX_PATH)

def norm_title(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

import mysql.connector

def get_conn():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "mysql"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "yta"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "yta"),
        autocommit=False,
    )


def main():
    df = pd.read_excel(XLSX_PATH, sheet_name=SHEET).dropna(subset=["tsc_code","proficiency_level","parent_skill_title"])
    df["tsc_code"] = df["tsc_code"].astype(str).str.strip()
    df["proficiency_level"] = df["proficiency_level"].astype(str).str.strip()
    df["sector_title"] = df["sector_title"].astype(str).str.strip()
    df["parent_skill_title"] = df["parent_skill_title"].astype(str).str.strip()
    df["title_norm"] = df["parent_skill_title"].map(norm_title)

    conn = get_conn()
    try:
        with conn.cursor(mysql.connector.cursor.MySQLCursorDict) as cur:
            # lookup maps
            cur.execute("SELECT skill_id, title_norm FROM cat_skill;")
            cat = {title_norm: skill_id for (skill_id, title_norm) in cur.fetchall()}

            cur.execute("SELECT sector_id, sector_name FROM sf_sector;")
            sector = {sector_name: sector_id for (sector_id, sector_name) in cur.fetchall()}

            cur.execute("SELECT sf_skill_id, tsc_ccs_code FROM sf_skill;")
            sf = {tsc_ccs_code: sf_skill_id for (sf_skill_id, tsc_ccs_code) in cur.fetchall()}

            # delete only this source (idempotent)
            cur.execute("DELETE FROM map_sf_to_cat_skill WHERE source_file=%s AND source_sheet=%s;", (SOURCE_FILE, SHEET))

            rows = []
            miss_cat = 0
            miss_sf = 0
            fixed_sf = 0

            missing_sf_rows = []
            fixed_sf_rows = []

            for idx, r in df.iterrows():
                excel_row = int(idx) + 2  # header is row 1
                code_raw = str(r["tsc_code"]).strip()
                code = code_raw
                used_code = code

                sf_skill_id = sf.get(code)
                fixed = False

                # Variant fix: some sources append "-1" suffix (e.g., ECC-...-1.1 -> ECC-...-1.1-1)
                if not sf_skill_id:
                    alt = f"{code}-1"
                    if not code.endswith("-1") and alt in sf:
                        used_code = alt
                        sf_skill_id = sf[alt]
                        fixed = True
                        fixed_sf += 1
                        fixed_sf_rows.append({
                            "excel_row": excel_row,
                            "tsc_code_raw": code_raw,
                            "tsc_code_used": used_code,
                            "sector_title": r.get("sector_title"),
                            "proficiency_level": r.get("proficiency_level"),
                            "skill_11k_title": r.get("skill_11k_title"),
                            "parent_skill_title": r.get("parent_skill_title"),
                            "note": "auto_fix: appended -1 suffix",
                        })
                    else:
                        miss_sf += 1
                        missing_sf_rows.append({
                            "excel_row": excel_row,
                            "tsc_code_raw": code_raw,
                            "sector_title": r.get("sector_title"),
                            "proficiency_level": r.get("proficiency_level"),
                            "skill_11k_title": r.get("skill_11k_title"),
                            "parent_skill_title": r.get("parent_skill_title"),
                            "suggested_code": alt if not code.endswith("-1") else "",
                            "reason": "sf_skill_id not found for tsc_code (and no -1 variant)",
                        })
                        continue

                skill_id = cat.get(r["title_norm"])
                if not skill_id:
                    miss_cat += 1
                    # Keep going so we can report; mapping insert is skipped.
                    continue

                sector_id = sector.get(str(r.get("sector_title", "")).strip())  # may be None; allowed

                rows.append((
                    sf_skill_id,
                    str(r["proficiency_level"]).strip(),
                    skill_id,
                    sector_id,
                    str(r.get("sector_title", "")).strip(),
                    r.get("skill_11k_title"),
                    SOURCE_FILE, SHEET, excel_row
                ))

            # Export reports (persist if you mount ./out:/app/out for the seed container)
            os.makedirs("out", exist_ok=True)
            if fixed_sf_rows:
                pd.DataFrame(fixed_sf_rows).to_csv("out/fixed_sf_skill_variants.csv", index=False)
            if missing_sf_rows:
                pd.DataFrame(missing_sf_rows).to_csv("out/missing_sf_skill_lookups.csv", index=False)
            cur.executemany(
                """INSERT INTO map_sf_to_cat_skill
                   (sf_skill_id, proficiency_level, skill_id, sector_id, sector_name_raw, source_skill_title,
                    source_file, source_sheet, source_row)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s);""",
                rows
            )

        conn.commit()
        print(f"✅ map_sf_to_cat_skill inserted: {len(rows)}")
        if fixed_sf:
            print(f"🛠️ Applied sf_skill variant fixes (-1 suffix): {fixed_sf} (see out/fixed_sf_skill_variants.csv)")
        if miss_sf or miss_cat:
            msg = f"⚠️ Missing lookups: sf_skill missing={miss_sf}, cat_skill missing={miss_cat}"
            if miss_sf:
                msg += " (see out/missing_sf_skill_lookups.csv)"
            print(msg)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
