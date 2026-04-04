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
from youtube_vector_index import DEFAULT_COLLECTION_NAME, embed_and_upsert_videos


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

    allowed_fields_by_collection = {
        "videos": {
            "sector",
            "skill_name",
            "videoId",
            "title",
            "competency",
            "proficiency_level",
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

    return {filter_field: {"$regex": re.escape(filter_value), "$options": "i"}}


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
