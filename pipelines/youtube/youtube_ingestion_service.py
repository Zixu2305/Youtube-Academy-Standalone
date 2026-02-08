from datetime import datetime
import re

import requests

from youtube_config import MAX_ERROR_DETAILS, REQUEST_TIMEOUT_SECONDS, to_int


QUOTA_ERROR_REASONS = {
    "quotaExceeded",
    "dailyLimitExceeded",
    "dailyLimitExceeded402",
    "dailyLimitExceededUnreg",
    "rateLimitExceeded",
}


def parse_duration(duration_str):
    if not duration_str:
        return "0:00"
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str)
    if not match:
        return "0:00"
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def request_json(url: str, params: dict):
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return response, payload


def extract_comments(items):
    comments = []
    for comment in items:
        snippet = (
            comment.get("snippet", {})
            .get("topLevelComment", {})
            .get("snippet", {})
        )
        comments.append(
            {
                "textDisplay": snippet.get("textDisplay", ""),
                "textOriginal": snippet.get("textOriginal", ""),
            }
        )
    return comments


def estimate_quota_units(skills_count: int, search_max_results: int, comments_enabled: bool):
    skills = max(0, skills_count)
    results = max(0, search_max_results)
    per_video_cost = 1 + (1 if comments_enabled else 0)
    return skills * (100 + (results * per_video_cost))


def build_quota_estimate(
    *,
    skills_count: int,
    search_max_results: int,
    comments_max_results: int,
    daily_limit: int,
    warning_threshold: int,
):
    comments_enabled = comments_max_results > 0
    estimate_units = estimate_quota_units(skills_count, search_max_results, comments_enabled)

    level = "ok"
    if estimate_units >= daily_limit:
        level = "over_limit"
    elif estimate_units >= warning_threshold:
        level = "warning"

    return {
        "estimated_units": estimate_units,
        "daily_limit": daily_limit,
        "warning_threshold": warning_threshold,
        "comments_enabled": comments_enabled,
        "level": level,
        "remaining_after_run": daily_limit - estimate_units,
    }


def normalize_rfc3339_date(value: str, end_of_day: bool):
    if not value:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return f"{value}T23:59:59Z" if end_of_day else f"{value}T00:00:00Z"
    return value


def _extract_error_reason(payload: dict):
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error", {})
    errors = error.get("errors", [])
    if isinstance(errors, list) and errors:
        reason = errors[0].get("reason", "")
        if reason:
            return reason
    reason = error.get("reason", "")
    if reason:
        return reason
    status = error.get("status", "")
    if status:
        return status
    return ""


def _is_quota_error(payload: dict):
    reason = _extract_error_reason(payload)
    return reason in QUOTA_ERROR_REASONS


def _status_with_reason(status_code: int, payload: dict):
    reason = _extract_error_reason(payload)
    if reason:
        return f"HTTP {status_code} ({reason})"
    return f"HTTP {status_code}"


def _apply_search_constraints(search_params: dict, search_constraints: dict):
    published_after = normalize_rfc3339_date(
        search_constraints.get("published_after", ""), end_of_day=False
    )
    published_before = normalize_rfc3339_date(
        search_constraints.get("published_before", ""), end_of_day=True
    )
    region_code = search_constraints.get("region_code", "").strip()
    relevance_language = search_constraints.get("relevance_language", "").strip()
    video_duration = search_constraints.get("video_duration", "").strip()

    if published_after:
        search_params["publishedAfter"] = published_after
    if published_before:
        search_params["publishedBefore"] = published_before
    if region_code:
        search_params["regionCode"] = region_code
    if relevance_language:
        search_params["relevanceLanguage"] = relevance_language
    if video_duration and video_duration != "any":
        search_params["videoDuration"] = video_duration


def run_ingestion(
    collection,
    *,
    sector: str,
    api_key: str,
    search_max_results: int,
    search_order: str,
    comments_max_results: int,
    selected_skills: list[str],
    min_view_count: int = 0,
    min_like_count: int = 0,
    search_constraints: dict | None = None,
):
    search_constraints = search_constraints or {}
    unique_skills = list(dict.fromkeys(selected_skills))

    summary = {
        "sector": sector,
        "skills_requested": len(unique_skills),
        "skills_processed": 0,
        "videos_found": 0,
        "videos_processed": 0,
        "videos_filtered_constraints": 0,
        "upserts_attempted": 0,
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "error_count": 0,
        "errors": [],
        "quota_exceeded": False,
        "constraints": {
            "min_view_count": max(0, min_view_count),
            "min_like_count": max(0, min_like_count),
            "published_after": search_constraints.get("published_after", ""),
            "published_before": search_constraints.get("published_before", ""),
            "region_code": search_constraints.get("region_code", ""),
            "relevance_language": search_constraints.get("relevance_language", ""),
            "video_duration": search_constraints.get("video_duration", "any"),
        },
    }

    stop_due_to_quota = False

    for skill in unique_skills:
        if stop_due_to_quota:
            break

        summary["skills_processed"] += 1

        search_url = "https://www.googleapis.com/youtube/v3/search"
        search_params = {
            "part": "snippet",
            "maxResults": search_max_results,
            "order": search_order,
            "key": api_key,
            "type": "video",
            "q": f"{sector} {skill}",
        }
        _apply_search_constraints(search_params, search_constraints)

        try:
            search_response, search_data = request_json(search_url, search_params)
        except requests.RequestException as e:
            summary["errors"].append(f"[{skill}] search request failed: {e}")
            continue

        if search_response.status_code != 200:
            summary["errors"].append(
                f"[{skill}] search request returned {_status_with_reason(search_response.status_code, search_data)}"
            )
            if _is_quota_error(search_data):
                summary["quota_exceeded"] = True
                stop_due_to_quota = True
            continue

        if "error" in search_data:
            summary["errors"].append(f"[{skill}] YouTube error: {search_data['error']}")
            if _is_quota_error(search_data):
                summary["quota_exceeded"] = True
                stop_due_to_quota = True
            continue

        items = search_data.get("items", [])
        summary["videos_found"] += len(items)

        for item in items:
            if stop_due_to_quota:
                break

            video_id = item.get("id", {}).get("videoId")
            if not video_id:
                continue

            summary["videos_processed"] += 1

            statistics = {}
            tags = []
            duration = ""

            videos_url = "https://www.googleapis.com/youtube/v3/videos"
            videos_params = {
                "key": api_key,
                "part": "snippet,statistics,contentDetails",
                "id": video_id,
            }

            try:
                videos_response, videos_json = request_json(videos_url, videos_params)
                if videos_response.status_code == 200 and videos_json.get("items"):
                    video_item = videos_json["items"][0]
                    statistics = video_item.get("statistics", {})
                    tags = video_item.get("snippet", {}).get("tags", [])
                    duration = video_item.get("contentDetails", {}).get("duration", "")
                elif videos_response.status_code != 200:
                    summary["errors"].append(
                        f"[{skill}:{video_id}] videos request returned {_status_with_reason(videos_response.status_code, videos_json)}"
                    )
                    if _is_quota_error(videos_json):
                        summary["quota_exceeded"] = True
                        stop_due_to_quota = True
            except requests.RequestException as e:
                summary["errors"].append(f"[{skill}:{video_id}] videos request failed: {e}")

            if stop_due_to_quota:
                break

            view_count = to_int(statistics.get("viewCount", 0), 0)
            like_count = to_int(statistics.get("likeCount", 0), 0)

            if view_count < max(0, min_view_count) or like_count < max(0, min_like_count):
                summary["videos_filtered_constraints"] += 1
                continue

            comments = []
            if comments_max_results > 0:
                comments_url = "https://www.googleapis.com/youtube/v3/commentThreads"
                comments_params = {
                    "key": api_key,
                    "part": "snippet",
                    "videoId": video_id,
                    "maxResults": comments_max_results,
                    "textFormat": "plaintext",
                    "order": "relevance",
                }

                try:
                    comments_response, comments_json = request_json(comments_url, comments_params)
                    if comments_response.status_code == 200:
                        comments = extract_comments(comments_json.get("items", []))
                    else:
                        summary["errors"].append(
                            f"[{skill}:{video_id}] comments request returned {_status_with_reason(comments_response.status_code, comments_json)}"
                        )
                        if _is_quota_error(comments_json):
                            summary["quota_exceeded"] = True
                            stop_due_to_quota = True
                except requests.RequestException as e:
                    summary["errors"].append(f"[{skill}:{video_id}] comments request failed: {e}")

            if stop_due_to_quota:
                break

            snippet = item.get("snippet", {})
            doc = {
                "sector": sector,
                "skill_name": skill,
                "videoId": video_id,
                "publishedAt": snippet.get("publishedAt", ""),
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "viewCount": view_count,
                "likeCount": like_count,
                "tags": tags,
                "comments": comments,
                "duration": parse_duration(duration),
                "ingested_timing": datetime.now(),
            }

            result = collection.update_one(
                {"videoId": video_id, "skill_name": skill},
                {"$set": doc},
                upsert=True,
            )
            summary["upserts_attempted"] += 1

            if result.upserted_id is not None:
                summary["inserted"] += 1
            elif result.modified_count > 0:
                summary["updated"] += 1
            else:
                summary["unchanged"] += 1

    summary["error_count"] = len(summary["errors"])
    summary["errors"] = summary["errors"][:MAX_ERROR_DETAILS]
    return summary
