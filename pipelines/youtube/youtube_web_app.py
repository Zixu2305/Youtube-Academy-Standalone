import json
import re
from datetime import datetime, timedelta
from uuid import uuid4

from flask import Flask, jsonify, render_template, request
from bson import ObjectId

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


def _build_mongo_filter(field: str, value: str) -> dict:
    allowed_fields = {"sector", "skill_name", "videoId", "title"}
    if not field or not value or field not in allowed_fields:
        return {}
    return {field: {"$regex": re.escape(value), "$options": "i"}}


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
        competencies = search_competencies(sector, skill)
        return jsonify(competencies)

    @app.route("/search_proficiency_levels", methods=["POST"])
    def search_proficiency_levels_route():
        data = request.get_json(silent=True) or {}
        sector = data.get("sector")
        skill = data.get("skill")
        competency = data.get("competency")
        levels = search_proficiency_levels(sector, skill, competency)
        return jsonify(levels)

    @app.route("/get_requirement", methods=["POST"])
    def get_requirement_route():
        data = request.get_json(silent=True) or {}
        sector = data.get("sector")
        skill = data.get("skill")
        competency = data.get("competency")
        proficiency = data.get("proficiency")
        requirements = get_requirement(sector, skill, competency, proficiency)
        return jsonify({"requirements": requirements})

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
            sort_field = request.args.get("sort", "ingested_timing").strip()
            allowed_sorts = {
                "ingested_timing",
                "publishedAt",
                "viewCount",
                "likeCount",
                "title",
            }
            if sort_field not in allowed_sorts:
                sort_field = "ingested_timing"
            sort_dir = -1 if request.args.get("order", "desc").lower() == "desc" else 1
            filter_field = request.args.get("filter_field", "").strip()
            filter_value = request.args.get("filter_value", "").strip()

            client = get_mongo_client()
            db = client[env("MONGO_DATABASE", "")]
            if collection_name not in db.list_collection_names():
                return jsonify({"ok": False, "error": "Collection not found."}), 404

            collection = db[collection_name]
            query = _build_mongo_filter(filter_field, filter_value)
            projection = None

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

            summary = upsert_selected_videos(videos_collection, videos_to_upsert)
            summary["mongo_status"] = mongo_status_snapshot(videos_collection)

            run_status = "completed"
            run_message = "Selected videos upserted successfully."
            if summary.get("error_count", 0) > 0:
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
            )
            summary["mongo_status"] = mongo_status_snapshot(videos_collection)

            run_status = "completed"
            run_message = "Fetch and upsert completed."
            if summary.get("quota_exceeded"):
                run_status = "stopped_quota"
                run_message = "Stopped due to YouTube quota limits."
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
