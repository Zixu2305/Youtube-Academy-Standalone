"""
quiz_mongo.py
-------------
MongoDB operations for storing generated quizzes.

Collection: Quiz_Generation
Document schema: All 5 questions in one document per unique quiz selection.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pymongo import MongoClient

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


# ---------------------------------------------------------------------------
# MongoDB configuration
# ---------------------------------------------------------------------------

def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def get_mongo_client() -> MongoClient:
    """Create and return a MongoDB client."""
    mongo_host = _env("MONGO_HOST", "127.0.0.1")
    mongo_port = _env("MONGO_PORT", "27017")
    mongo_user = _env("MONGO_ROOT_USERNAME", "")
    mongo_pass = _env("MONGO_ROOT_PASSWORD", "")
    mongo_db = _env("MONGO_DATABASE", "yta")
    mongo_auth_source = _env("MONGO_AUTH_SOURCE", "admin")

    if mongo_user and mongo_pass:
        uri = (
            f"mongodb://{mongo_user}:{mongo_pass}@{mongo_host}:{mongo_port}/"
            f"{mongo_db}?authSource={mongo_auth_source}"
        )
    else:
        uri = f"mongodb://{mongo_host}:{mongo_port}/{mongo_db}"

    return MongoClient(uri)


def store_quiz_in_mongo(quiz_data: dict[str, Any]) -> str | None:
    """
    Store a generated quiz in MongoDB.

    Args:
        quiz_data: Dictionary containing quiz information with structure:
        {
          "quiz_key": str,
          "sector": str,
          "skill": str,
          "competency": str,
          "proficiency_level": str,
          "proficiency_description": str,
          "item_type": "knowledge" | "ability",
          "questions": [
            {
              "question": str,
              "question_type": str,
              "options": {A, B, C, D},
              "correct": str,
              "explanation": str
            },
            ...
          ]
        }

    Returns:
        The MongoDB _id of the inserted document, or None if insertion failed.
    """
    try:
        client = get_mongo_client()
        db = client.get_default_database()
        collection = db["Quiz_Generation"]

        # Build the document
        doc = {
            "quiz_key": quiz_data.get("quiz_key", ""),
            "sector": quiz_data.get("sector", ""),
            "skill": quiz_data.get("skill", ""),
            "proficiency_level": quiz_data.get("proficiency_level", ""),
            "proficiency_description": quiz_data.get("proficiency_description", ""),
            "competency": quiz_data.get("competency", ""),
            "item_type": quiz_data.get("item_type", ""),  # knowledge or ability
            "questions": quiz_data.get("questions", []),
            "ingested_at": datetime.utcnow(),
        }

        # Insert the document
        result = collection.insert_one(doc)
        return str(result.inserted_id)

    except Exception as exc:
        print(f"Error storing quiz in MongoDB: {exc}")
        return None
    finally:
        client.close()


def get_stored_quizzes(filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """
    Retrieve stored quizzes from MongoDB with optional filters.

    Args:
        filters: Dictionary of filter criteria (e.g., {"sector": "Healthcare", "skill": "Patient Communication"})

    Returns:
        List of quiz documents matching the filters.
    """
    try:
        client = get_mongo_client()
        db = client.get_default_database()
        collection = db["Quiz_Generation"]

        query_filters = filters or {}
        quizzes = list(collection.find(query_filters).sort("ingested_at", -1))

        # Convert ObjectId to string for JSON serialization
        for quiz in quizzes:
            if "_id" in quiz:
                quiz["_id"] = str(quiz["_id"])

        return quizzes

    except Exception as exc:
        print(f"Error retrieving quizzes from MongoDB: {exc}")
        return []
    finally:
        client.close()
