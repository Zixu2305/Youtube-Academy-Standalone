"""
quiz_store.py
-------------
Persistent JSON cache for generated quizzes.

Rules
-----
* A quiz is generated exactly ONCE per unique selection key
  (sector + skill + competency + proficiency_level + proficiency_description).
* Subsequent requests for the same key return the cached version unchanged —
  this fulfils the "questions stay fixed" requirement.
* The cache file lives at  pipelines/quiz_gen/data/quiz_cache.json
  and is safe to commit to version control (no credentials, no LLM output).
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from .quiz_data_access import fetch_quiz_context
from .quiz_engine import generate_quiz, generate_quiz_stream

# ---------------------------------------------------------------------------
# Cache file location
# ---------------------------------------------------------------------------

_CACHE_DIR = Path(__file__).parent / "data"
_CACHE_FILE = _CACHE_DIR / "quiz_cache.json"

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------

def make_quiz_key(
    sector: str,
    skill: str,
    competency: str,
    proficiency_level: str,
    proficiency_description: str,
) -> str:
    """
    Return a short hex string that uniquely identifies this exact selection.
    Using SHA-256 keeps the key stable across Python versions.
    """
    raw = f"{sector}|{skill}|{competency}|{proficiency_level}|{proficiency_description}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Low-level cache I/O  (thread-safe)
# ---------------------------------------------------------------------------

def _load_cache() -> dict[str, Any]:
    if not _CACHE_FILE.exists():
        return {}
    try:
        with _CACHE_FILE.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _CACHE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_cached_quiz(quiz_key: str) -> dict[str, Any] | None:
    """Return a cached quiz entry or None if it has not been generated yet."""
    with _lock:
        cache = _load_cache()
    return cache.get(quiz_key)


def store_quiz(quiz_key: str, entry: dict[str, Any]) -> None:
    """Persist a quiz entry.  Existing entries are NEVER overwritten."""
    with _lock:
        cache = _load_cache()
        if quiz_key not in cache:
            cache[quiz_key] = entry
            _save_cache(cache)


def get_or_create_quiz(
    sector: str,
    skill: str,
    competency: str,
    proficiency_level: str,
    proficiency_description: str,
) -> dict[str, Any]:
    """
    Return the quiz for this exact selection.

    On the first call the quiz is generated, stored, and returned.
    All subsequent calls return the stored version — the questions never change.

    Returns:
        {
          "quiz_key": str,
          "sector": str,
          "skill": str,
          "competency": str,
          "proficiency_level": str,
          "proficiency_description": str,
          "questions": [ {question_number, question, options, correct, explanation}, … ]
        }
    """
    quiz_key = make_quiz_key(
        sector, skill, competency, proficiency_level, proficiency_description
    )

    # --- cache hit ---
    cached = get_cached_quiz(quiz_key)
    if cached is not None:
        return cached

    # --- cache miss: generate once ---
    ctx = fetch_quiz_context(sector, skill, competency, proficiency_level)
    # Overwrite proficiency_description with what the caller passed in
    # (the caller may have obtained it from the UI selection step).
    if proficiency_description:
        ctx["proficiency_description"] = proficiency_description

    questions = generate_quiz(ctx)

    entry: dict[str, Any] = {
        "quiz_key": quiz_key,
        "sector": sector,
        "skill": skill,
        "competency": competency,
        "proficiency_level": proficiency_level,
        "proficiency_description": proficiency_description,
        "questions": questions,
    }

    store_quiz(quiz_key, entry)
    return entry


def stream_or_cached_quiz(
    sector: str,
    skill: str,
    competency: str,
    proficiency_level: str,
    proficiency_description: str,
):
    """
    Generator that streams quiz questions one at a time.

    Yields dicts of the form:
      {"type": "context",  "quiz_key": str, "sector": str, ...}
      {"type": "question", "question_number": int, ...question fields}
      {"type": "done",     "quiz_key": str}        ← final event
      {"type": "error",    "message": str}          ← only on failure

    If the quiz is already cached, all questions stream immediately.
    If not cached, questions stream as Ollama generates them (one at a time)
    and the quiz is stored after the last question.
    """
    quiz_key = make_quiz_key(
        sector, skill, competency, proficiency_level, proficiency_description
    )

    # ── cache hit: stream the stored questions instantly ──────────
    cached = get_cached_quiz(quiz_key)
    if cached is not None:
        yield {"type": "context", "quiz_key": quiz_key,
               "sector": sector, "skill": skill,
               "competency": competency,
               "proficiency_level": proficiency_level,
               "proficiency_description": proficiency_description,
               "from_cache": True}
        for q in cached["questions"]:
            yield {"type": "question", **q}
        yield {"type": "done", "quiz_key": quiz_key}
        return

    # ── cache miss: generate via Ollama, stream each question ─────
    try:
        ctx = fetch_quiz_context(sector, skill, competency, proficiency_level)
        if proficiency_description:
            ctx["proficiency_description"] = proficiency_description
    except Exception as exc:
        yield {"type": "error", "message": str(exc)}
        return

    yield {"type": "context", "quiz_key": quiz_key,
           "sector": sector, "skill": skill,
           "competency": competency,
           "proficiency_level": proficiency_level,
           "proficiency_description": proficiency_description,
           "from_cache": False}

    questions: list[Any] = []
    try:
        for q in generate_quiz_stream(ctx):
            questions.append(q)
            yield {"type": "question", **q}
    except Exception as exc:
        yield {"type": "error", "message": f"Quiz generation failed: {exc}"}
        return

    # persist the complete quiz
    entry: dict[str, Any] = {
        "quiz_key": quiz_key,
        "sector": sector, "skill": skill,
        "competency": competency,
        "proficiency_level": proficiency_level,
        "proficiency_description": proficiency_description,
        "questions": questions,
    }
    store_quiz(quiz_key, entry)
    yield {"type": "done", "quiz_key": quiz_key}
