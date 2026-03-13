from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ---------- 枚举 ----------
class Module(str, Enum):
    PROFESSIONAL_IDEA = "职业理念"
    PROFESSIONAL_ETHICS = "职业道德"
    EDU_LAW = "教育法律法规"
    CULTURE_LITERACY = "文化素养"
    BASIC_ABILITY = "基本能力"


class QuestionType(str, Enum):
    SINGLE = "single"
    MULTIPLE = "multiple"


class QuestionSource(str, Enum):
    POOL = "pool"
    REALTIME = "realtime"


class ProofreadStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    REJECTED = "rejected"


class SessionStatus(str, Enum):
    STARTED = "started"
    SUBMITTED = "submitted"


# ---------- 表 ----------
class Candidate(Base):
    """考生。身份主键为 unionid（ADR-0001 / Implementation 25）。"""

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    unionid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    aigc_notice_acked: Mapped[bool] = mapped_column(Boolean, default=False)
    exam_date: Mapped[str | None] = mapped_column(String(32), nullable=True)  # ISO date，考期为锚（票 10）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Question(Base):
    """题目实体（变式题）。携带考点归属、来源、审校状态、AIGC 标识、版本号（Implementation 21）。"""

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    module: Mapped[Module] = mapped_column(SAEnum(Module), index=True)
    knowledge_point: Mapped[str] = mapped_column(String(128), index=True)
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[str] = mapped_column(Text)  # JSON: [{"key","text"}]
    answer: Mapped[str] = mapped_column(String(64))  # JSON: ["A", ...]
    explanation: Mapped[str] = mapped_column(Text)
    type: Mapped[QuestionType] = mapped_column(SAEnum(QuestionType), default=QuestionType.SINGLE)
    source: Mapped[QuestionSource] = mapped_column(SAEnum(QuestionSource), default=QuestionSource.POOL)
    proofread_status: Mapped[ProofreadStatus] = mapped_column(
        SAEnum(ProofreadStatus), default=ProofreadStatus.PENDING
    )
    aigc_flag: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Session(Base):
    """闯关局。含题目序列与提交状态，用于续答、幂等、历史回看（Implementation 26）。"""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    module: Mapped[Module | None] = mapped_column(SAEnum(Module), nullable=True)
    knowledge_point: Mapped[str | None] = mapped_column(String(128), nullable=True)
    question_count: Mapped[int] = mapped_column(Integer, default=10)
    status: Mapped[SessionStatus] = mapped_column(SAEnum(SessionStatus), default=SessionStatus.STARTED)
    question_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON: [id,...]
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 提交结果（幂等缓存）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MistakeBook(Base):
    """错题本。按考点聚合（Implementation 8 / 用户故事 #32）。"""

    __tablename__ = "mistake_book"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), index=True)
    knowledge_point: Mapped[str] = mapped_column(String(128), index=True)
    wrong_count: Mapped[int] = mapped_column(Integer, default=1)
    last_wrong_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("candidate_id", "question_id", name="uq_mistake_candidate_question"),
    )


class DailyTask(Base):
    """每日任务。以考期为锚，当日有效时限（Implementation 7 / 用户故事 #35-#37）。"""

    __tablename__ = "daily_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    exam_date: Mapped[str | None] = mapped_column(String(32), nullable=True)  # ISO date
    task_date: Mapped[str] = mapped_column(String(32), index=True)  # 本地日期 YYYY-MM-DD
    items: Mapped[str] = mapped_column(Text, default="[]")  # JSON: 任务项引用
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_hours: Mapped[int] = mapped_column(Integer, default=12)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Mastery(Base):
    """掌握度。按模块维度存储并随答题历史更新（Implementation 27）。"""

    __tablename__ = "mastery"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    module: Mapped[Module] = mapped_column(SAEnum(Module), index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("candidate_id", "module", name="uq_mastery_candidate_module"),
    )
