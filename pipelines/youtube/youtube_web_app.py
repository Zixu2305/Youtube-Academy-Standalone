import json
import re
from datetime import datetime
from uuid import uuid4

from flask import Flask, jsonify, render_template, request
from bson import ObjectId

from youtube_config import (
    DEFAULT_DAILY_QUOTA_LIMIT,
    DEFAULT_QUOTA_WARNING_THRESHOLD,
    env,
    to_int,
)
from youtube_data_access import (
    fetch_sectors_and_skills,
    get_mongo_client,
    mongo_status_snapshot,
    search_skills,
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


def _parse_bool(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _build_mongo_filter(field: str, value: str) -> dict:
    allowed_fields = {"sector", "skill_name", "videoId", "title"}
    if not field or not value or field not in allowed_fields:
        return {}
    return {field: {"$regex": re.escape(value), "$options": "i"}}


def _parse_fetch_payload(req):
    if req.is_json:
        data = req.get_json(silent=True) or {}
        payload = {
            "sector": data.get("sector", ""),
            "api_key": data.get("api_key", ""),
            "search_max_results": to_int(data.get("search_max_results", 10), 10),
            "search_order": data.get("search_order", "relevance"),
            "comments_max_results": to_int(data.get("comments_max_results", 10), 10),
            "selected_skills": data.get("skills", []),
            "published_after": data.get("published_after", ""),
            "published_before": data.get("published_before", ""),
            "region_code": data.get("region_code", ""),
            "relevance_language": data.get("relevance_language", ""),
            "video_duration": data.get("video_duration", "any"),
            "min_view_count": to_int(data.get("min_view_count", 0), 0),
            "min_like_count": to_int(data.get("min_like_count", 0), 0),
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
        "search_max_results": to_int(req.form.get("search_max_results", 10), 10),
        "search_order": req.form.get("search_order", "relevance"),
        "comments_max_results": to_int(req.form.get("comments_max_results", 10), 10),
        "selected_skills": selected_skills,
        "published_after": req.form.get("published_after", ""),
        "published_before": req.form.get("published_before", ""),
        "region_code": req.form.get("region_code", ""),
        "relevance_language": req.form.get("relevance_language", ""),
        "video_duration": req.form.get("video_duration", "any"),
        "min_view_count": to_int(req.form.get("min_view_count", 0), 0),
        "min_like_count": to_int(req.form.get("min_like_count", 0), 0),
    }
    return payload


def _validate_fetch_payload(payload):
    if not payload["sector"]:
        return "Sector is required."
    if not payload["api_key"]:
        return "YouTube API key is required."
    if not payload["selected_skills"]:
        return "Select at least one skill."
    if payload["search_max_results"] <= 0:
        return "Search Max Results must be greater than 0."
    if payload["comments_max_results"] < 0:
        return "Comments Max Results cannot be negative."
    if payload["min_view_count"] < 0:
        return "Minimum view count cannot be negative."
    if payload["min_like_count"] < 0:
        return "Minimum like count cannot be negative."
    return None


def _build_quota_context(payload):
    return build_quota_estimate(
        skills_count=len(list(dict.fromkeys(payload["selected_skills"]))),
        search_max_results=payload["search_max_results"],
        comments_max_results=payload["comments_max_results"],
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

    @app.route("/quota_estimate", methods=["POST"])
    def quota_estimate():
        payload = _parse_fetch_payload(request)
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
            include_comments = _parse_bool(request.args.get("include_comments", "false"))

            client = get_mongo_client()
            db = client[env("MONGO_DATABASE", "")]
            if collection_name not in db.list_collection_names():
                return jsonify({"ok": False, "error": "Collection not found."}), 404

            collection = db[collection_name]
            query = _build_mongo_filter(filter_field, filter_value)
            projection = None if include_comments else {"comments": 0}

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

    @app.route("/fetch", methods=["POST"])
    def fetch():
        payload = _parse_fetch_payload(request)
        validation_error = _validate_fetch_payload(payload)
        if validation_error:
            return jsonify({"ok": False, "error": validation_error}), 400

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
                    "skills_count": len(list(dict.fromkeys(payload["selected_skills"]))),
                    "search_max_results": payload["search_max_results"],
                    "search_order": payload["search_order"],
                    "comments_max_results": payload["comments_max_results"],
                    "published_after": payload["published_after"],
                    "published_before": payload["published_before"],
                    "region_code": payload["region_code"],
                    "relevance_language": payload["relevance_language"],
                    "video_duration": payload["video_duration"],
                    "min_view_count": payload["min_view_count"],
                    "min_like_count": payload["min_like_count"],
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
                comments_max_results=payload["comments_max_results"],
                selected_skills=payload["selected_skills"],
                min_view_count=payload["min_view_count"],
                min_like_count=payload["min_like_count"],
                search_constraints={
                    "published_after": payload["published_after"],
                    "published_before": payload["published_before"],
                    "region_code": payload["region_code"],
                    "relevance_language": payload["relevance_language"],
                    "video_duration": payload["video_duration"],
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
