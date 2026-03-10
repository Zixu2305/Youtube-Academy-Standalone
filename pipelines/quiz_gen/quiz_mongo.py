"""
quiz_mongo.py
-------------
MongoDB operations for storing generated quizzes.

Collection: Quiz_Generation
Document schema: One document per question (with quiz metadata) for flexible deletion and management.
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


def store_quiz_in_mongo(quiz_data: dict[str, Any]) -> list[str] | None:
    """
    Store generated quiz questions individually in MongoDB (1 document per question).

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
        List of MongoDB _ids of the inserted documents, or None if insertion failed.
    """
    try:
        client = get_mongo_client()
        db = client.get_default_database()
        collection = db["Quiz_Generation"]

        quiz_key = quiz_data.get("quiz_key", "")
        inserted_ids = []

        # Store each question as a separate document
        for question in quiz_data.get("questions", []):
            doc = {
                "quiz_key": quiz_key,  # Link questions to their quiz
                "sector": quiz_data.get("sector", ""),
                "skill": quiz_data.get("skill", ""),
                "proficiency_level": quiz_data.get("proficiency_level", ""),
                "proficiency_description": quiz_data.get("proficiency_description", ""),
                "competency": quiz_data.get("competency", ""),
                "item_type": quiz_data.get("item_type", ""),  # knowledge or ability
                "question": question.get("question", ""),
                "question_type": question.get("question_type", ""),
                "options": question.get("options", {}),
                "correct": question.get("correct", ""),
                "explanation": question.get("explanation", ""),
                "ingested_at": datetime.utcnow(),
                "deleted": False,  # Soft delete flag
            }

            result = collection.insert_one(doc)
            inserted_ids.append(str(result.inserted_id))

        return inserted_ids if inserted_ids else None

    except Exception as exc:
        print(f"Error storing quiz in MongoDB: {exc}")
        return None
    finally:
        client.close()


def get_stored_quizzes(filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """
    Retrieve stored quiz questions from MongoDB (organized by quiz_key).
    
    Returns questions grouped by quiz_key but flattened for easier browsing.

    Args:
        filters: Dictionary of filter criteria (e.g., {"sector": "Healthcare", "skill": "Patient Communication"})

    Returns:
        List of question documents matching the filters (excluded deleted questions).
    """
    try:
        client = get_mongo_client()
        db = client.get_default_database()
        collection = db["Quiz_Generation"]

        query_filters = filters or {}
        # Only return non-deleted questions (deleted must be False or not exist)
        query_filters["deleted"] = {"$ne": True}

        questions = list(collection.find(query_filters).sort("ingested_at", -1))

        # Convert ObjectId to string for JSON serialization
        for question in questions:
            if "_id" in question:
                question["_id"] = str(question["_id"])

        return questions

    except Exception as exc:
        print(f"Error retrieving quizzes from MongoDB: {exc}")
        return []
    finally:
        client.close()


def delete_question(question_id: str) -> dict[str, Any]:
    """
    Soft-delete a quiz question by marking it as deleted.

    Args:
        question_id: MongoDB _id of the question to delete

    Returns:
        Dictionary with keys:
        - "success": bool (True if deletion was successful)
        - "message": str (diagnostic message)
        - "matched": int (documents matched)
        - "modified": int (documents modified)
    """
    client = None
    try:
        from bson import ObjectId
        
        # Validate question_id format
        if not question_id or len(question_id) != 24:
            msg = f"Invalid question_id format: {question_id} (must be 24-char hex string)"
            print(f"❌ {msg}")
            return {"success": False, "message": msg, "matched": 0, "modified": 0}
        
        print(f"🔍 Attempting to delete question: {question_id}")
        client = get_mongo_client()
        db = client.get_default_database()
        collection = db["Quiz_Generation"]
        
        print(f"📊 Connected to database, checking if question exists...")

        # Convert to ObjectId
        obj_id = ObjectId(question_id)
        
        # First, check if the question exists
        existing = collection.find_one({"_id": obj_id})
        if not existing:
            msg = f"Question not found with ID: {question_id}"
            print(f"❌ {msg}")
            return {"success": False, "message": msg, "matched": 0, "modified": 0}
        
        print(f"✅ Question found. Current deleted status: {existing.get('deleted', 'NOT SET')}")
        
        # Perform soft delete
        print(f"🔄 Updating question to mark as deleted...")
        result = collection.update_one(
            {"_id": obj_id},
            {
                "$set": {
                    "deleted": True,
                    "deleted_at": datetime.utcnow(),
                }
            },
        )

        print(f"📈 Update result - matched: {result.matched_count}, modified: {result.modified_count}")
        
        # Verify the deletion by querying again
        print(f"🔍 Verifying deletion...")
        verify = collection.find_one({"_id": obj_id})
        if verify:
            deleted_status = verify.get('deleted', 'NOT SET')
            deleted_at = verify.get('deleted_at', 'NOT SET')
            print(f"✅ Current state after update - deleted: {deleted_status}, deleted_at: {deleted_at}")
            
            if deleted_status is True:
                msg = f"Question successfully marked as deleted"
                print(f"✅ {msg}")
                return {
                    "success": True,
                    "message": msg,
                    "matched": result.matched_count,
                    "modified": result.modified_count
                }
            else:
                msg = f"Update ran but deleted flag is not True. Current value: {deleted_status}"
                print(f"⚠️ {msg}")
                return {
                    "success": False,
                    "message": msg,
                    "matched": result.matched_count,
                    "modified": result.modified_count
                }
        else:
            msg = "Question disappeared after update (shouldn't happen)"
            print(f"❌ {msg}")
            return {
                "success": False,
                "message": msg,
                "matched": result.matched_count,
                "modified": result.modified_count
            }

    except Exception as exc:
        import traceback
        msg = f"Error deleting question from MongoDB: {exc}"
        print(f"❌ {msg}")
        print(f"Traceback: {traceback.format_exc()}")
        return {"success": False, "message": msg, "matched": 0, "modified": 0}
    finally:
        if client:
            try:
                client.close()
                print(f"🔒 MongoDB connection closed")
            except Exception as e:
                print(f"⚠️ Error closing client: {e}")


def get_quiz_from_mongo(
    quiz_key: str = "",
    quiz_mode: str = "competency",
    sector: str = "",
    skill: str = "",
    proficiency_level: str = "",
    competency: str = "",
) -> dict[str, Any] | None:
    """
    Retrieve a complete quiz from MongoDB filtered by hierarchy: sector → skill → proficiency_level → competency.
    
    Args:
        quiz_key: (Deprecated) The unique key identifying the quiz - kept for backwards compatibility
        quiz_mode: The quiz mode determining the filter scope:
                  - "competency": Filter by sector + skill + proficiency_level + competency
                  - "knowledge": Filter by sector + skill + proficiency_level + item_type="knowledge" (all competencies of knowledge type)
                  - "ability": Filter by sector + skill + proficiency_level + item_type="ability" (all competencies of ability type)
                  - "proficiency": Filter by sector + skill + proficiency_level (all competencies)
                  - "skill": Filter by sector + skill (all proficiency_levels & competencies)
        sector: Sector/industry name
        skill: Skill name
        proficiency_level: Proficiency level (e.g., "Level 1", "Level 2")
        competency: Competency description (e.g., "knowledge: ...", "ability: ...")
        
    Returns:
        Quiz dictionary with structure:
        {
          "quiz_key": str,
          "sector": str,
          "skill": str,
          "competency": str or null,
          "proficiency_level": str or null,
          "proficiency_description": str,
          "questions": [ {question_number, question, options, correct, explanation, question_type}, … ]
        }
        or None if not found
    """
    try:
        client = get_mongo_client()
        db = client.get_default_database()
        collection = db["Quiz_Generation"]

        # Build query filters based on hierarchy: sector → skill → proficiency → competency
        query = {"deleted": {"$ne": True}}
        
        # Always require sector and skill (the top two levels)
        if sector:
            query["sector"] = sector
        if skill:
            query["skill"] = skill

        # Apply mode-specific filters
        if quiz_mode == "competency":
            # Per Competency: sector + skill + proficiency_level + competency
            if proficiency_level:
                query["proficiency_level"] = proficiency_level
            if competency:
                query["competency"] = competency
                
        elif quiz_mode == "knowledge":
            # Knowledge Only: sector + skill + proficiency_level + item_type="knowledge" (all competencies of knowledge type)
            if proficiency_level:
                query["proficiency_level"] = proficiency_level
            query["item_type"] = "knowledge"
            # No competency filter - get all knowledge competencies at this proficiency level
            
        elif quiz_mode == "ability":
            # Ability Only: sector + skill + proficiency_level + item_type="ability" (all competencies of ability type)
            if proficiency_level:
                query["proficiency_level"] = proficiency_level
            query["item_type"] = "ability"
            # No competency filter - get all ability competencies at this proficiency level
            
        elif quiz_mode == "proficiency":
            # Per Proficiency Level: sector + skill + proficiency_level (all competencies)
            if proficiency_level:
                query["proficiency_level"] = proficiency_level
            # No competency filter - get all competencies at this proficiency level
            
        elif quiz_mode == "skill":
            # Per Skill: sector + skill (all proficiency_levels & competencies)
            # No proficiency_level or competency filter - get all for this skill
            pass
        
        # For backwards compatibility, use quiz_key if provided and no other filters
        if quiz_key and not sector and not skill:
            query["quiz_key"] = quiz_key

        # Get all matching questions
        questions = list(collection.find(query).sort("ingested_at", 1))

        if not questions:
            return None

        # Take metadata from the first question
        first_q = questions[0]
        quiz = {
            "quiz_key": first_q.get("quiz_key", ""),
            "sector": first_q.get("sector", ""),
            "skill": first_q.get("skill", ""),
            "competency": first_q.get("competency", None),
            "proficiency_level": first_q.get("proficiency_level", None),
            "proficiency_description": first_q.get("proficiency_description", ""),
            "questions": []
        }

        # Build questions list
        for i, q in enumerate(questions, 1):
            quiz["questions"].append({
                "question_number": i,
                "question": q.get("question", ""),
                "options": q.get("options", {}),
                "correct": q.get("correct", ""),
                "explanation": q.get("explanation", ""),
                "question_type": q.get("question_type", "")
            })

        return quiz

    except Exception as exc:
        print(f"Error retrieving quiz from MongoDB: {exc}")
        return None
    finally:
        client.close()

def delete_video(video_id: str) -> dict[str, Any]:
    """
    Soft-delete a video by marking it as deleted.

    Args:
        video_id: MongoDB _id of the video to delete

    Returns:
        Dictionary with keys:
        - "success": bool (True if deletion was successful)
        - "message": str (diagnostic message)
        - "matched": int (documents matched)
        - "modified": int (documents modified)
    """
    client = None
    try:
        from bson import ObjectId
        
        # Validate video_id format
        if not video_id or len(video_id) != 24:
            msg = f"Invalid video_id format: {video_id} (must be 24-char hex string)"
            print(f"❌ {msg}")
            return {"success": False, "message": msg, "matched": 0, "modified": 0}
        
        print(f"🔍 Attempting to delete video: {video_id}")
        client = get_mongo_client()
        db = client.get_default_database()
        collection = db["videos"]
        
        print(f"📊 Connected to database, checking if video exists...")

        # Convert to ObjectId
        obj_id = ObjectId(video_id)
        
        # First, check if the video exists
        existing = collection.find_one({"_id": obj_id})
        if not existing:
            msg = f"Video not found with ID: {video_id}"
            print(f"❌ {msg}")
            return {"success": False, "message": msg, "matched": 0, "modified": 0}
        
        print(f"✅ Video found. Current deleted status: {existing.get('deleted', 'NOT SET')}")
        
        # Perform soft delete
        print(f"🔄 Updating video to mark as deleted...")
        result = collection.update_one(
            {"_id": obj_id},
            {
                "$set": {
                    "deleted": True,
                    "deleted_at": datetime.utcnow(),
                }
            },
        )

        print(f"📈 Update result - matched: {result.matched_count}, modified: {result.modified_count}")
        
        # Verify the deletion by querying again
        print(f"🔍 Verifying deletion...")
        verify = collection.find_one({"_id": obj_id})
        if verify:
            deleted_status = verify.get('deleted', 'NOT SET')
            deleted_at = verify.get('deleted_at', 'NOT SET')
            print(f"✅ Current state after update - deleted: {deleted_status}, deleted_at: {deleted_at}")
            
            if deleted_status is True:
                msg = f"Video successfully marked as deleted"
                print(f"✅ {msg}")
                return {
                    "success": True,
                    "message": msg,
                    "matched": result.matched_count,
                    "modified": result.modified_count
                }
            else:
                msg = f"Update ran but deleted flag is not True. Current value: {deleted_status}"
                print(f"⚠️ {msg}")
                return {
                    "success": False,
                    "message": msg,
                    "matched": result.matched_count,
                    "modified": result.modified_count
                }
        else:
            msg = "Video disappeared after update (shouldn't happen)"
            print(f"❌ {msg}")
            return {
                "success": False,
                "message": msg,
                "matched": result.matched_count,
                "modified": result.modified_count
            }

    except Exception as exc:
        import traceback
        msg = f"Error deleting video from MongoDB: {exc}"
        print(f"❌ {msg}")
        print(f"Traceback: {traceback.format_exc()}")
        return {"success": False, "message": msg, "matched": 0, "modified": 0}
    finally:
        if client:
            try:
                client.close()
                print(f"🔒 MongoDB connection closed")
            except Exception as e:
                print(f"⚠️ Error closing client: {e}")