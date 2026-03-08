from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from .models import Module, QuestionSource, QuestionType


class SessionStartIn(BaseModel):
    module: Optional[Module] = None
    knowledge_point: Optional[str] = None
    question_count: int = 10


class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    module: Module
    knowledge_point: str
    stem: str
    options: str  # JSON: [{"key","text"}]
    answer: str  # JSON: ["A", ...]
    explanation: str
    type: QuestionType
    source: QuestionSource
    aigc_flag: bool


class SessionStartOut(BaseModel):
    session_id: int
    candidate_id: int
    module: Optional[Module]
    knowledge_point: Optional[str]
    question_count: int
    questions: List[QuestionOut]
