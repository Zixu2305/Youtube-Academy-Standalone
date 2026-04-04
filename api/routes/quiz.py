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

from pipelines.quiz_gen.quiz_store import make_quiz_key, store_quiz_submission, stream_quiz  # noqa: E402
from pipelines.quiz_gen.quiz_mongo import get_quiz_from_mongo, add_questions_to_existing_quiz  # noqa: E402
from pipelines.quiz_gen.quiz_data_access import fetch_quiz_context, parse_competency_string  # noqa: E402
from pipelines.quiz_gen.quiz_engine import generate_quiz_stream  # noqa: E402

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
    quiz_mode: str = Field(..., description="Quiz mode: competency, knowledge, ability, proficiency, skill.")


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
    Retrieve the quiz from MongoDB Quiz_Generation collection for the given
    selection path (sector → skill → proficiency_level → competency) and quiz mode.

    If found in MongoDB:
      - If it has >= 5 questions, return as-is.
      - If it has < 5 questions, generate additional questions to reach 5 total,
        save them to MongoDB, and return the combined set.
    
    If not found in MongoDB, generate 5 fresh questions using Groq, save to MongoDB,
    and return them.
    
    Ensures minimum of 5 questions are always returned.
    """
    sector = payload.sector.strip()
    skill = payload.skill.strip()
    proficiency_level = payload.proficiency_level.strip()
    competency = payload.competency.strip()
    proficiency_description = payload.proficiency_description.strip()
    quiz_mode = payload.quiz_mode.strip()
    
    # Make a deterministic quiz key
    quiz_key = make_quiz_key(
        sector, skill, competency, proficiency_level, proficiency_description
    )
    
    # Try to get quiz from MongoDB first
    result = get_quiz_from_mongo(
        quiz_mode=quiz_mode,
        sector=sector,
        skill=skill,
        proficiency_level=proficiency_level,
        competency=competency,
    )
    
    # If found in MongoDB, check if it needs filling
    if result is not None:
        current_count = len(result.get("questions", []))
        
        if current_count >= 5:
            # Enough questions, return as-is
            return result
        
        # Need to fill: generate additional questions
        try:
            questions_needed = 5 - current_count
            # Fetch context from MySQL (respecting quiz_mode)
            ctx = fetch_quiz_context(sector, skill, competency, proficiency_level, quiz_mode)
            if proficiency_description:
                ctx["proficiency_description"] = proficiency_description
            
            # Generate additional questions (will be 5 total, cycle through types)
            new_questions = list(generate_quiz_stream(ctx, num_questions=questions_needed))
            
            # Determine item_type based on quiz_mode
            if quiz_mode == "knowledge":
                item_type = "knowledge"
            elif quiz_mode == "ability":
                item_type = "ability"
            else:
                # For other modes (competency, proficiency, skill), infer from competency string
                item_type = "knowledge"
                if result.get("questions"):
                    comp_str = result.get("competency", "")
                    if comp_str.lower().startswith("ability:"):
                        item_type = "ability"
            
            # Save additional questions to MongoDB
            add_questions_to_existing_quiz(
                quiz_key=quiz_key,
                sector=sector,
                skill=skill,
                competency=competency,
                proficiency_level=proficiency_level,
                proficiency_description=proficiency_description,
                item_type=item_type,
                questions=new_questions,
            )
            
            # Combine old and new questions
            combined_questions = result.get("questions", []) + [
                {
                    "question_number": current_count + i + 1,
                    "question": q.get("question", ""),
                    "options": q.get("options", {}),
                    "correct": q.get("correct", ""),
                    "explanation": q.get("explanation", ""),
                }
                for i, q in enumerate(new_questions)
            ]
            
            result["questions"] = combined_questions
            return result
            
        except Exception as exc:
            # If filling fails, still return what we have (don't error out)
            print(f"Warning: Failed to fill quiz: {exc}")
            return result
    
    # Quiz not found in MongoDB: generate fresh from scratch
    try:
        # Fetch context from MySQL (respecting quiz_mode)
        ctx = fetch_quiz_context(sector, skill, competency, proficiency_level, quiz_mode)
        if proficiency_description:
            ctx["proficiency_description"] = proficiency_description
        
        # Generate 5 questions using Groq
        questions = list(generate_quiz_stream(ctx, num_questions=5))
        
        # Determine item_type based on quiz_mode
        if quiz_mode == "knowledge":
            item_type = "knowledge"
        elif quiz_mode == "ability":
            item_type = "ability"
        else:
            # For other modes (competency, proficiency, skill), infer from competency string
            item_type = "knowledge"
            if competency.lower().startswith("ability:"):
                item_type = "ability"
        
        # Build quiz data for storage
        quiz_data = {
            "quiz_key": quiz_key,
            "sector": sector,
            "skill": skill,
            "competency": competency,
            "proficiency_level": proficiency_level,
            "proficiency_description": proficiency_description,
            "item_type": item_type,
            "questions": questions,
        }
        
        # Store to MongoDB for future use
        from pipelines.quiz_gen.quiz_mongo import store_quiz_in_mongo
        store_quiz_in_mongo(quiz_data)
        
        # Build response
        return {
            "quiz_key": quiz_key,
            "sector": sector,
            "skill": skill,
            "competency": competency,
            "proficiency_level": proficiency_level,
            "proficiency_description": proficiency_description,
            "questions": [
                {
                    "question_number": i + 1,
                    "question": q.get("question", ""),
                    "options": q.get("options", {}),
                    "correct": q.get("correct", ""),
                    "explanation": q.get("explanation", ""),
                }
                for i, q in enumerate(questions)
            ]
        }
    except Exception as exc:
        err_text = str(exc).lower()
        if "rate limit" in err_text or "429" in err_text:
            raise HTTPException(
                status_code=429,
                detail="Quiz generation is temporarily rate-limited by the configured LLM provider. Please retry in a few seconds.",
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate quiz: {str(exc)}. "
                   f"Ensure LLM provider/model/API key and MySQL are configured.",
        ) from exc


@router.get("/quiz/{quiz_key}", response_model=QuizResponse, tags=["quiz"])
def get_quiz(quiz_key: str) -> dict:
    """
    Retrieve a previously stored quiz from MongoDB by its stable quiz key.
    
    For backwards compatibility. Uses quiz_key directly.

    Returns 404 if the quiz has not been stored in MongoDB yet.
    """
    result = get_quiz_from_mongo(quiz_key=quiz_key)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No quiz found in MongoDB for key '{quiz_key}'. "
                   f"Ensure the quiz has been generated and stored first.",
        )
    return result


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

