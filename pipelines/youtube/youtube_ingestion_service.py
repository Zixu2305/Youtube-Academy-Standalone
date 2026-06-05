from datetime import datetime, timedelta
from html import unescape
import json
import os
import re

import requests

try:
    from youtube_config import MAX_ERROR_DETAILS, REQUEST_TIMEOUT_SECONDS, to_int
    from youtube_data_access import get_requirement, get_proficiency_description
except ModuleNotFoundError:  # pragma: no cover - package import path
    from .youtube_config import MAX_ERROR_DETAILS, REQUEST_TIMEOUT_SECONDS, to_int
    from .youtube_data_access import get_requirement, get_proficiency_description

try:
    from pipelines.llm_client import call_llm_chat
except ModuleNotFoundError:  # pragma: no cover - package import path
    from ..llm_client import call_llm_chat


QUOTA_ERROR_REASONS = {
    "quotaExceeded",
    "dailyLimitExceeded",
    "dailyLimitExceeded402",
    "dailyLimitExceededUnreg",
    "rateLimitExceeded",
}
SHORT_FORM_EXCLUSION_TERMS = ("shorts", "#shorts", "short", "reels", "reel")


def looks_like_short_form_video(title: str, description: str) -> bool:
    text = f"{title} {description}".lower()
    return any(term in text for term in SHORT_FORM_EXCLUSION_TERMS)


def clean_youtube_text(value: object) -> str:
    return unescape(str(value or "")).strip()

# --- LLM CONFIGURATION ---
_LLM_TIMEOUT = 10  # Fast timeout for keywords
_LLM_RATE_LIMIT_RETRIES = 1

def _get_llm_keywords(text: str, count: int = 3) -> str:
    """
    Asks configured LLM provider to extract the {count} most important technical terms.
    Returns a space-separated string of keywords.
    """
    if not text:
        return ""
        
    prompt = (
        f"Extract exactly {count} most important technical search terms from the text below "
        f"to find a YouTube tutorial. Return ONLY the keywords separated by spaces. No quotes.\n"
        f"Text: \"{text}\""
    )

    try:
        content = call_llm_chat(
            prompt,
            temperature=0.1,
            max_tokens=50,
            timeout=_LLM_TIMEOUT,
            expect_json=False,
            rate_limit_retries=_LLM_RATE_LIMIT_RETRIES,
            retry_backoff_seconds=1,
        )
        return content.strip()
    except Exception:
        # If LLM is unavailable, unauthenticated, or rate-limited, use regex fallback.
        return ""

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "is", "are", "was", "were",
    "of", "in", "on", "at", "to", "for", "with", "by", "from",
    "this", "that", "these", "those", "it", "its", "as", "be", "have", "has",
    "can", "will", "shall", "do", "does", "did", "not", "no", "yes"
}

def keywordize(text: str, k: int = 8) -> str:
    """
    Extracts top-k longest keywords from text, excluding stopwords.
    """
    if not text:
        return ""
    
    # Simple tokenization: lowercase, remove non-alphanumeric (keep spaces for split)
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", text.lower())
    tokens = cleaned.split()
    
    # Filter stopwords and uniques, keeping order is nice but simple set is fine for keywords
    # Prioritize longer words as 'keywords' often imply significant terms
    unique_tokens = []
    seen = set()
    for t in tokens:
        if t not in STOPWORDS and len(t) > 2 and t not in seen:
            unique_tokens.append(t)
            seen.add(t)
            
    # Sort by length descending, then take top k
    unique_tokens.sort(key=len, reverse=True)
    keywords = unique_tokens[:k]
    
    return " ".join(keywords)


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


def duration_to_minutes(duration_str):
    if not duration_str:
        return 0
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    total_minutes = hours * 60 + minutes + seconds / 60
    return total_minutes


def request_json(url: str, params: dict):
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return response, payload



def estimate_quota_units(skills_count: int, search_max_results: int, comments_enabled: bool):
    skills = max(0, skills_count)
    results = max(0, search_max_results)
    per_video_cost = 1 + (1 if comments_enabled else 0)
    return skills * (100 + (results * per_video_cost))


def build_quota_estimate(
    *,
    skills_count: int,
    search_max_results: int,
    daily_limit: int,
    warning_threshold: int,
):
    estimate_units = estimate_quota_units(skills_count, search_max_results, False)

    level = "ok"
    if estimate_units >= daily_limit:
        level = "over_limit"
    elif estimate_units >= warning_threshold:
        level = "warning"

    return {
        "estimated_units": estimate_units,
        "daily_limit": daily_limit,
        "warning_threshold": warning_threshold,
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
    if published_after:
        search_params["publishedAfter"] = published_after


def _normalize_query_includes(query_includes: dict | None):
    defaults = {
        "sector": True,
        "skill": True,
        "competency": True,
        "requirement": True,
    }
    if not query_includes:
        return defaults
    return {
        "sector": bool(query_includes.get("sector", defaults["sector"])),
        "skill": bool(query_includes.get("skill", defaults["skill"])),
        "competency": bool(query_includes.get("competency", defaults["competency"])),
        "requirement": bool(query_includes.get("requirement", defaults["requirement"])),
    }


def _build_query_parts(
    *,
    sector: str,
    skill: str,
    competency: str,
    proficiency: str,
    requirement: str,
    additional_query: str,
    query_includes: dict,
):
    query_parts = []

    if query_includes.get("sector") and sector.strip():
        query_parts.append(sector.strip())
    if query_includes.get("skill") and skill.strip():
        query_parts.append(skill.strip())
    if query_includes.get("competency") and competency.strip():
        query_parts.append(competency.strip())

    if query_includes.get("requirement"):
        requirement_text = requirement.strip()
        if not requirement_text and proficiency.strip():
            description = get_proficiency_description(sector, skill, proficiency.strip())
            if description:
                requirement_text = description.strip()
        if requirement_text:
            query_parts.append(requirement_text)

    if additional_query.strip():
        query_parts.append(additional_query.strip())

    return query_parts


def _build_advanced_queries(
    *,
    sector: str,
    skill: str,
    competency: str,
    additional_query: str,
):
    """
    Builds the single advanced query variant:
    Query: skill + (LLM Keywords OR Regex Keywords) + Base Terms + Negatives
    """
    base_terms = "(tutorial|course|guide|explained|basics|hands-on|project|demo)"
    negatives = "-music -podcast -mix -asmr -trailer -highlights -shorts"

    # Clean inputs
    sk_clean = skill.strip()
    
    # 1. Try LLM for intelligent keyword extraction
    comp_keywords = _get_llm_keywords(competency, 3)
    
    # 2. Fallback to regex if LLM fails
    if not comp_keywords:
        comp_keywords = keywordize(competency, 3)

    parts = [sk_clean, comp_keywords, base_terms, negatives]
    if additional_query:
        parts.append(additional_query.strip())
        
    query = " ".join(filter(None, parts))

    return [query]


def _search_youtube_for_skill(
    *,
    queries: list[str],
    api_key: str,
    search_max_results: int,
    search_order: str,
    search_constraints: dict,
    summary_errors: list,
    skill_name: str,
):
    """
    Runs search.list for each query variant and duration, collecting unique video IDs.
    Returns: set of unique video IDs
    """
    unique_video_ids = set()
    durations = ["medium", "long"]
    search_url = "https://www.googleapis.com/youtube/v3/search"

    for q in queries:
        for duration in durations:
            search_params = {
                "part": "snippet",
                "maxResults": search_max_results,
                "order": search_order,
                "key": api_key,
                "type": "video",
                "q": q,
                "videoEmbeddable": "true",
                "safeSearch": "moderate",
                "videoDuration": duration,
            }
            # Apply date constraint if present
            _apply_search_constraints(search_params, search_constraints)

            try:
                search_response, search_data = request_json(search_url, search_params)
            except requests.RequestException as e:
                summary_errors.append(f"[{skill_name}] search request failed (q={q[:30]}..., d={duration}): {e}")
                continue

            if search_response.status_code != 200:
                summary_errors.append(
                    f"[{skill_name}] search request returned {_status_with_reason(search_response.status_code, search_data)}"
                )
                if _is_quota_error(search_data):
                    # Signal quota error by re-raising or returning specific flag
                    return unique_video_ids, True
                continue

            if "error" in search_data:
                summary_errors.append(f"[{skill_name}] YouTube error: {search_data['error']}")
                if _is_quota_error(search_data):
                    return unique_video_ids, True
                continue

            items = search_data.get("items", [])
            for item in items:
                vid = item.get("id", {}).get("videoId")
                if vid:
                    unique_video_ids.add(vid)
                    
    return unique_video_ids, False


def fetch_videos_for_preview(
    *,
    sector: str,
    api_key: str,
    search_max_results: int,
    search_order: str,
    selected_skills: list[str],
    competency: str = "",
    proficiency: str = "",
    requirement: str = "",
    min_view_count: int = 0,
    min_like_count: int = 0,
    min_video_length: int = 0,
    max_video_length: int = 0,
    min_comment_count: int = 0,
    max_video_age: int = 0,
    additional_query: str = "",
    query_includes: dict | None = None,
    search_constraints: dict | None = None,
):
    search_constraints = search_constraints or {}
    query_includes = _normalize_query_includes(query_includes)
    unique_skills = list(dict.fromkeys(selected_skills))

    videos = []
    summary = {
        "sector": sector,
        "skills_requested": len(unique_skills),
        "skills_processed": 0,
        "videos_found": 0,
        "videos_filtered_constraints": 0,
        "error_count": 0,
        "errors": [],
        "quota_exceeded": False,
        "constraints": {
            "min_view_count": max(0, min_view_count),
            "min_like_count": max(0, min_like_count),
            "min_video_length": max(0, min_video_length),
            "max_video_length": max(0, max_video_length),
            "min_comment_count": max(0, min_comment_count),
            "max_video_age": max(0, max_video_age),
            "published_after": search_constraints.get("published_after", ""),
            "competency": competency,
            "proficiency": proficiency,
            "requirement": requirement,
            "additional_query": additional_query,
            "query_includes": query_includes,
            "query": "",
        },
    }

    # Convert max_video_age to published_after if not already set
    if max_video_age > 0 and not search_constraints.get("published_after"):
        cutoff_date = datetime.now() - timedelta(days=max_video_age)
        search_constraints["published_after"] = cutoff_date.strftime("%Y-%m-%d")

    stop_due_to_quota = False

    for skill in unique_skills:
        if stop_due_to_quota:
            break

        summary["skills_processed"] += 1

        # Use 1 query variant
        # 3) skill + keywordize(competency, 3) + "(tutorial|...) + negatives
        queries = _build_advanced_queries(
            sector=sector,
            skill=skill,
            competency=competency,
            additional_query=additional_query,
        )

        # Store the first query in summary for reference
        if not summary["constraints"]["query"]:
            summary["constraints"]["query"] = queries[0]

        # Use helper
        # Run search for each query & duration (medium, long)
        unique_video_ids = set()
        
        video_ids, quota_hit = _search_youtube_for_skill(
            queries=queries,
            api_key=api_key,
            search_max_results=search_max_results,
            search_order=search_order,
            search_constraints=search_constraints,
            summary_errors=summary["errors"],
            skill_name=skill,
        )

        if quota_hit:
            summary["quota_exceeded"] = True
            stop_due_to_quota = True

        # Respect the global max results limit per skill by slicing the aggregated list
        # unique_video_ids is a set, convert to list and slice
        video_ids_list = list(video_ids)[:search_max_results]
        summary["videos_found"] += len(video_ids_list)

        for video_id in video_ids_list:
            if stop_due_to_quota:
                break
            
            # Retrieve video details for each ID
            video_item = None
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
            comment_count = to_int(statistics.get("commentCount", 0), 0)
            video_length_minutes = duration_to_minutes(duration)

            if (
                view_count < max(0, min_view_count)
                or like_count < max(0, min_like_count)
                or comment_count < max(0, min_comment_count)
                or (min_video_length > 0 and video_length_minutes < min_video_length)
                or (max_video_length > 0 and video_length_minutes > max_video_length)
            ):
                summary["videos_filtered_constraints"] += 1
                continue

            snippet = video_item.get("snippet", {}) if video_item else {}
            # Extract item_type from competency (formatted as "item_type: text")
            item_type = ""
            if competency and ":" in competency:
                item_type = competency.split(":", 1)[0].strip()
            
            # Get proficiency description from database
            proficiency_description = get_proficiency_description(sector, skill, proficiency.strip())
            
            video_doc = {
                "sector": sector,
                "skill_name": skill,
                "competency": competency,
                "item_type": item_type,
                "proficiency_level": proficiency,
                "proficiency_description": proficiency_description,
                "videoId": video_id,
                "publishedAt": clean_youtube_text(snippet.get("publishedAt", "")),
                "title": clean_youtube_text(snippet.get("title", "")),
                "description": clean_youtube_text(snippet.get("description", "")),
                "viewCount": view_count,
                "likeCount": like_count,
                "commentCount": comment_count,
                "tags": tags,
                "duration": parse_duration(duration),
                "channelTitle": clean_youtube_text(snippet.get("channelTitle", "")),
                "thumbnailUrl": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
            }
            videos.append(video_doc)
    summary["error_count"] = len(summary["errors"])
    summary["errors"] = summary["errors"][:MAX_ERROR_DETAILS]
    return videos, summary


def fetch_direct_youtube_search(
    *,
    query: str,
    api_key: str,
    search_max_results: int,
    search_order: str = "relevance",
    search_constraints: dict | None = None,
):
    search_constraints = search_constraints or {}
    clean_query = query.strip()
    summary = {
        "query": clean_query,
        "videos_found": 0,
        "error_count": 0,
        "errors": [],
        "quota_exceeded": False,
    }
    if not clean_query:
        return [], summary

    duration_items = {"medium": [], "long": []}
    seen_video_ids = set()
    for duration in ("medium", "long"):
        search_params = {
            "part": "snippet",
            "maxResults": search_max_results,
            "order": search_order,
            "key": api_key,
            "type": "video",
            "q": clean_query,
            "videoEmbeddable": "true",
            "videoDuration": duration,
            "safeSearch": "moderate",
        }
        _apply_search_constraints(search_params, search_constraints)

        try:
            search_response, search_data = request_json("https://www.googleapis.com/youtube/v3/search", search_params)
        except requests.RequestException as exc:
            summary["errors"].append(f"search request failed ({duration}): {exc}")
            summary["error_count"] = len(summary["errors"])
            continue

        if search_response.status_code != 200:
            summary["errors"].append(f"search request returned {_status_with_reason(search_response.status_code, search_data)} ({duration})")
            summary["quota_exceeded"] = _is_quota_error(search_data)
            summary["error_count"] = len(summary["errors"])
            if summary["quota_exceeded"]:
                break
            continue

        if "error" in search_data:
            summary["errors"].append(f"YouTube error ({duration}): {search_data['error']}")
            summary["quota_exceeded"] = _is_quota_error(search_data)
            summary["error_count"] = len(summary["errors"])
            if summary["quota_exceeded"]:
                break
            continue

        for item in search_data.get("items", []):
            video_id = item.get("id", {}).get("videoId")
            if not video_id or video_id in seen_video_ids:
                continue
            seen_video_ids.add(video_id)
            duration_items[duration].append(item)
            if len(duration_items[duration]) >= search_max_results:
                break

    search_items = []
    for index in range(search_max_results):
        for duration in ("medium", "long"):
            if index < len(duration_items[duration]):
                search_items.append(duration_items[duration][index])
            if len(search_items) >= search_max_results:
                break
        if len(search_items) >= search_max_results:
            break

    video_ids = [
        item.get("id", {}).get("videoId")
        for item in search_items
        if item.get("id", {}).get("videoId")
    ]
    summary["videos_found"] = len(video_ids)
    if not video_ids:
        return [], summary

    videos = []
    for item in search_items:
        video_id = item.get("id", {}).get("videoId", "")
        if not video_id:
            continue
        snippet = item.get("snippet", {})
        title = clean_youtube_text(snippet.get("title", ""))
        description = clean_youtube_text(snippet.get("description", ""))
        if looks_like_short_form_video(title, description):
            continue
        thumbnails = snippet.get("thumbnails", {})
        thumbnail = (
            thumbnails.get("medium", {}).get("url")
            or thumbnails.get("default", {}).get("url")
            or ""
        )
        videos.append({
            "sector": "",
            "skill_name": "",
            "competency": "",
            "item_type": "",
            "proficiency_level": "",
            "proficiency_description": "",
            "videoId": video_id,
            "publishedAt": snippet.get("publishedAt", ""),
            "title": title,
            "description": description,
            "viewCount": 0,
            "likeCount": 0,
            "commentCount": 0,
            "tags": [],
            "duration": "",
            "channelTitle": clean_youtube_text(snippet.get("channelTitle", "")),
            "thumbnailUrl": thumbnail,
        })

    summary["error_count"] = len(summary["errors"])
    summary["errors"] = summary["errors"][:MAX_ERROR_DETAILS]
    return videos, summary


def run_ingestion(
    collection,
    *,
    sector: str,
    api_key: str,
    search_max_results: int,
    search_order: str,
    selected_skills: list[str],
    competency: str = "",
    proficiency: str = "",
    requirement: str = "",
    min_view_count: int = 0,
    min_like_count: int = 0,
    min_video_length: int = 0,
    max_video_length: int = 0,
    min_comment_count: int = 0,
    max_video_age: int = 0,
    additional_query: str = "",
    query_includes: dict | None = None,
    search_constraints: dict | None = None,
    touched_docs: list[dict] | None = None,
):
    search_constraints = search_constraints or {}
    query_includes = _normalize_query_includes(query_includes)
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
            "min_video_length": max(0, min_video_length),
            "max_video_length": max(0, max_video_length),
            "min_comment_count": max(0, min_comment_count),
            "max_video_age": max(0, max_video_age),
            "published_after": search_constraints.get("published_after", ""),
            "competency": competency,
            "proficiency": proficiency,
            "requirement": requirement,
            "additional_query": additional_query,
            "query_includes": query_includes,
            "query": "",
        },
    }

    # Convert max_video_age to published_after if not already set
    if max_video_age > 0 and not search_constraints.get("published_after"):
        cutoff_date = datetime.now() - timedelta(days=max_video_age)
        search_constraints["published_after"] = cutoff_date.strftime("%Y-%m-%d")

    stop_due_to_quota = False

    for skill in unique_skills:
        if stop_due_to_quota:
            break

        summary["skills_processed"] += 1

        # Use 3 query variants
        queries = _build_advanced_queries(
            sector=sector,
            skill=skill,
            competency=competency,
            additional_query=additional_query,
        )

        # Store the first query in summary
        if not summary["constraints"]["query"]:
            summary["constraints"]["query"] = queries[0]

        # Search for each query & duration
        video_ids, quota_hit = _search_youtube_for_skill(
            queries=queries,
            api_key=api_key,
            search_max_results=search_max_results,
            search_order=search_order,
            search_constraints=search_constraints,
            summary_errors=summary["errors"],
            skill_name=skill,
        )

        if quota_hit:
            summary["quota_exceeded"] = True
            stop_due_to_quota = True

        # Respect the global max results limit per skill by slicing the aggregated list
        video_ids_list = list(video_ids)[:search_max_results]
        summary["videos_found"] += len(video_ids_list)

        for video_id in video_ids_list:
            if stop_due_to_quota:
                break
            
            summary["videos_processed"] += 1

            video_item = None
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
            comment_count = to_int(statistics.get("commentCount", 0), 0)
            video_length_minutes = duration_to_minutes(duration)

            if (
                view_count < max(0, min_view_count)
                or like_count < max(0, min_like_count)
                or comment_count < max(0, min_comment_count)
                or (min_video_length > 0 and video_length_minutes < min_video_length)
                or (max_video_length > 0 and video_length_minutes > max_video_length)
            ):
                summary["videos_filtered_constraints"] += 1
                continue

            comments = []

            if stop_due_to_quota:
                break

            snippet = video_item.get("snippet", {}) if video_item else {}

            doc = {
                "sector": sector,
                "skill_name": skill,
                "competency": competency,
                "proficiency_level": proficiency,
                "proficiency_description": requirement,
                "videoId": video_id,
                "publishedAt": clean_youtube_text(snippet.get("publishedAt", "")),
                "title": clean_youtube_text(snippet.get("title", "")),
                "description": clean_youtube_text(snippet.get("description", "")),
                "viewCount": view_count,
                "likeCount": like_count,
                "commentCount": comment_count,
                "tags": tags,
                "duration": parse_duration(duration),
                "ingested_timing": datetime.now(),
            }

            result = collection.update_one(
                {"videoId": video_id, "skill_name": skill},
                {"$set": doc},
                upsert=True,
            )
            summary["upserts_attempted"] += 1
            if touched_docs is not None:
                touched_docs.append(dict(doc))

            if result.upserted_id is not None:
                summary["inserted"] += 1
            elif result.modified_count > 0:
                summary["updated"] += 1
            else:
                summary["unchanged"] += 1

    summary["error_count"] = len(summary["errors"])
    summary["errors"] = summary["errors"][:MAX_ERROR_DETAILS]
    return summary


def upsert_selected_videos(
    collection,
    videos_to_upsert: list[dict],
    *,
    touched_docs: list[dict] | None = None,
):
    """
    Upsert selected videos into MongoDB.
    
    Args:
        collection: MongoDB collection to upsert into
        videos_to_upsert: List of video documents to upsert
        
    Returns:
        Summary dict with upsert statistics
    """
    summary = {
        "videos_to_upsert": len(videos_to_upsert),
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "error_count": 0,
        "errors": [],
    }
    
    for video in videos_to_upsert:
        try:
            # Add ingestion timestamp
            video["ingested_timing"] = datetime.now()
            
            # Extract videoId and skill_name for the filter
            video_id = video.get("videoId")
            skill_name = video.get("skill_name")
            
            if not video_id or not skill_name:
                summary["errors"].append(f"Missing videoId or skill_name in video document")
                summary["error_count"] += 1
                continue
            
            result = collection.update_one(
                {"videoId": video_id, "skill_name": skill_name},
                {"$set": video},
                upsert=True,
            )
            if touched_docs is not None:
                touched_docs.append(dict(video))
            
            if result.upserted_id is not None:
                summary["inserted"] += 1
            elif result.modified_count > 0:
                summary["updated"] += 1
            else:
                summary["unchanged"] += 1
                
        except Exception as e:
            summary["errors"].append(f"Failed to upsert video {video.get('videoId', 'unknown')}: {str(e)}")
            summary["error_count"] += 1
    
    summary["errors"] = summary["errors"][:MAX_ERROR_DETAILS]
    return summary
