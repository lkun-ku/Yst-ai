from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from .models import Module, QuestionSource, QuestionType


class SessionStartIn(BaseModel):
    module: Optional[Module] = None
    knowledge_point: Optional[str] = None
    question_count: int = 10
    doc_id: Optional[int] = None  # 个人题库：从指定资料生成的题目中选题（B3 不消耗每日额度）
    mode: str = "normal"  # normal=普通闯关 / mock=模考（限时、不可回退、统一交卷）


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
    # S3 模考附加字段：server_now 供前端校正本地时钟；deadline_at 为服务端绝对截止时间
    mode: str = "normal"
    duration_sec: int = 0
    deadline_at: Optional[str] = None
    server_now: Optional[str] = None


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


# ---------- 错题本（票 09：按考点聚合） ----------
class MistakeGroupOut(BaseModel):
    knowledge_point: str
    module: Optional[Module] = None
    wrong_count: int  # 该考点累计错次（跨题累计）
    question_count: int  # 该考点错过的不同题目数
    question_ids: List[int]
    last_wrong_at: str  # ISO datetime


# ---------- 每日任务与考期倒计时（票 10） ----------
class ExamDateIn(BaseModel):
    exam_date: str  # ISO date YYYY-MM-DD


class ExamDateOut(BaseModel):
    exam_date: str
    countdown_days: int


class DailyTaskItems(BaseModel):
    mistake_review: List[dict]  # [{question_id, stem, knowledge_point, wrong_count}]
    new_questions: List[QuestionOut]


class DailyTaskOut(BaseModel):
    task_id: int
    task_date: str
    valid_hours: int = 12
    completed: bool = False
    feedback: Optional[str] = None
    deadline: Optional[str] = None  # ISO，任务失效时刻（R6 / K-02 12h 倒计时）
    items: DailyTaskItems
    # 完成进度：required=任务要求作答的总题数，done=已作答题数；can_complete=False 时前端应禁用「完成」按钮
    required_count: int = 0
    done_count: int = 0
    can_complete: bool = True


class DailyOut(BaseModel):
    exam_date: Optional[str] = None
    countdown_days: Optional[int] = None
    task: DailyTaskOut


class DailyCompleteIn(BaseModel):
    task_id: int


class DailyCompleteOut(BaseModel):
    task_id: int
    completed: bool
    feedback: str


# ---------- 历史闯关局回看（票 11 / D3） ----------
class SessionHistoryOut(BaseModel):
    session_id: int
    created_at: str
    submitted_at: Optional[str] = None
    module: Optional[Module] = None
    knowledge_point: Optional[str] = None
    question_count: int
    correct_count: int
    mastery_overview: Dict[str, float]  # 模块 -> 本局正确率（概览）


# ---------- 额度与 VIP（票 12 / Implementation 31） ----------
class QuotaOut(BaseModel):
    is_vip: bool
    free_daily_limit: int  # 20
    used_today: int
    remaining: int
    reset_rule: str  # 「每日 0 点按本地时区自然日重置」



class ReviewOut(BaseModel):
    session_id: int
    mastery: Dict[str, float]  # 五模块 -> 掌握度
    weak_points: List[str]  # 薄弱 3 考点/模块
    next_step: str  # 下一步做一件事
    paragraph: str  # AI 个性化段落（票 14 接实时生成，本票为模板占位）
    aigc_flag: bool = True


# ---------- AI 出题：资料与异步任务 ----------
class DocumentOut(BaseModel):
    id: int
    title: str
    file_type: str
    char_count: int
    page_count: int
    chunk_count: int
    status: str  # pending / parsed / failed
    created_at: str
    question_count: int = 0  # 该资料已生成的题目数


class DocChunkPreview(BaseModel):
    """资料切片预览（#27 资料查看页）：只带前 120 字，全文按需取 /api/kb/chunk/{id}。"""

    id: int
    seq: int
    heading_path: Optional[str] = None
    preview: str = ""


class DocumentFullOut(BaseModel):
    """资料**连续全文**（由切片按 seq 拼接、已消除滑窗重叠）。

    两条如实交代（前端必须显示，不能让人以为这就是原文件）：

    - 上传时的原文件按 A3 决策**不保留**（只留切片，降低版权风险），
      所以这里是**重建文本** —— 与原始文件的分段/排版可能略有差异；
      实测（上传 7980 字 → 重建 7935 字）：少掉的是**章节标题行**，
      它们在解析时被提取成 `heading_path`（「按片段」视图里显示为分组标题），正文中不再重复；
    - 超长资料按 `MAX_FULL_CHARS` 截断，`truncated=True` 表示只给了前面一段。

    `has_original_file` 为真时前端可以另外提供"看原文件"（历史资料才有）。
    """

    id: int
    title: str
    file_type: str
    char_count: int
    chunk_count: int
    text: str
    truncated: bool = False
    has_original_file: bool = False
    #: `original` = 上传时留存的**原文**；`restitched` = 存量资料，由切片按 seq **重建**
    #: （会少掉章节标题行）。前端据此决定提示语 —— 不能让用户把重建文本当成原文。
    source: str = "restitched"


class DocumentRenameIn(BaseModel):
    """资料重命名（#27 资料管理）。"""

    title: str


class DocumentDetailOut(DocumentOut):
    """含章节树与切片预览，供出题页选择范围与「查看资料」页。"""

    headings: List[str] = []
    chunks: List[DocChunkPreview] = []


class QuestionSpec(BaseModel):
    type: str  # single / multiple / judge / blank / short
    count: int


class GenerateIn(BaseModel):
    mode: str = "paper"  # paper=整卷（章节配额） / spot=定点（Top-K）
    spec: List[QuestionSpec]
    difficulty: str = "medium"  # easy / medium / hard
    scope: Optional[List[str]] = None  # 章节 heading_path 列表；None=全文档
    focus: Optional[str] = None  # 补充要求


class GenerateOut(BaseModel):
    task_id: int
    total: int


class DocTaskOut(BaseModel):
    id: int
    document_id: int
    status: str  # pending / running / done / failed
    mode: str
    done: int
    total: int
    error: Optional[str] = None
    question_count: int = 0


# ---------- S1：连胜与补签卡 ----------
class StreakOut(BaseModel):
    current: int
    max: int
    last_date: Optional[str] = None
    cards: int
    total_days: int
    can_makeup: bool = False
    makeup_date: Optional[str] = None  # 建议补签的日期（最近漏掉的一天）


class MakeupIn(BaseModel):
    date: str  # YYYY-MM-DD


class MakeupOut(BaseModel):
    ok: bool
    current: int
    cards: int
