import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from bson import ObjectId

# Make pipelines.quiz_gen importable when the app runs from this subdirectory
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from youtube_config import (
    ALLOWED_SEARCH_ORDERS,
    DEFAULT_DAILY_QUOTA_LIMIT,
    DEFAULT_QUOTA_WARNING_THRESHOLD,
    SEARCH_MAX_RESULTS_MAX,
    SEARCH_MAX_RESULTS_MIN,
    VIDEO_AGE_UNITS,
    VIDEO_AGE_UNIT_TO_DAYS,
    env,
    to_int,
)
from youtube_data_access import (
    fetch_sectors_and_skills,
    get_mongo_client,
    mongo_status_snapshot,
    search_skills,
    search_competencies,
    search_proficiency_levels,
    get_requirement,
)
from youtube_ingestion_service import build_quota_estimate, run_ingestion
from youtube_vector_index import (
    DEFAULT_COLLECTION_NAME,
    delete_video_points,
    embed_and_upsert_videos,
    normalize_video_mappings,
    sync_video_mapping_payload,
)


def _daily_quota_limit():
    return to_int(
        env("YOUTUBE_DAILY_QUOTA_LIMIT", str(DEFAULT_DAILY_QUOTA_LIMIT)),
        DEFAULT_DAILY_QUOTA_LIMIT,
    )


def _quota_warning_threshold():
    return to_int(
        env("YOUTUBE_QUOTA_WARNING_THRESHOLD", str(DEFAULT_QUOTA_WARNING_THRESHOLD)),
        DEFAULT_QUOTA_WARNING_THRESHOLD,
    )


def _get_videos_collection():
    client = get_mongo_client()
    database_name = env("MONGO_DATABASE", env("DB_NAME", "yta"))
    return client, client[database_name]["videos"]


def _mapping_key(mapping: dict) -> tuple[str, str]:
    return (
        str(mapping.get("proficiency_level") or "").strip(),
        str(mapping.get("competency") or "").strip(),
    )


def _mapping_from_single_fields(doc: dict) -> dict | None:
    competency = str(doc.get("competency") or "").strip()
    if not competency:
        return None
    return {
        "competency": competency,
        "item_type": str(doc.get("item_type") or ""),
        "proficiency_level": str(doc.get("proficiency_level") or ""),
        "proficiency_description": str(doc.get("proficiency_description") or ""),
    }


def _current_mappings_from_doc(doc: dict) -> list[dict]:
    return normalize_video_mappings(doc)


def _normalize_video_id(video_id: str) -> str:
    return str(video_id or "").strip()


def _normalize_review_mapping(mapping: dict) -> dict:
    item_type = str(mapping.get("item_type") or "").strip().lower()
    competency = str(mapping.get("competency") or "").strip()
    if ":" in competency:
        prefix, text = competency.split(":", 1)
        item_type = item_type or prefix.strip().lower()
        competency = f"{prefix.strip().lower()}: {text.strip()}"

    return {
        "sector": str(mapping.get("sector") or "").strip(),
        "skill": str(mapping.get("skill") or mapping.get("skill_name") or "").strip(),
        "proficiency_level": str(mapping.get("proficiency_level") or "").strip(),
        "proficiency_description": str(mapping.get("proficiency_description") or "").strip(),
        "competency": competency,
        "item_type": item_type,
        "confidence": float(mapping.get("confidence") or 0),
        "source": str(mapping.get("source") or "admin").strip() or "admin",
    }


def _validate_review_mappings(mappings: list[dict]) -> tuple[list[dict], str]:
    normalized = [_normalize_review_mapping(item) for item in mappings if isinstance(item, dict)]
    complete = [
        item for item in normalized
        if item["sector"] and item["skill"] and item["proficiency_level"] and item["competency"] and item["item_type"]
    ]
    if not complete:
        return [], "Add at least one complete mapping."

    errors = []
    for item in complete:
        if item["item_type"] not in {"knowledge", "ability"}:
            errors.append(f"{item['skill']}: item type must be knowledge or ability.")
            continue

        levels = search_proficiency_levels(item["sector"], item["skill"])
        level_match = next(
            (
                level for level in levels
                if str(level.get("proficiency_level") or "") == item["proficiency_level"]
            ),
            None,
        )
        if not level_match:
            errors.append(
                f"{item['sector']} / {item['skill']} / level {item['proficiency_level']} is not in the seeded mapping."
            )
            continue

        allowed_competencies = search_competencies(item["sector"], item["skill"], item["proficiency_level"])
        if item["competency"] not in allowed_competencies:
            errors.append(f"{item['competency']} is not a seeded competency for {item['skill']}.")
            continue

        if not item["proficiency_description"]:
            item["proficiency_description"] = str(level_match.get("proficiency_description") or "")

    if errors:
        return [], " ".join(errors)
    return complete, ""


def _make_mapping_review_response(doc: dict) -> dict:
    review = doc.get("mapping_review") or {}
    suggested_mappings = review.get("suggested_mappings") or []
    suggestion_status = str(review.get("suggestion_status") or "").strip()
    if not suggestion_status:
        suggestion_status = "ready" if suggested_mappings else "none"
    return {
        "video_id": str(doc.get("videoId") or ""),
        "review_status": str(doc.get("review_status") or review.get("status") or "pending"),
        "title": str(doc.get("title") or ""),
        "description": str(doc.get("description") or ""),
        "channel_title": str(doc.get("channelTitle") or ""),
        "thumbnail_url": str(doc.get("thumbnailUrl") or ""),
        "search_query": str(review.get("search_query") or ""),
        "suggestion_status": suggestion_status,
        "suggestion_error": str(review.get("suggestion_error") or ""),
        "suggested_mappings": [_normalize_review_mapping(item) for item in suggested_mappings],
        "approved_mappings": [_normalize_review_mapping(item) for item in (review.get("approved_mappings") or [])],
        "created_at": _serialize(review.get("created_at") or doc.get("ingested_timing")),
        "updated_at": _serialize(review.get("updated_at")),
        "reviewed_at": _serialize(review.get("reviewed_at")),
        "rejection_reason": str(review.get("rejection_reason") or ""),
    }


def _mapping_to_video_doc(base_video: dict, mapping: dict, reviewer: str, reviewed_at: datetime) -> dict:
    doc = {key: value for key, value in base_video.items() if key != "_id"}
    doc.update(
        {
            "sector": mapping["sector"],
            "skill_name": mapping["skill"],
            "competency": mapping["competency"],
            "item_type": mapping["item_type"],
            "proficiency_level": mapping["proficiency_level"],
            "proficiency_description": mapping.get("proficiency_description", ""),
            "review_status": "approved",
            "approved_mappings": [mapping],
            "suggested_mappings": base_video.get("suggested_mappings") or [],
            "mapping_review": {
                **(base_video.get("mapping_review") or {}),
                "status": "approved",
                "is_request": False,
                "approved_mappings": [mapping],
                "reviewed_at": reviewed_at,
                "updated_at": reviewed_at,
                "reviewer": reviewer,
            },
            "approved_at": reviewed_at,
        }
    )
    return doc


def _unpublish_approved_video(videos_collection, video_id: str) -> dict:
    qdrant_summary = delete_video_points(video_id)
    delete_result = videos_collection.delete_many(
        {
            "videoId": video_id,
            "review_status": "approved",
            "mapping_review.is_request": {"$ne": True},
        }
    )
    return {
        "approved_deleted_count": delete_result.deleted_count,
        **qdrant_summary,
    }


def _embed_touched_videos(summary: dict, docs: list[dict]) -> None:
    summary.setdefault("errors", [])
    try:
        embedding_summary = embed_and_upsert_videos(docs)
    except Exception as exc:
        embedding_summary = {
            "embedding_status": "failed",
            "embedding_requested": len(docs),
            "embedding_indexed": 0,
            "embedding_skipped_invalid": 0,
            "embedding_batch_size": None,
            "embedding_collection": env("QDRANT_YT_COLLECTION", DEFAULT_COLLECTION_NAME),
        }
        summary["errors"].append(f"[embedding] {exc}")

    summary.update(embedding_summary)
    summary["error_count"] = len(summary.get("errors", []))


def _serialize(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {key: _serialize(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


TITLE_SEARCH_FIELDS = ("title", "video_title", "videoTitle", "snippet.title", "metadata.title")
TITLE_TOKEN_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


def _field_regex_filter(field: str, value: str) -> dict:
    return {field: {"$regex": re.escape(value), "$options": "i"}}


def _build_flexible_text_filter(fields: tuple[str, ...], value: str) -> dict:
    filter_value = (value or "").strip()
    literal_filters = [_field_regex_filter(field, filter_value) for field in fields]
    seen_tokens: set[str] = set()
    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z0-9]+", filter_value):
        normalized = token.lower()
        if len(normalized) < 3 or normalized in TITLE_TOKEN_STOPWORDS or normalized in seen_tokens:
            continue
        seen_tokens.add(normalized)
        tokens.append(token)

    token_filter = {}
    if tokens:
        important_tokens = sorted(tokens, key=len, reverse=True)[:5]
        token_filter = {
            "$and": [
                {"$or": [_field_regex_filter(field, token) for field in fields]}
                for token in important_tokens
            ]
        }

    if token_filter:
        return {"$or": literal_filters + [token_filter]}
    if len(literal_filters) == 1:
        return literal_filters[0]
    return {"$or": literal_filters}


def _build_mongo_filter(field: str, value: str, collection_name: str = "") -> dict:
    filter_field = (field or "").strip()
    filter_value = (value or "").strip()
    selected_collection = (collection_name or "").strip()

    if not filter_field or not filter_value:
        return {}

    # Backward-compatible aliases from UI field names.
    if filter_field == "skill":
        filter_field = "skill_name" if selected_collection == "videos" else "skill"
    elif filter_field == "video_id":
        filter_field = "videoId"
        watch_match = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{6,})", filter_value)
        if watch_match:
            filter_value = watch_match.group(1)
    elif selected_collection == "videos" and filter_field == "title":
        return _build_flexible_text_filter(TITLE_SEARCH_FIELDS, filter_value)
    elif selected_collection == "videos" and filter_field == "doc_type":
        normalized_value = filter_value.lower()
        if normalized_value in {"review", "request", "review request", "pending request"}:
            return {"mapping_review.is_request": True}
        if normalized_value in {"live", "approved", "approved mapping", "mapping"}:
            return {"mapping_review.is_request": {"$ne": True}}
        return {}

    allowed_fields_by_collection = {
        "videos": {
            "sector",
            "skill_name",
            "videoId",
            "title",
            "competency",
            "proficiency_level",
            "review_status",
            "doc_type",
        },
        "Quiz_Generation": {
            "sector",
            "skill",
            "competency",
            "proficiency_level",
            "question",
        },
        "ingestion_runs": {
            "run_id",
            "status",
        },
    }
    fallback_allowed_fields = {
        "sector",
        "skill_name",
        "skill",
        "videoId",
        "title",
        "competency",
        "proficiency_level",
        "question",
        "run_id",
        "status",
    }

    allowed_fields = allowed_fields_by_collection.get(selected_collection, fallback_allowed_fields)
    if filter_field not in allowed_fields:
        return {}

    return _field_regex_filter(filter_field, filter_value)


def _to_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_fetch_payload(req):
    if req.is_json:
        data = req.get_json(silent=True) or {}
        payload = {
            "sector": data.get("sector", "") or "",
            "api_key": data.get("api_key", "") or "",
            "search_max_results": to_int(data.get("search_max_results", 5), 5),
            "search_order": data.get("search_order", "relevance") or "relevance",
            "selected_skills": data.get("skills", []),
            "competency": data.get("competency", "") or "",
            "proficiency": data.get("proficiency", "") or "",
            "requirement": data.get("requirement", "") or "",
            "include_sector": _to_bool(data.get("include_sector", False)),
            "include_skill": _to_bool(data.get("include_skill", False)),
            "include_competency": _to_bool(data.get("include_competency", False)),
            "include_requirement": _to_bool(data.get("include_requirement", False)),
            "published_after": data.get("published_after", "") or "",
            "max_video_age": to_int(data.get("max_video_age", 0), 0),
            "video_age_unit": data.get("video_age_unit", "days") or "days",
            "additional_query": data.get("additional_query", "") or "",
            "min_view_count": to_int(data.get("min_view_count", 0), 0),
            "min_like_count": to_int(data.get("min_like_count", 0), 0),
            "min_video_length": to_int(data.get("min_video_length", 0), 0),
            "max_video_length": to_int(data.get("max_video_length", 0), 0),
            "min_comment_count": to_int(data.get("min_comment_count", 0), 0),
        }
        return payload

    selected_skills = req.form.getlist("skills")
    selected_skills_json = req.form.get("selected_skills_json", "")
    if selected_skills_json:
        try:
            selected_skills = json.loads(selected_skills_json)
        except json.JSONDecodeError:
            pass

    payload = {
        "sector": req.form.get("sector", ""),
        "api_key": req.form.get("api_key", ""),
        "search_max_results": to_int(req.form.get("search_max_results", 5), 5),
        "search_order": req.form.get("search_order", "relevance"),
        "selected_skills": selected_skills,
        "competency": req.form.get("competency", ""),
        "proficiency": req.form.get("proficiency", ""),
        "requirement": req.form.get("requirement", ""),
        "include_sector": _to_bool(req.form.get("include_sector", "false")),
        "include_skill": _to_bool(req.form.get("include_skill", "false")),
        "include_competency": _to_bool(req.form.get("include_competency", "false")),
        "include_requirement": _to_bool(req.form.get("include_requirement", "false")),
        "published_after": req.form.get("published_after", ""),
        "max_video_age": to_int(req.form.get("max_video_age", 0), 0),
        "video_age_unit": req.form.get("video_age_unit", "days"),
        "additional_query": req.form.get("additional_query", ""),
        "min_view_count": to_int(req.form.get("min_view_count", 0), 0),
        "min_like_count": to_int(req.form.get("min_like_count", 0), 0),
        "min_video_length": to_int(req.form.get("min_video_length", 0), 0),
        "max_video_length": to_int(req.form.get("max_video_length", 0), 0),
        "min_comment_count": to_int(req.form.get("min_comment_count", 0), 0),
    }
    return payload


def _resolve_video_age_to_published_after(payload):
    """If max_video_age is set, compute published_after from it.

    If published_after is already set, video_age is ignored.
    Mutates *payload* in place.
    """
    age = payload.get("max_video_age", 0)
    if age <= 0:
        return
    if payload.get("published_after", "").strip():
        # Manual date takes precedence; clear the age so it doesn't confuse
        # downstream code.
        return
    unit = payload.get("video_age_unit", "days")
    multiplier = VIDEO_AGE_UNIT_TO_DAYS.get(unit, 1)
    total_days = age * multiplier
    payload["max_video_age"] = total_days  # normalize to days for the service
    cutoff = datetime.now() - timedelta(days=total_days)
    payload["published_after"] = cutoff.strftime("%Y-%m-%d")


def _validate_fetch_payload(payload):
    """Return a list of error strings (empty list == valid)."""
    errors = []

    # ── Required fields ──────────────────────────────────────────
    if not payload["sector"]:
        errors.append("Sector is required.")
    if not payload["api_key"]:
        errors.append("YouTube API key is required.")
    if not payload["selected_skills"]:
        errors.append("Select a skill.")

    # ── search_max_results ───────────────────────────────────────
    smr = payload["search_max_results"]
    if smr < SEARCH_MAX_RESULTS_MIN or smr > SEARCH_MAX_RESULTS_MAX:
        errors.append(
            f"Search Max Results must be between {SEARCH_MAX_RESULTS_MIN} and {SEARCH_MAX_RESULTS_MAX}."
        )

    # ── search_order ─────────────────────────────────────────────
    if payload["search_order"] not in ALLOWED_SEARCH_ORDERS:
        errors.append(
            f"Search Order must be one of: {', '.join(sorted(ALLOWED_SEARCH_ORDERS))}."
        )

    # ── published_after ──────────────────────────────────────────
    pa = payload.get("published_after", "").strip()
    if pa:
        try:
            pa_date = datetime.strptime(pa, "%Y-%m-%d")
            if pa_date > datetime.now():
                errors.append("Published After date cannot be in the future.")
        except ValueError:
            errors.append("Published After must be a valid date (YYYY-MM-DD).")

    # ── max_video_age + video_age_unit ───────────────────────────
    age = payload.get("max_video_age", 0)
    unit = payload.get("video_age_unit", "days")
    if age < 0:
        errors.append("Video Age cannot be negative.")
    if age > 0 and unit not in VIDEO_AGE_UNITS:
        errors.append(f"Video Age Unit must be one of: {', '.join(sorted(VIDEO_AGE_UNITS))}.")
    if age > 0 and pa:
        errors.append("Provide either Published After or Video Age, not both.")

    # ── numeric min/max constraints ──────────────────────────────
    if payload["min_view_count"] < 0:
        errors.append("Minimum View Count cannot be negative.")
    if payload["min_like_count"] < 0:
        errors.append("Minimum Like Count cannot be negative.")
    if payload["min_video_length"] < 0:
        errors.append("Minimum Video Length cannot be negative.")
    if payload["max_video_length"] < 0:
        errors.append("Maximum Video Length cannot be negative.")
    if (
        payload["min_video_length"] > 0
        and payload["max_video_length"] > 0
        and payload["min_video_length"] > payload["max_video_length"]
    ):
        errors.append("Minimum Video Length cannot exceed Maximum Video Length.")
    if payload["min_comment_count"] < 0:
        errors.append("Minimum Comment Count cannot be negative.")

    # ── query source check (REMOVED) ─────────────────────────────
    # New ingestion logic automatically builds advanced queries
    # regardless of these flags. Validation removed.
    
    return errors


def _build_quota_context(payload):
    return build_quota_estimate(
        skills_count=len(list(dict.fromkeys(payload["selected_skills"]))),
        search_max_results=payload["search_max_results"],
        daily_limit=_daily_quota_limit(),
        warning_threshold=_quota_warning_threshold(),
    )


def _get_recent_runs(runs_collection, limit: int):
    safe_limit = max(1, min(limit, 50))
    cursor = (
        runs_collection.find({}, {"_id": 0})
        .sort("started_at", -1)
        .limit(safe_limit)
    )
    return [_serialize(doc) for doc in cursor]


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def index():
        try:
            sectors = fetch_sectors_and_skills()
            sectors_list = list(sectors.keys())
        except Exception:
            sectors_list = []
        return render_template("index.html", sectors_list=sectors_list)

    @app.route("/mongo_browser")
    def mongo_browser():
        return render_template("mongo_browser.html")

    @app.route("/quiz_gen")
    def quiz_gen():
        try:
            sectors = fetch_sectors_and_skills()
            sectors_list = list(sectors.keys())
        except Exception:
            sectors_list = []
        return render_template("quiz.html", sectors_list=sectors_list)

    @app.route("/delete")
    def delete_page():
        """Render the delete question page."""
        return render_template("delete.html")

    @app.route("/multi_label")
    def multi_label():
        """Render the multi-labelling page."""
        return render_template("multi_label.html")

    @app.route("/mapping_review")
    def mapping_review():
        """Render pending learner video mapping review page."""
        try:
            sectors = fetch_sectors_and_skills()
            sectors_list = list(sectors.keys())
        except Exception:
            sectors_list = []
        return render_template("mapping_review.html", sectors_list=sectors_list)

    @app.route("/api/admin/video-mapping-requests", methods=["GET"])
    def list_video_mapping_requests():
        status = request.args.get("status", "pending").strip().lower()
        if status not in {"pending", "approved", "rejected", "all"}:
            status = "pending"
        limit = max(1, min(to_int(request.args.get("limit", "100"), 100), 200))
        client = None
        try:
            client, videos_collection = _get_videos_collection()
            query = {"mapping_review.is_request": True}
            if status != "all":
                query["review_status"] = status
            cursor = videos_collection.find(query).sort("mapping_review.updated_at", -1)
            rows = []
            seen_video_ids = set()
            for doc in cursor:
                video_id = str(doc.get("videoId") or "").strip()
                if not video_id or video_id in seen_video_ids:
                    continue
                seen_video_ids.add(video_id)
                rows.append(_make_mapping_review_response(doc))
                if len(rows) >= limit:
                    break
            return jsonify({"ok": True, "count": len(rows), "requests": rows})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        finally:
            if client:
                client.close()

    @app.route("/api/admin/video-mapping-requests/<video_id>", methods=["GET"])
    def get_video_mapping_request(video_id):
        normalized_video_id = _normalize_video_id(video_id)
        client = None
        try:
            client, videos_collection = _get_videos_collection()
            doc = videos_collection.find_one(
                {
                    "videoId": normalized_video_id,
                    "mapping_review.is_request": True,
                },
                sort=[("mapping_review.updated_at", -1)],
            )
            if not doc:
                return jsonify({"ok": False, "error": "Video mapping review request not found."}), 404
            return jsonify({"ok": True, **_make_mapping_review_response(doc)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        finally:
            if client:
                client.close()

    @app.route("/api/admin/video-mapping-requests/<video_id>", methods=["PUT"])
    def update_video_mapping_request(video_id):
        normalized_video_id = _normalize_video_id(video_id)
        data = request.get_json(silent=True) or {}
        mappings, error = _validate_review_mappings(data.get("mappings") or [])
        if error:
            return jsonify({"ok": False, "error": error}), 400
        for mapping in mappings:
            mapping["source"] = "admin"

        client = None
        try:
            client, videos_collection = _get_videos_collection()
            existing = videos_collection.find_one(
                {
                    "videoId": normalized_video_id,
                    "mapping_review.is_request": True,
                }
            )
            if not existing:
                return jsonify({"ok": False, "error": "Video mapping review request not found."}), 404

            now = datetime.utcnow()
            videos_collection.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "review_status": "pending",
                        "suggested_mappings": mappings,
                        "mapping_review.status": "pending",
                        "mapping_review.suggested_mappings": mappings,
                        "mapping_review.suggestion_status": "manual",
                        "mapping_review.suggestion_error": "",
                        "mapping_review.updated_at": now,
                        "mapping_review.reviewer": str(data.get("reviewer") or "admin-console"),
                    }
                },
            )
            updated = videos_collection.find_one({"_id": existing["_id"]})
            return jsonify({"ok": True, **_make_mapping_review_response(updated or existing)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        finally:
            if client:
                client.close()

    @app.route("/api/admin/video-mapping-requests/<video_id>/retry-suggestion", methods=["POST"])
    def retry_video_mapping_suggestion(video_id):
        normalized_video_id = _normalize_video_id(video_id)
        data = request.get_json(silent=True) or {}
        reviewer = str(data.get("reviewer") or "admin-console")

        client = None
        try:
            import json
            import urllib.error
            import urllib.request

            client, videos_collection = _get_videos_collection()
            existing = videos_collection.find_one(
                {
                    "videoId": normalized_video_id,
                    "mapping_review.is_request": True,
                }
            )
            if not existing:
                return jsonify({"ok": False, "error": "Video mapping review request not found."}), 404
            if str(existing.get("review_status") or (existing.get("mapping_review") or {}).get("status") or "") != "pending":
                return jsonify({"ok": False, "error": "Only pending requests can retry AI suggestions."}), 400

            now = datetime.utcnow()
            videos_collection.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "suggested_mappings": [],
                        "mapping_review.suggested_mappings": [],
                        "mapping_review.suggestion_status": "running",
                        "mapping_review.suggestion_error": "",
                        "mapping_review.updated_at": now,
                        "mapping_review.reviewer": reviewer,
                    }
                },
            )

            review = existing.get("mapping_review") or {}
            search_query = str(review.get("search_query") or "")
            try:
                api_base = env("ACADEMY_API_BASE_URL", "http://host.docker.internal:9000").rstrip("/")
                api_payload = {
                    "search_query": search_query,
                    "use_ai": True,
                    "video": {
                        "video_id": str(existing.get("videoId") or ""),
                        "title": str(existing.get("title") or ""),
                        "description": str(existing.get("description") or ""),
                        "channel_title": str(existing.get("channelTitle") or ""),
                        "thumbnail_url": str(existing.get("thumbnailUrl") or ""),
                        "tags": [str(tag) for tag in (existing.get("tags") or [])],
                    },
                }
                encoded_payload = json.dumps(api_payload).encode("utf-8")
                retry_request = urllib.request.Request(
                    f"{api_base}/api/public/videos/suggest-mapping",
                    data=encoded_payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(retry_request, timeout=45) as response:
                    retry_payload = json.loads(response.read().decode("utf-8"))
                suggestion = retry_payload.get("suggestion") or {}
                mappings = [suggestion] if suggestion else []
                status = "ready" if mappings else "failed"
                error = "" if mappings else "No reliable suggestion"
            except urllib.error.HTTPError as exc:
                mappings = []
                status = "failed"
                try:
                    error_payload = json.loads(exc.read().decode("utf-8"))
                    error = str(error_payload.get("detail") or error_payload.get("error") or exc.reason)
                except Exception:
                    error = str(exc.reason or exc)
            except Exception as exc:
                mappings = []
                status = "failed"
                error = str(exc)

            videos_collection.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "suggested_mappings": mappings,
                        "mapping_review.suggested_mappings": mappings,
                        "mapping_review.suggestion_status": status,
                        "mapping_review.suggestion_error": error,
                        "mapping_review.updated_at": datetime.utcnow(),
                        "mapping_review.reviewer": reviewer,
                    }
                },
            )
            updated = videos_collection.find_one({"_id": existing["_id"]})
            return jsonify({"ok": True, **_make_mapping_review_response(updated or existing)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        finally:
            if client:
                client.close()

    @app.route("/api/admin/video-mapping-requests/<video_id>/approve", methods=["POST"])
    def approve_video_mapping_request(video_id):
        normalized_video_id = _normalize_video_id(video_id)
        data = request.get_json(silent=True) or {}
        mappings, error = _validate_review_mappings(data.get("mappings") or [])
        if error:
            return jsonify({"ok": False, "error": error}), 400
        for mapping in mappings:
            mapping["source"] = "admin"

        reviewer = str(data.get("reviewer") or "admin-console")
        client = None
        summary = {"errors": [], "embedding_status": "skipped", "embedding_indexed": 0}
        approved_docs = []
        try:
            client, videos_collection = _get_videos_collection()
            base_video = videos_collection.find_one(
                {
                    "videoId": normalized_video_id,
                    "mapping_review.is_request": True,
                }
            ) or videos_collection.find_one({"videoId": normalized_video_id})
            if not base_video:
                return jsonify({"ok": False, "error": "Video mapping review request not found."}), 404

            videos_collection.delete_many(
                {
                    "videoId": normalized_video_id,
                    "review_status": "approved",
                    "mapping_review.is_request": {"$ne": True},
                }
            )

            now = datetime.utcnow()
            for mapping in mappings:
                doc = _mapping_to_video_doc(base_video, mapping, reviewer, now)
                approved_docs.append(doc)
                videos_collection.update_one(
                    {
                        "videoId": normalized_video_id,
                        "skill_name": mapping["skill"],
                        "proficiency_level": mapping["proficiency_level"],
                        "competency": mapping["competency"],
                        "review_status": "approved",
                    },
                    {"$set": doc},
                    upsert=True,
                )

            videos_collection.update_many(
                {
                    "videoId": normalized_video_id,
                    "mapping_review.is_request": True,
                    "review_status": {"$ne": "approved"},
                },
                {
                    "$set": {
                        "review_status": "approved",
                        "approved_mappings": mappings,
                        "mapping_review.status": "approved",
                        "mapping_review.approved_mappings": mappings,
                        "mapping_review.reviewed_at": now,
                        "mapping_review.updated_at": now,
                        "mapping_review.reviewer": reviewer,
                    }
                },
            )

            _embed_touched_videos(summary, approved_docs)
            return jsonify(
                {
                    "ok": True,
                    "message": "Video mapping approved and indexed.",
                    "video_id": normalized_video_id,
                    "review_status": "approved",
                    "approved_mappings": mappings,
                    "embedding_status": summary.get("embedding_status", "skipped"),
                    "embedding_indexed": int(summary.get("embedding_indexed") or 0),
                    "errors": summary.get("errors", []),
                }
            )
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        finally:
            if client:
                client.close()

    @app.route("/api/admin/video-mapping-requests/<video_id>/reject", methods=["POST"])
    def reject_video_mapping_request(video_id):
        normalized_video_id = _normalize_video_id(video_id)
        data = request.get_json(silent=True) or {}
        client = None
        try:
            client, videos_collection = _get_videos_collection()
            existing = videos_collection.find_one(
                {
                    "videoId": normalized_video_id,
                    "mapping_review.is_request": True,
                }
            )
            if not existing:
                return jsonify({"ok": False, "error": "Video mapping review request not found."}), 404
            if str(existing.get("review_status") or (existing.get("mapping_review") or {}).get("status") or "") == "approved":
                return jsonify({
                    "ok": False,
                    "error": "Approved videos must be unpublished so approved docs and Qdrant points are removed.",
                }), 409

            now = datetime.utcnow()
            videos_collection.update_many(
                {
                    "videoId": normalized_video_id,
                    "mapping_review.is_request": True,
                },
                {
                    "$set": {
                        "review_status": "rejected",
                        "mapping_review.status": "rejected",
                        "mapping_review.reviewed_at": now,
                        "mapping_review.updated_at": now,
                        "mapping_review.reviewer": str(data.get("reviewer") or "admin-console"),
                        "mapping_review.rejection_reason": str(data.get("reason") or "Rejected in admin console."),
                        "mapping_review.suggestion_status": "cancelled",
                    }
                },
            )
            return jsonify(
                {
                    "ok": True,
                    "message": "Video mapping rejected.",
                    "video_id": normalized_video_id,
                    "review_status": "rejected",
                    "approved_mappings": [],
                    "embedding_status": "skipped",
                    "embedding_indexed": 0,
                }
            )
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        finally:
            if client:
                client.close()

    @app.route("/api/admin/video-mapping-requests/<video_id>/unpublish", methods=["POST"])
    def unpublish_video_mapping_request(video_id):
        normalized_video_id = _normalize_video_id(video_id)
        data = request.get_json(silent=True) or {}
        client = None
        try:
            client, videos_collection = _get_videos_collection()
            existing = videos_collection.find_one(
                {
                    "videoId": normalized_video_id,
                    "mapping_review.is_request": True,
                }
            )
            if not existing:
                return jsonify({"ok": False, "error": "Video mapping review request not found."}), 404

            cleanup_summary = _unpublish_approved_video(videos_collection, normalized_video_id)
            now = datetime.utcnow()
            videos_collection.update_many(
                {
                    "videoId": normalized_video_id,
                    "mapping_review.is_request": True,
                },
                {
                    "$set": {
                        "review_status": "rejected",
                        "mapping_review.status": "rejected",
                        "mapping_review.reviewed_at": now,
                        "mapping_review.updated_at": now,
                        "mapping_review.reviewer": str(data.get("reviewer") or "admin-console"),
                        "mapping_review.rejection_reason": str(data.get("reason") or "Unpublished by admin."),
                        "mapping_review.unpublished_at": now,
                    }
                },
            )
            return jsonify(
                {
                    "ok": True,
                    "message": "Video unpublished and review request rejected.",
                    "video_id": normalized_video_id,
                    "review_status": "rejected",
                    "approved_mappings": [],
                    "embedding_status": "skipped",
                    "embedding_indexed": 0,
                    "approved_deleted_count": int(cleanup_summary.get("approved_deleted_count") or 0),
                    "qdrant_delete_status": str(cleanup_summary.get("qdrant_delete_status") or "skipped"),
                }
            )
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        finally:
            if client:
                client.close()

    @app.route("/api/admin/video-mapping-requests/<video_id>/reopen", methods=["POST"])
    def reopen_video_mapping_request(video_id):
        normalized_video_id = _normalize_video_id(video_id)
        data = request.get_json(silent=True) or {}
        client = None
        try:
            client, videos_collection = _get_videos_collection()
            existing = videos_collection.find_one(
                {
                    "videoId": normalized_video_id,
                    "mapping_review.is_request": True,
                }
            )
            if not existing:
                return jsonify({"ok": False, "error": "Video mapping review request not found."}), 404
            if str(existing.get("review_status") or (existing.get("mapping_review") or {}).get("status") or "") == "approved":
                return jsonify({
                    "ok": False,
                    "error": "Approved videos are already live. Unpublish them before reopening.",
                }), 409

            now = datetime.utcnow()
            videos_collection.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "review_status": "pending",
                        "mapping_review.status": "pending",
                        "mapping_review.updated_at": now,
                        "mapping_review.reviewer": str(data.get("reviewer") or "admin-console"),
                        "mapping_review.rejection_reason": "",
                    },
                    "$unset": {
                        "mapping_review.reviewed_at": "",
                        "mapping_review.unpublished_at": "",
                    },
                },
            )
            updated = videos_collection.find_one({"_id": existing["_id"]})
            return jsonify({"ok": True, **_make_mapping_review_response(updated or existing)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        finally:
            if client:
                client.close()

    @app.route("/search_skills", methods=["POST"])
    def search_skills_route():
        data = request.get_json(silent=True) or {}
        sector = data.get("sector")
        search_term = data.get("search_term", "")
        skills = search_skills(sector, search_term)
        return jsonify(skills)

    @app.route("/search_competencies", methods=["POST"])
    def search_competencies_route():
        data = request.get_json(silent=True) or {}
        sector = data.get("sector")
        skill = data.get("skill")
        proficiency_level = data.get("proficiency_level")
        competencies = search_competencies(sector, skill, proficiency_level)
        return jsonify(competencies)

    @app.route("/search_proficiency_levels", methods=["POST"])
    def search_proficiency_levels_route():
        data = request.get_json(silent=True) or {}
        sector = data.get("sector")
        skill = data.get("skill")
        levels = search_proficiency_levels(sector, skill)
        return jsonify(levels)

    # ───────────────────────────────────────────────────────────────────────
    # Multi-Labelling Routes
    # ───────────────────────────────────────────────────────────────────────

    @app.route("/api/public/multi-label/search-videos", methods=["POST"])
    def multi_label_search_videos_route():
        """Search for videos to apply multi-labelling."""
        try:
            data = request.get_json(silent=True) or {}
            query = data.get("query", "").strip().lower()
            search_type = data.get("search_type", "title")
            limit = data.get("limit", 20)

            if not query:
                return jsonify({"count": 0, "results": []}), 400

            client, videos_collection = _get_videos_collection()
            try:
                # Build filter based on search type. Multi-label should operate on
                # live mapped docs, not retained admin-review request/audit docs.
                live_doc_filter = {
                    "deleted": {"$ne": True},
                    "mapping_review.is_request": {"$ne": True},
                    "skill_name": {"$nin": ["", None]},
                    "competency": {"$nin": ["", None]},
                }
                if search_type == "video_id":
                    watch_match = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{6,})", query)
                    video_query = watch_match.group(1) if watch_match else query
                    search_filter = {"videoId": {"$regex": re.escape(video_query), "$options": "i"}}
                elif search_type == "skill":
                    search_filter = {"skill_name": {"$regex": re.escape(query), "$options": "i"}}
                else:  # title
                    search_filter = _build_flexible_text_filter(TITLE_SEARCH_FIELDS, query)
                filter_query = {"$and": [live_doc_filter, search_filter]}

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
                            "review_status": 1,
                            "mapping_review": 1,
                            "additional_mappings": 1,
                            "mappings": 1,
                        },
                    )
                    .limit(limit)
                )

                results = []
                for doc in docs:
                    video_id = doc.get("videoId", "")
                    current_mappings = _current_mappings_from_doc(doc)

                    results.append({
                        "video_id": video_id,
                        "title": doc.get("title", ""),
                        "sector": doc.get("sector", ""),
                        "skill_name": doc.get("skill_name", ""),
                        "competency": doc.get("competency", ""),
                        "proficiency_level": doc.get("proficiency_level", ""),
                        "current_mappings": current_mappings,
                        "description": doc.get("description", ""),
                        "channel_title": doc.get("channelTitle", ""),
                    })

                return jsonify({"count": len(results), "results": results})
            finally:
                client.close()

        except Exception as e:
            return jsonify({"count": 0, "results": [], "error": str(e)}), 500

    @app.route("/api/public/multi-label/competencies", methods=["GET"])
    def multi_label_competencies_route():
        """Get available competencies for a proficiency level."""
        try:
            sector = (request.args.get("sector") or "").strip()
            skill = (request.args.get("skill") or "").strip()
            proficiency_level = (request.args.get("proficiency_level") or "").strip()

            if not all([sector, skill, proficiency_level]):
                return jsonify({"error": "sector, skill, and proficiency_level are required"}), 400

            knowledge_items, ability_items, proficiency_description = search_competencies_by_level(
                sector, skill, proficiency_level
            )

            return jsonify({
                "proficiency_level": proficiency_level,
                "proficiency_description": proficiency_description,
                "knowledge_items": knowledge_items,
                "ability_items": ability_items,
            })

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/public/multi-label/update-video", methods=["POST"])
    def multi_label_update_video_route():
        """Update a video with multiple competency mappings."""
        try:
            data = request.get_json(silent=True) or {}
            video_id = data.get("video_id", "").strip()
            skill_name = data.get("skill_name", "").strip()
            sector = data.get("sector", "").strip()
            competency = data.get("competency", "").strip()
            proficiency_level = data.get("proficiency_level", "").strip()
            mappings_to_add = data.get("mappings_to_add", [])
            mappings_to_remove = data.get("mappings_to_remove", [])

            if not video_id or not skill_name:
                return jsonify({"error": "video_id and skill_name are required"}), 400

            if not mappings_to_add and not mappings_to_remove:
                return jsonify({"error": "Either mappings_to_add or mappings_to_remove is required"}), 400

            client, videos_collection = _get_videos_collection()
            try:
                # Get current document
                target_query = {
                    "videoId": video_id,
                    "skill_name": skill_name,
                    "deleted": {"$ne": True},
                    "mapping_review.is_request": {"$ne": True},
                }
                if competency:
                    target_query["competency"] = competency
                if proficiency_level:
                    target_query["proficiency_level"] = proficiency_level

                doc = videos_collection.find_one(target_query)
                if not doc:
                    return jsonify({"error": "Video not found"}), 404

                # Get or initialize additional_mappings array.
                current_mappings = _current_mappings_from_doc(doc)

                # Remove mappings
                removed_count = 0
                if mappings_to_remove:
                    remove_keys = set()
                    remove_competencies = set()
                    for item in mappings_to_remove:
                        if isinstance(item, dict):
                            key = _mapping_key(item)
                            if all(key):
                                remove_keys.add(key)
                        else:
                            competency = str(item or "").strip()
                            if competency:
                                remove_competencies.add(competency)

                    kept_mappings = []
                    for mapping in current_mappings:
                        key = _mapping_key(mapping)
                        competency = key[1]
                        should_remove = key in remove_keys or (
                            competency in remove_competencies and not remove_keys
                        )
                        if should_remove:
                            removed_count += 1
                        else:
                            kept_mappings.append(mapping)
                    current_mappings = kept_mappings

                # Add new mappings
                existing_keys = {_mapping_key(m) for m in current_mappings}
                added_count = 0
                for mapping in mappings_to_add:
                    if not isinstance(mapping, dict):
                        continue
                    key = _mapping_key(mapping)
                    if all(key) and key not in existing_keys:
                        current_mappings.append({
                            "competency": key[1],
                            "item_type": mapping.get("item_type", ""),
                            "proficiency_level": key[0],
                            "proficiency_description": mapping.get("proficiency_description", ""),
                        })
                        existing_keys.add(key)
                        added_count += 1

                # Update document
                result = videos_collection.update_one(
                    target_query,
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
                    return jsonify({"error": "Failed to update video"}), 500

                updated_doc = dict(doc)
                updated_doc["sector"] = sector
                updated_doc["additional_mappings"] = current_mappings

                qdrant_synced = True
                qdrant_error = None
                try:
                    sync_result = sync_video_mapping_payload(updated_doc)
                    qdrant_synced = sync_result.get("qdrant_payload_status") != "skipped"
                except Exception as exc:
                    qdrant_synced = False
                    qdrant_error = str(exc)

                return jsonify({
                    "video_id": video_id,
                    "message": "Successfully updated video mappings.",
                    "total_mappings": len(current_mappings),
                    "added": added_count,
                    "removed": removed_count,
                    "qdrant_synced": qdrant_synced,
                    "qdrant_error": qdrant_error,
                })
            finally:
                client.close()

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    def search_competencies_by_level(sector: str, skill: str, proficiency_level: str):
        """Helper function to get competencies for a specific proficiency level."""
        try:
            knowledge_items = []
            ability_items = []
            proficiency_description = ""

            # Search using existing search_competencies function
            competencies = search_competencies(sector, skill, proficiency_level)
            
            for comp in competencies:
                item_type_text = comp.lower()
                if item_type_text.startswith("knowledge"):
                    knowledge_items.append(comp)
                elif item_type_text.startswith("ability"):
                    ability_items.append(comp)

            # Get proficiency description
            description_list = search_proficiency_levels(sector, skill)
            for desc in description_list:
                if desc.get("proficiency_level") == proficiency_level:
                    proficiency_description = desc.get("proficiency_description", "")
                    break

            return knowledge_items, ability_items, proficiency_description
        except Exception:
            return [], [], ""

    @app.route("/get_requirement", methods=["POST"])
    def get_requirement_route():
        data = request.get_json(silent=True) or {}
        sector = data.get("sector")
        skill = data.get("skill")
        proficiency_level = data.get("proficiency_level")
        competency = data.get("competency")
        requirements = get_requirement(sector, skill, proficiency_level, competency)
        return jsonify({"requirements": requirements})

    @app.route("/generate_quiz_stream", methods=["POST"])
    def generate_quiz_stream_route():
        """
        Streaming (NDJSON) version of /generate_quiz.
        Yields one JSON object per line as Groq generates each question.
        Event shapes:
          {"type":"context", "quiz_key":str, ...context fields}
          {"type":"question", "question_number":int, ...question fields}
          {"type":"error",   "message":str}
          {"type":"done",    "quiz_key":str}
        """
        from pipelines.quiz_gen.quiz_store import stream_quiz  # lazy import

        data = request.get_json(silent=True) or {}
        sector               = (data.get("sector")               or "").strip()
        skill                = (data.get("skill")                or "").strip()
        competency           = (data.get("competency")           or "").strip()
        proficiency_level    = (data.get("proficiency_level")    or "").strip()
        proficiency_description = (data.get("proficiency_description") or "").strip()
        question_types       = data.get("question_types", ["Conceptual", "Application", "Scenario-Based", "Technical", "Evaluation"])
        try:
            num_questions = int(data.get("num_questions", 5))
        except (TypeError, ValueError):
            num_questions = 5

        if not all([sector, skill, competency, proficiency_level]):
            def _err():
                yield json.dumps({"type": "error", "message": "sector, skill, competency and proficiency_level are required."}) + "\n"
            return Response(stream_with_context(_err()), mimetype="application/x-ndjson"), 400

        def _generate():
            for event in stream_quiz(
                sector=sector,
                skill=skill,
                competency=competency,
                proficiency_level=proficiency_level,
                proficiency_description=proficiency_description,
                quiz_mode="competency",  # Admin always uses competency mode for direct generation
                question_types=question_types,
                num_questions=num_questions,
            ):
                yield json.dumps(event, ensure_ascii=False) + "\n"

        return Response(
            stream_with_context(_generate()),
            mimetype="application/x-ndjson",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    @app.route("/store_quiz", methods=["POST"])
    def store_quiz_route():
        """
        Store a generated quiz in MongoDB after user confirmation.

        Expects JSON payload:
        {
          "sector": str,
          "skill": str,
          "competency": str,
          "proficiency_level": str,
          "proficiency_description": str,
          "item_type": "knowledge" | "ability",
          "questions": [
            {
              "question_number": int,
              "question": str,
              "question_type": str,
              "options": {"A": str, "B": str, "C": str, "D": str},
              "correct": str,
              "explanation": str
            },
            ...
          ]
        }

        Returns:
        {
          "success": bool,
          "mongo_ids": list[str] or None,
          "question_count": int,
          "message": str
        }
        """
        from pipelines.quiz_gen.quiz_store import store_quiz_submission  # lazy import

        data = request.get_json(silent=True) or {}

        # Validate required fields
        required_fields = ["sector", "skill", "competency", "proficiency_level", "item_type", "questions"]
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            return jsonify({
                "success": False,
                "mongo_ids": None,
                "message": f"Missing required fields: {', '.join(missing)}"
            }), 400

        try:
            result = store_quiz_submission(
                sector=data.get("sector", "").strip(),
                skill=data.get("skill", "").strip(),
                competency=data.get("competency", "").strip(),
                proficiency_level=data.get("proficiency_level", "").strip(),
                proficiency_description=data.get("proficiency_description", "").strip(),
                item_type=data.get("item_type", "").strip(),
                questions=data.get("questions", []),
            )
            status_code = 200 if result.get("success") else 400
            return jsonify(result), status_code
        except Exception as exc:
            return jsonify({
                "success": False,
                "mongo_ids": None,
                "message": f"Error storing quiz: {str(exc)}"
            }), 500

    @app.route("/delete_question", methods=["POST"])
    def delete_question_route():
        """
        Delete a quiz question from MongoDB (soft delete).

        Expects JSON payload:
        {
          "question_id": str  (MongoDB _id)
        }

        Returns:
        {
          "success": bool,
          "message": str
        }
        """
        from pipelines.quiz_gen.quiz_mongo import delete_question  # lazy import

        data = request.get_json(silent=True) or {}
        question_id = data.get("question_id", "").strip()

        print(f"\n{'='*60}")
        print(f"DELETE_QUESTION_ROUTE called")
        print(f"Question ID: {question_id}")
        print(f"{'='*60}")

        if not question_id:
            print(f"❌ No question_id provided")
            return jsonify({
                "success": False,
                "message": "question_id is required."
            }), 400

        try:
            print(f"🔄 Calling delete_question({question_id})...")
            result = delete_question(question_id)
            print(f"📤 delete_question() returned: {result}")
            
            if result.get("success"):
                print(f"✅ Delete successful, returning 200")
                return jsonify({
                    "success": True,
                    "message": result.get("message", "Question deleted successfully.")
                }), 200
            else:
                print(f"❌ Delete returned False, returning 400")
                return jsonify({
                    "success": False,
                    "message": result.get("message", "Failed to delete question.")
                }), 400
        except Exception as exc:
            print(f"❌ Exception: {exc}")
            import traceback
            print(traceback.format_exc())
            return jsonify({
                "success": False,
                "message": f"Error deleting question: {str(exc)}"
            }), 500

    @app.route("/delete_video", methods=["POST"])
    def delete_video_route():
        """
        Delete a video from MongoDB (soft delete).

        Expects JSON payload:
        {
          "video_id": str  (MongoDB _id)
        }

        Returns:
        {
          "success": bool,
          "message": str
        }
        """
        from pipelines.quiz_gen.quiz_mongo import delete_video  # lazy import

        data = request.get_json(silent=True) or {}
        video_id = data.get("video_id", "").strip()

        print(f"\n{'='*60}")
        print(f"DELETE_VIDEO_ROUTE called")
        print(f"Video ID: {video_id}")
        print(f"{'='*60}")

        if not video_id:
            print(f"❌ No video_id provided")
            return jsonify({
                "success": False,
                "message": "video_id is required."
            }), 400

        try:
            print(f"🔄 Calling delete_video({video_id})...")
            result = delete_video(video_id)
            print(f"📤 delete_video() returned: {result}")
            
            if result.get("success"):
                print(f"✅ Delete successful, returning 200")
                return jsonify({
                    "success": True,
                    "message": result.get("message", "Video deleted successfully.")
                }), 200
            else:
                print(f"❌ Delete returned False, returning 400")
                return jsonify({
                    "success": False,
                    "message": result.get("message", "Failed to delete video.")
                }), 400
        except Exception as exc:
            print(f"❌ Exception: {exc}")
            import traceback
            print(traceback.format_exc())
            return jsonify({
                "success": False,
                "message": f"Error deleting video: {str(exc)}"
            }), 500

    @app.route("/quota_estimate", methods=["POST"])
    def quota_estimate():
        payload = _parse_fetch_payload(request)
        _resolve_video_age_to_published_after(payload)
        quota = _build_quota_context(payload)
        return jsonify({"ok": True, "quota": quota})

    @app.route("/mongo_status", methods=["GET"])
    def mongo_status():
        client = None
        try:
            client = get_mongo_client()
            db = client[env("MONGO_DATABASE", "")]
            videos_collection = db["videos"]
            runs_collection = db["ingestion_runs"]
            status = mongo_status_snapshot(videos_collection)
            recent_runs = _get_recent_runs(runs_collection, limit=8)
            return jsonify({"ok": True, "status": status, "recent_runs": recent_runs})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        finally:
            if client:
                client.close()

    @app.route("/mongo_collections", methods=["GET"])
    def mongo_collections():
        client = None
        try:
            client = get_mongo_client()
            db = client[env("MONGO_DATABASE", "")]
            collections = sorted(db.list_collection_names())
            return jsonify({"ok": True, "collections": collections})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        finally:
            if client:
                client.close()

    @app.route("/mongo_documents", methods=["GET"])
    def mongo_documents():
        client = None
        try:
            collection_name = request.args.get("collection", "videos").strip()
            limit = to_int(request.args.get("limit", 20), 20)
            limit = max(1, min(limit, 100))
            skip = to_int(request.args.get("skip", 0), 0)
            skip = max(0, skip)
            
            # Set default sort field and allowed sorts based on collection
            if collection_name == "ingestion_runs":
                default_sort = "started_at"
                allowed_sorts = {"started_at", "status", "run_id"}
            elif collection_name == "Quiz_Generation":
                default_sort = "ingested_at"
                allowed_sorts = {"ingested_at", "sector", "skill", "competency", "proficiency_level"}
            else:
                default_sort = "ingested_timing"
                allowed_sorts = {"ingested_timing", "publishedAt", "viewCount", "likeCount", "title"}
            
            sort_field = request.args.get("sort", default_sort).strip()
            if sort_field not in allowed_sorts:
                sort_field = default_sort
            sort_dir = -1 if request.args.get("order", "desc").lower() == "desc" else 1
            filter_field = request.args.get("filter_field", "").strip()
            filter_value = request.args.get("filter_value", "").strip()

            client = get_mongo_client()
            db = client[env("MONGO_DATABASE", "")]
            if collection_name not in db.list_collection_names():
                return jsonify({"ok": False, "error": "Collection not found."}), 404

            collection = db[collection_name]
            query = _build_mongo_filter(filter_field, filter_value, collection_name)
            
            # For Quiz_Generation, exclude deleted questions
            if collection_name == "Quiz_Generation":
                query["deleted"] = {"$ne": True}
            # For videos collection, exclude deleted videos
            elif collection_name == "videos":
                query["deleted"] = {"$ne": True}
            
            projection = {"mappings": 0} if collection_name == "videos" else None

            cursor = (
                collection.find(query, projection)
                .sort(sort_field, sort_dir)
                .skip(skip)
                .limit(limit + 1)
            )
            docs = list(cursor)
            has_more = len(docs) > limit
            docs = docs[:limit]
            docs = [_serialize(doc) for doc in docs]
            return jsonify(
                {
                    "ok": True,
                    "documents": docs,
                    "skip": skip,
                    "limit": limit,
                    "has_more": has_more,
                }
            )
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        finally:
            if client:
                client.close()

    @app.route("/ingestion_runs", methods=["GET"])
    def ingestion_runs():
        client = None
        try:
            limit = to_int(request.args.get("limit", 20), 20)
            client = get_mongo_client()
            db = client[env("MONGO_DATABASE", "")]
            runs_collection = db["ingestion_runs"]
            runs = _get_recent_runs(runs_collection, limit=limit)
            return jsonify({"ok": True, "runs": runs})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        finally:
            if client:
                client.close()

    @app.route("/preview", methods=["POST"])
    def preview():
        from youtube_ingestion_service import fetch_videos_for_preview
        
        payload = _parse_fetch_payload(request)
        validation_errors = _validate_fetch_payload(payload)
        if validation_errors:
            return jsonify({"ok": False, "error": " ".join(validation_errors), "errors": validation_errors}), 400
        _resolve_video_age_to_published_after(payload)

        try:
            videos, summary = fetch_videos_for_preview(
                sector=payload["sector"],
                api_key=payload["api_key"],
                search_max_results=payload["search_max_results"],
                search_order=payload["search_order"],
                selected_skills=payload["selected_skills"],
                competency=payload["competency"],
                proficiency=payload["proficiency"],
                requirement=payload["requirement"],
                min_view_count=payload["min_view_count"],
                min_like_count=payload["min_like_count"],
                min_video_length=payload["min_video_length"],
                max_video_length=payload["max_video_length"],
                min_comment_count=payload["min_comment_count"],
                max_video_age=payload["max_video_age"],
                additional_query=payload["additional_query"],
                query_includes={
                    "sector": payload["include_sector"],
                    "skill": payload["include_skill"],
                    "competency": payload["include_competency"],
                    "requirement": payload["include_requirement"],
                },
                search_constraints={
                    "published_after": payload["published_after"],
                },
            )
            
            quota_context = _build_quota_context(payload)
            
            return jsonify({
                "ok": True,
                "videos": videos,
                "summary": summary,
                "quota": quota_context,
            })
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/upsert_selected", methods=["POST"])
    def upsert_selected():
        from youtube_ingestion_service import upsert_selected_videos
        
        data = request.get_json(silent=True) or {}
        videos_to_upsert = data.get("videos", [])
        payload = data.get("payload", {})
        
        if not videos_to_upsert:
            return jsonify({"ok": False, "error": "No videos selected for upsert"}), 400

        client = None
        run_doc_id = None
        summary = None
        run_id = str(uuid4())
        touched_docs: list[dict] = []

        try:
            client = get_mongo_client()
            db = client[env("MONGO_DATABASE", "")] 
            videos_collection = db["videos"]
            runs_collection = db["ingestion_runs"]

            run_doc = {
                "run_id": run_id,
                "started_at": datetime.now(),
                "ended_at": None,
                "status": "running",
                "message": "Upserting selected videos",
                "params": {
                    "sector": payload.get("sector", ""),
                    "selected_skills": payload.get("skills", []),
                    "competency": payload.get("competency", ""),
                    "proficiency": payload.get("proficiency", ""),
                    "requirement": payload.get("requirement", ""),
                    "videos_to_upsert": len(videos_to_upsert),
                },
                "summary": None,
            }
            run_doc_id = runs_collection.insert_one(run_doc).inserted_id

            summary = upsert_selected_videos(
                videos_collection,
                videos_to_upsert,
                touched_docs=touched_docs,
            )
            _embed_touched_videos(summary, touched_docs)
            summary["mongo_status"] = mongo_status_snapshot(videos_collection)

            run_status = "completed"
            run_message = "Selected videos upserted successfully."
            if summary.get("embedding_status") == "completed" and summary.get("embedding_indexed", 0) > 0:
                run_message = "Selected videos upserted and indexed successfully."
            elif summary.get("embedding_status") == "skipped":
                run_message = "Selected videos upserted. No valid videos required indexing."

            if summary.get("embedding_status") == "failed":
                run_status = "completed_with_errors"
                run_message = "Selected videos upserted, but embedding failed."
            elif summary.get("error_count", 0) > 0:
                run_status = "completed_with_errors"
                run_message = "Selected videos upserted with errors."

            runs_collection.update_one(
                {"_id": run_doc_id},
                {
                    "$set": {
                        "ended_at": datetime.now(),
                        "status": run_status,
                        "message": run_message,
                        "summary": summary,
                    }
                },
            )

            return jsonify({
                "ok": True,
                "run_id": run_id,
                "message": run_message,
                "summary": summary,
            })
        except Exception as e:
            if summary is None:
                summary = {"error_count": 1, "errors": [str(e)]}
            if client and run_doc_id is not None:
                db = client[env("MONGO_DATABASE", "")] 
                runs_collection = db["ingestion_runs"]
                runs_collection.update_one(
                    {"_id": run_doc_id},
                    {
                        "$set": {
                            "ended_at": datetime.now(),
                            "status": "failed",
                            "message": str(e),
                            "summary": summary,
                        }
                    },
                )
            return jsonify({"ok": False, "error": str(e), "summary": summary}), 500
        finally:
            if client:
                client.close()

    @app.route("/fetch", methods=["POST"])
    def fetch():
        payload = _parse_fetch_payload(request)
        validation_errors = _validate_fetch_payload(payload)
        if validation_errors:
            return jsonify({"ok": False, "error": " ".join(validation_errors), "errors": validation_errors}), 400
        _resolve_video_age_to_published_after(payload)

        client = None
        run_doc_id = None
        summary = None
        quota_context = _build_quota_context(payload)
        run_id = str(uuid4())
        touched_docs: list[dict] = []

        try:
            client = get_mongo_client()
            db = client[env("MONGO_DATABASE", "")]
            videos_collection = db["videos"]
            runs_collection = db["ingestion_runs"]

            run_doc = {
                "run_id": run_id,
                "started_at": datetime.now(),
                "ended_at": None,
                "status": "running",
                "message": "Ingestion in progress",
                "params": {
                    "sector": payload["sector"],
                    "selected_skills": payload["selected_skills"],
                    "competency": payload["competency"],
                    "proficiency": payload["proficiency"],
                    "requirement": payload["requirement"],
                    "skills_count": len(list(dict.fromkeys(payload["selected_skills"]))),
                    "search_max_results": payload["search_max_results"],
                    "search_order": payload["search_order"],
                    "published_after": payload["published_after"],
                    "max_video_age": payload["max_video_age"],
                    "video_age_unit": payload["video_age_unit"],
                    "min_view_count": payload["min_view_count"],
                    "min_like_count": payload["min_like_count"],
                    "min_video_length": payload["min_video_length"],
                    "max_video_length": payload["max_video_length"],
                    "min_comment_count": payload["min_comment_count"],
                    "api_key_supplied": bool(payload["api_key"]),
                },
                "quota_estimate": quota_context,
                "summary": None,
            }
            run_doc_id = runs_collection.insert_one(run_doc).inserted_id

            summary = run_ingestion(
                videos_collection,
                sector=payload["sector"],
                api_key=payload["api_key"],
                search_max_results=payload["search_max_results"],
                search_order=payload["search_order"],
                selected_skills=payload["selected_skills"],
                competency=payload["competency"],
                proficiency=payload["proficiency"],
                requirement=payload["requirement"],
                min_view_count=payload["min_view_count"],
                min_like_count=payload["min_like_count"],
                min_video_length=payload["min_video_length"],
                max_video_length=payload["max_video_length"],
                min_comment_count=payload["min_comment_count"],
                max_video_age=payload["max_video_age"],
                additional_query=payload["additional_query"],
                query_includes={
                    "sector": payload["include_sector"],
                    "skill": payload["include_skill"],
                    "competency": payload["include_competency"],
                    "requirement": payload["include_requirement"],
                },
                search_constraints={
                    "published_after": payload["published_after"],
                },
                touched_docs=touched_docs,
            )
            _embed_touched_videos(summary, touched_docs)
            summary["mongo_status"] = mongo_status_snapshot(videos_collection)

            run_status = "completed"
            run_message = "Fetch and upsert completed."
            if summary.get("embedding_status") == "completed" and summary.get("embedding_indexed", 0) > 0:
                run_message = "Fetch, upsert, and embedding completed."
            elif summary.get("embedding_status") == "skipped":
                run_message = "Fetch and upsert completed. No valid videos required indexing."

            if summary.get("quota_exceeded"):
                run_status = "stopped_quota"
                run_message = "Stopped due to YouTube quota limits."
                if summary.get("embedding_status") == "completed" and summary.get("embedding_indexed", 0) > 0:
                    run_message = "Stopped due to YouTube quota limits after indexing fetched videos."
                elif summary.get("embedding_status") == "failed":
                    run_message = "Stopped due to YouTube quota limits, and embedding failed for fetched videos."
            elif summary.get("embedding_status") == "failed":
                run_status = "completed_with_errors"
                run_message = "Fetch and upsert completed, but embedding failed."
            elif summary.get("error_count", 0) > 0:
                run_status = "completed_with_errors"
                run_message = "Fetch and upsert completed with errors."

            runs_collection.update_one(
                {"_id": run_doc_id},
                {
                    "$set": {
                        "ended_at": datetime.now(),
                        "status": run_status,
                        "message": run_message,
                        "summary": summary,
                    }
                },
            )

            return jsonify(
                {
                    "ok": True,
                    "run_id": run_id,
                    "message": run_message,
                    "summary": summary,
                    "quota": quota_context,
                }
            )
        except Exception as e:
            if summary is None:
                summary = {"error_count": 1, "errors": [str(e)]}
            if client and run_doc_id is not None:
                db = client[env("MONGO_DATABASE", "")]
                runs_collection = db["ingestion_runs"]
                runs_collection.update_one(
                    {"_id": run_doc_id},
                    {
                        "$set": {
                            "ended_at": datetime.now(),
                            "status": "failed",
                            "message": str(e),
                            "summary": summary,
                        }
                    },
                )
            return jsonify({"ok": False, "error": str(e), "summary": summary}), 500
        finally:
            if client:
                client.close()

    return app
