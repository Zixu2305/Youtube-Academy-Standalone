"""
api/routes/quiz.py
------------------
FastAPI endpoints for rule-based MCQ quiz generation.

Endpoints
---------
POST /api/quiz/generate
    Generate (or retrieve the cached) 5-question MCQ quiz for a specific
    selection path:  sector → skill → competency → proficiency_level
                     → proficiency_description

GET  /api/quiz/{quiz_key}
    Retrieve a previously generated quiz by its stable cache key.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# Ensure the project root is on sys.path so that `pipelines.*` is importable
# regardless of how / where uvicorn is launched from.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipelines.quiz_gen.quiz_store import get_or_create_quiz, get_cached_quiz, store_quiz_submission  # noqa: E402

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class QuizGenerateRequest(BaseModel):
    sector: str = Field(..., min_length=1, description="Sector selected by the user.")
    skill: str = Field(..., min_length=1, description="Skill title selected by the user.")
    competency: str = Field(
        ...,
        min_length=1,
        description=(
            "Competency description selected by the user, "
            "prefixed with its type, e.g. 'knowledge: Understand data pipeline architectures'."
        ),
    )
    proficiency_level: str = Field(..., min_length=1, description="Proficiency level, e.g. 'Level 3'.")
    proficiency_description: str = Field(
        default="",
        description="Proficiency description / requirement text for the selected level.",
    )


class QuizOption(BaseModel):
    A: str
    B: str
    C: str
    D: str


class QuizQuestion(BaseModel):
    question_number: int | None = None  # Optional; questions will be randomly arranged in MongoDB
    question: str
    options: QuizOption
    correct: str
    explanation: str


class QuizResponse(BaseModel):
    quiz_key: str
    sector: str
    skill: str
    competency: str
    proficiency_level: str
    proficiency_description: str
    questions: list[QuizQuestion]


class QuizStoreRequest(BaseModel):
    sector: str = Field(..., min_length=1, description="Sector selected by the user.")
    skill: str = Field(..., min_length=1, description="Skill title selected by the user.")
    competency: str = Field(
        ...,
        min_length=1,
        description="Competency description with type prefix, e.g. 'knowledge: ...'",
    )
    proficiency_level: str = Field(..., min_length=1, description="Proficiency level, e.g. 'Level 3'.")
    proficiency_description: str = Field(
        default="",
        description="Proficiency description / requirement text.",
    )
    item_type: str = Field(..., description="Either 'knowledge' or 'ability'.")
    questions: list[QuizQuestion] = Field(..., description="List of generated quiz questions.")


class QuizStoreResponse(BaseModel):
    success: bool
    mongo_id: str | None = None
    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/quiz/generate", response_model=QuizResponse, tags=["quiz"])
def generate_quiz(payload: QuizGenerateRequest) -> dict:
    """
    Generate or retrieve the fixed 5-question MCQ quiz for the given
    selection path.

    The quiz is generated once and then cached permanently.
    The same inputs will always return exactly the same questions and answers.
    """
    try:
        result = get_or_create_quiz(
            sector=payload.sector.strip(),
            skill=payload.skill.strip(),
            competency=payload.competency.strip(),
            proficiency_level=payload.proficiency_level.strip(),
            proficiency_description=payload.proficiency_description.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Quiz generation failed: {exc}",
        ) from exc

    return result


@router.get("/quiz/{quiz_key}", response_model=QuizResponse, tags=["quiz"])
def get_quiz(quiz_key: str) -> dict:
    """
    Retrieve a previously generated quiz by its stable cache key.

    Returns 404 if the quiz has not been generated yet.
    You can obtain the quiz_key from the POST /api/quiz/generate response.
    """
    entry = get_cached_quiz(quiz_key)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"No quiz found for key '{quiz_key}'. "
                   f"Generate it first via POST /api/quiz/generate.",
        )
    return entry


@router.post("/quiz/store", response_model=QuizStoreResponse, tags=["quiz"])
def store_quiz_endpoint(payload: QuizStoreRequest) -> dict:
    """
    Store a generated quiz in MongoDB after user confirms the questions.

    This endpoint saves the quiz to the Quiz_Generation MongoDB collection
    for tracking and auditing purposes.

    Returns:
        {
          "success": bool,
          "mongo_id": str (ObjectId from MongoDB) or null,
          "message": str (success or error message)
        }
    """
    try:
        result = store_quiz_submission(
            sector=payload.sector.strip(),
            skill=payload.skill.strip(),
            competency=payload.competency.strip(),
            proficiency_level=payload.proficiency_level.strip(),
            proficiency_description=payload.proficiency_description.strip(),
            item_type=payload.item_type.strip(),
            questions=[q.dict() for q in payload.questions],
        )
        return result
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to store quiz: {str(exc)}",
        ) from exc

