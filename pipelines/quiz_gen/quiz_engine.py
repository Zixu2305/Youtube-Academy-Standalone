"""
quiz_engine.py
--------------
LLM-backed MCQ generator using Ollama (Llama 3.2 3B).

Five question types per quiz:
  Q1  Conceptual     â€“ understanding a core concept from the competency
  Q2  Application    â€“ applying an ability in a real work task
  Q3  Scenario-Based â€“ workplace scenario tied to the proficiency requirement
  Q4  Technical      â€“ specific technical knowledge or method
  Q5  Evaluation     â€“ choosing the best approach to meet the proficiency

Priority context: competency item + proficiency requirement/description.
Determinism is enforced by the persistent JSON cache (generate-once).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Ollama configuration
# ---------------------------------------------------------------------------

_OLLAMA_MODEL = "llama3.2:3b"
_OLLAMA_TIMEOUT = 120  # seconds â€” 3B model is slow on CPU


def _ollama_host() -> str:
    return os.getenv("OLLAMA_HOST", "http://ollama:11434")


def _call_ollama(prompt: str) -> dict[str, Any]:
    """
    POST to Ollama generate endpoint with JSON format enforced.
    Returns the parsed response dict.
    Raises RuntimeError on connectivity or parse failures.
    """
    url = f"{_ollama_host()}/api/generate"
    payload = {
        "model": _OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.0,
            "seed": 42,
            "num_predict": 600,
        },
    }
    try:
        resp = requests.post(url, json=payload, timeout=_OLLAMA_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json().get("response", "")
    except requests.RequestException as exc:
        raise RuntimeError(f"Ollama request failed: {exc}") from exc

    # Strip markdown code fences the model sometimes adds
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Last resort: grab first {...} block
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        raise RuntimeError(f"Could not parse Ollama JSON: {raw[:300]}")


def _validate_mcq(data: dict[str, Any]) -> dict[str, Any]:
    """Raise ValueError if required MCQ fields are missing or malformed."""
    for field in ("question", "options", "correct", "explanation"):
        if field not in data:
            raise ValueError(f"LLM response missing field: '{field}'")
    opts = data["options"]
    if not isinstance(opts, dict) or not all(k in opts for k in ("A", "B", "C", "D")):
        raise ValueError("options must be a dict with keys A, B, C, D")
    if data["correct"] not in opts:
        raise ValueError(f"correct key '{data['correct']}' not in options")
    return data


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

_JSON_SCHEMA = """\
Respond ONLY with a single valid JSON object â€” no extra text, no markdown fences:
{
  "question": "<question text>",
  "options": {"A": "...", "B": "...", "C": "...", "D": "..."},
  "correct": "<A|B|C|D>",
  "explanation": "<one sentence explaining the correct answer>"
}
Rules:
- All 4 options must be distinct and plausible; only one is correct
- The question and options must be directly relevant to the Core Competency and Proficiency Requirement
- Keep each option concise (under 80 words)
- Do not use phrases like "All of the above" or "None of the above"\
"""


def _ctx_vars(ctx: dict[str, Any]) -> dict[str, str]:
    """Pull the most relevant fields for prompt construction."""
    knowledge_items = ctx.get("knowledge_items", [])
    ability_items   = ctx.get("ability_items", [])
    item_text       = ctx.get("item_text", "")

    # Use the selected competency item text; fall back to first knowledge/ability item
    competency_item = (
        item_text
        or (knowledge_items[0] if knowledge_items else "")
        or (ability_items[0]   if ability_items   else "")
    )

    # Use proficiency_description as the "requirement"; fall back to raw proficiency_level
    requirement = (
        ctx.get("proficiency_description", "")
        or ctx.get("requirement", "")
        or ctx.get("proficiency_level", "")
    )

    return {
        "skill":             ctx.get("skill", ""),
        "sector":            ctx.get("sector", ""),
        "proficiency_level": ctx.get("proficiency_level", ""),
        "competency_item":   competency_item[:350],
        "requirement":       requirement[:350],
    }


def _base_context(ctx: dict[str, Any]) -> str:
    v = _ctx_vars(ctx)
    return (
        f"Skill: {v['skill']}\n"
        f"Sector: {v['sector']}\n"
        f"Proficiency Level: {v['proficiency_level']}\n"
        f"Core Competency: {v['competency_item']}\n"
        f"Proficiency Requirement: {v['requirement']}\n\n"
    )


# ---------------------------------------------------------------------------
# Question type prompt builders
# ---------------------------------------------------------------------------

def _prompt_conceptual(ctx: dict[str, Any]) -> str:
    return (
        "You are an expert quiz designer for workplace skills assessments.\n"
        "Create a CONCEPTUAL multiple-choice question that tests whether the learner "
        "understands the meaning or importance of the core competency in the workplace context.\n\n"
        + _base_context(ctx)
        + _JSON_SCHEMA
    )


def _prompt_application(ctx: dict[str, Any]) -> str:
    return (
        "You are an expert quiz designer for workplace skills assessments.\n"
        "Create an APPLICATION multiple-choice question. Present a concrete work task or "
        "responsibility and ask which action best demonstrates the required competency.\n\n"
        + _base_context(ctx)
        + _JSON_SCHEMA
    )


def _prompt_scenario(ctx: dict[str, Any]) -> str:
    return (
        "You are an expert quiz designer for workplace skills assessments.\n"
        "Create a SCENARIO-BASED multiple-choice question. Describe a specific realistic "
        "workplace situation and ask what the professional should do to satisfy the "
        "proficiency requirement.\n\n"
        + _base_context(ctx)
        + _JSON_SCHEMA
    )


def _prompt_technical(ctx: dict[str, Any]) -> str:
    return (
        "You are an expert quiz designer for workplace skills assessments.\n"
        "Create a TECHNICAL multiple-choice question that tests knowledge of specific "
        "methods, tools, standards, or procedures directly related to the core competency.\n\n"
        + _base_context(ctx)
        + _JSON_SCHEMA
    )


def _prompt_evaluation(ctx: dict[str, Any]) -> str:
    return (
        "You are an expert quiz designer for workplace skills assessments.\n"
        "Create an EVALUATION multiple-choice question. Present several different approaches "
        "or decisions and ask the learner to choose which one BEST satisfies the proficiency "
        "requirement for this skill and level.\n\n"
        + _base_context(ctx)
        + _JSON_SCHEMA
    )


# ---------------------------------------------------------------------------
# Question type registry
# ---------------------------------------------------------------------------

_QUESTION_TYPES: list[tuple[str, Any]] = [
    ("Conceptual",     _prompt_conceptual),
    ("Application",    _prompt_application),
    ("Scenario-Based", _prompt_scenario),
    ("Technical",      _prompt_technical),
    ("Evaluation",     _prompt_evaluation),
]


# ---------------------------------------------------------------------------
# Individual question builder (with retry)
# ---------------------------------------------------------------------------

def _build_question(
    q_num: int,
    q_type: str,
    prompt_fn,
    ctx: dict[str, Any],
    retries: int = 2,
) -> dict[str, Any]:
    """
    Call Ollama, validate the response, return the question dict.
    Retries up to `retries` times before raising.
    """
    last_exc: Exception = RuntimeError("unknown error")

    for attempt in range(retries):
        try:
            raw = _call_ollama(prompt_fn(ctx))
            validated = _validate_mcq(raw)
            return {
                "question_number": q_num,
                "question_type":   q_type,
                "question":        validated["question"],
                "options":         validated["options"],
                "correct":         validated["correct"],
                "explanation":     validated["explanation"],
            }
        except Exception as exc:
            last_exc = exc

    raise RuntimeError(
        f"Q{q_num} ({q_type}) failed after {retries} attempts: {last_exc}"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_quiz(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Generate exactly 5 MCQ questions using Ollama (Llama 3.2 3B).
    Returns a list — all 5 questions are generated before returning.
    Useful for serving from cache.
    """
    return list(generate_quiz_stream(ctx))


def generate_quiz_stream(ctx: dict[str, Any]):
    """
    Generator version of generate_quiz.
    Yields each question dict as soon as Ollama returns it.
    Use this for streaming responses so the browser doesn't time out.
    """
    for idx, (q_type, prompt_fn) in enumerate(_QUESTION_TYPES, start=1):
        q = _build_question(idx, q_type, prompt_fn, ctx)
        yield q
