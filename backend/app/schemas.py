from typing import Dict, List, Optional

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


# ---------- 提交判定 ----------
class AnswerIn(BaseModel):
    question_id: int
    selected: List[str]


class SubmitIn(BaseModel):
    session_id: int
    answers: List[AnswerIn]


class QuestionJudgement(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    question_id: int
    is_correct: bool
    selected: List[str]
    correct: List[str]
    state: str  # correct | wrong | partial（多选）
    options_state: Optional[Dict[str, str]] = None  # 多选四态：correct_selected/wrong_selected/missed/wrong_not_selected
    explanation: str
    knowledge_point: str
    positive_note: str


class SubmitOut(BaseModel):
    session_id: int
    idempotent: bool
    results: List[QuestionJudgement]
    mastery: Dict[str, float]  # 模块 -> 掌握度
    new_mistakes: List[int]  # 新进入错题本的题 id


class ReviewOut(BaseModel):
    session_id: int
    mastery: Dict[str, float]  # 五模块 -> 掌握度
    weak_points: List[str]  # 薄弱 3 考点/模块
    next_step: str  # 下一步做一件事
    paragraph: str  # AI 个性化段落（票 14 接实时生成，本票为模板占位）
    aigc_flag: bool = True
