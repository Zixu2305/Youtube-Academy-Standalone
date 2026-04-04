"""
quiz_store.py
-------------
Quiz storage utilities for MongoDB operations.

This module provides utilities for generating quiz keys and storing quiz submissions to MongoDB.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .quiz_data_access import fetch_quiz_context
from .quiz_engine import generate_quiz_stream
from .quiz_mongo import store_quiz_in_mongo


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
# Public API
# ---------------------------------------------------------------------------

def store_quiz_submission(
    sector: str,
    skill: str,
    competency: str,
    proficiency_level: str,
    proficiency_description: str,
    item_type: str,
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Store generated quiz questions in MongoDB after user confirmation (1 doc per question).

    Args:
        sector: Selected sector
        skill: Selected skill
        competency: Selected competency (full string with type prefix)
        proficiency_level: Proficiency level (e.g., "Level 1")
        proficiency_description: Full proficiency description
        item_type: "knowledge" or "ability"
        questions: List of question objects from the generated quiz

    Returns:
        {
          "success": bool,
          "mongo_ids": list of str or None,
          "question_count": int,
          "message": str
        }
    """
    try:
        # Prepare the quiz data for MongoDB
        quiz_data = {
            "quiz_key": make_quiz_key(
                sector, skill, competency, proficiency_level, proficiency_description
            ),
            "sector": sector,
            "skill": skill,
            "competency": competency,
            "proficiency_level": proficiency_level,
            "proficiency_description": proficiency_description,
            "item_type": item_type,
            "questions": questions,
        }

        mongo_ids = store_quiz_in_mongo(quiz_data)
        return {
            "success": mongo_ids is not None,
            "mongo_ids": mongo_ids,
            "question_count": len(questions),
            "message": "Quiz stored successfully" if mongo_ids else "Failed to store quiz",
        }
    except Exception as exc:
        return {
            "success": False,
            "mongo_ids": None,
            "question_count": len(questions),
            "message": f"Failed to store quiz: {str(exc)}",
        }


def stream_quiz(
    sector: str,
    skill: str,
    competency: str,
    proficiency_level: str,
    proficiency_description: str,
    quiz_mode: str = "competency",
    question_types: list[str] | None = None,
    num_questions: int = 5,
):
    """
    Generator that streams quiz questions one at a time using LLM generation.

    Args:
        quiz_mode: Quiz mode for context filtering (default: "competency").
        question_types: List of question types to include (default: all 5 types).
        num_questions: Number of questions to generate (default: 5).

    Yields dicts of the form:
      {"type": "context",  "quiz_key":str, "sector": str, ...context fields}
      {"type": "question", "question_number":int, ...question fields}
      {"type": "done",     "quiz_key":str}        ← final event
      {"type": "error",    "message": str}          ← only on failure

    Generates fresh questions using Groq LLM.
    """
    if question_types is None:
        question_types = ["Conceptual", "Application", "Scenario-Based", "Technical", "Evaluation"]
    num_questions = max(1, min(num_questions, 20))  # clamp to 1-20
    
    quiz_key = make_quiz_key(
        sector, skill, competency, proficiency_level, proficiency_description
    )

    # Generate fresh questions via Groq, stream each question
    try:
        ctx = fetch_quiz_context(sector, skill, competency, proficiency_level, quiz_mode)
        if proficiency_description:
            ctx["proficiency_description"] = proficiency_description
    except Exception as exc:
        yield {"type": "error", "message": str(exc)}
        return

    yield {"type": "context", "quiz_key": quiz_key,
           "sector": sector, "skill": skill,
           "competency": competency,
           "proficiency_level": proficiency_level,
           "proficiency_description": proficiency_description}

    questions: list[Any] = []
    try:
        for q in generate_quiz_stream(ctx, question_types, num_questions):
            questions.append(q)
            yield {"type": "question", **q}
    except Exception as exc:
        yield {"type": "error", "message": f"Quiz generation failed: {exc}"}
        return

    yield {"type": "done", "quiz_key": quiz_key}