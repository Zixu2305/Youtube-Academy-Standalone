# pipelines/quiz_gen
# Rule-based MCQ quiz generation for the SkillsFuture selection pathway.
#
# Public API surface:
#   from pipelines.quiz_gen import get_or_create_quiz
#   from pipelines.quiz_gen import make_quiz_key
from .quiz_store import get_or_create_quiz, make_quiz_key

__all__ = ["get_or_create_quiz", "make_quiz_key"]
