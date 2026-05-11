from __future__ import annotations


def proficiency_sort_key(value: str) -> tuple[int, str]:
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits:
        return int(digits), value.lower()
    return 9999, value.lower()


def clean_db_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "none" else text


def build_work_function_groups(rows: list[dict]) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        name = clean_db_text(row.get("work_function_name"))
        if not name:
            continue

        entry = grouped.setdefault(
            name,
            {
                "name": name,
                "key_tasks": [],
                "_seen_tasks": set(),
            },
        )

        key_task = clean_db_text(row.get("key_task_text"))
        if key_task and key_task not in entry["_seen_tasks"]:
            entry["_seen_tasks"].add(key_task)
            entry["key_tasks"].append(key_task)

    results = []
    for name in sorted(grouped.keys(), key=str.lower):
        entry = grouped[name]
        results.append(
            {
                "name": entry["name"],
                "key_tasks": entry["key_tasks"],
            }
        )
    return results


def build_job_role_skill_groups(rows: list[dict]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in rows:
        skill_title = clean_db_text(row.get("skill_title"))
        skill_type = clean_db_text(row.get("skill_type"))
        proficiency_level = clean_db_text(row.get("proficiency_level"))
        if not skill_title or not proficiency_level:
            continue

        group_key = (skill_title, skill_type, proficiency_level)
        entry = grouped.setdefault(
            group_key,
            {
                "skill_title": skill_title,
                "skill_type": skill_type,
                "proficiency_level": proficiency_level,
                "proficiency_description": clean_db_text(row.get("proficiency_description")),
                "tsc_ccs_codes": [],
                "knowledge_items": [],
                "ability_items": [],
                "_seen_codes": set(),
                "_seen_knowledge": set(),
                "_seen_ability": set(),
            },
        )

        proficiency_description = clean_db_text(row.get("proficiency_description"))
        if proficiency_description and not entry["proficiency_description"]:
            entry["proficiency_description"] = proficiency_description

        code = clean_db_text(row.get("tsc_ccs_code"))
        if code and code not in entry["_seen_codes"]:
            entry["_seen_codes"].add(code)
            entry["tsc_ccs_codes"].append(code)

        item_type = clean_db_text(row.get("item_type")).lower()
        item_text = clean_db_text(row.get("item_text"))
        if not item_type or not item_text:
            continue

        if item_type == "knowledge" and item_text not in entry["_seen_knowledge"]:
            entry["_seen_knowledge"].add(item_text)
            entry["knowledge_items"].append(item_text)
        elif item_type == "ability" and item_text not in entry["_seen_ability"]:
            entry["_seen_ability"].add(item_text)
            entry["ability_items"].append(item_text)

    results = []
    for skill_title, skill_type, proficiency_level in sorted(
        grouped.keys(),
        key=lambda item: (item[0].lower(), proficiency_sort_key(item[2]), item[1].lower()),
    ):
        entry = grouped[(skill_title, skill_type, proficiency_level)]
        results.append(
            {
                "skill_title": entry["skill_title"],
                "skill_type": entry["skill_type"],
                "proficiency_level": entry["proficiency_level"],
                "proficiency_description": entry["proficiency_description"],
                "tsc_ccs_codes": entry["tsc_ccs_codes"],
                "knowledge_items": entry["knowledge_items"],
                "ability_items": entry["ability_items"],
            }
        )
    return results
