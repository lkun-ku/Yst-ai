from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# 工单 14：生产 PG 的 pgvector 列类型 VECTOR(1024)（可建 HNSW 索引）；
# dev/test SQLite 退化为 LargeBinary 变体，不参与检索（检索仍走 embedding 内存路径）。
# pgvector 未安装时整体退化为 LargeBinary，保证纯 dev 环境不因缺依赖而崩。
try:
    from pgvector.sqlalchemy import Vector as _PgVector

    _EMBED_VEC_TYPE = _PgVector(1024).with_variant(LargeBinary, "sqlite")
except ImportError:  # pragma: no cover
    _EMBED_VEC_TYPE = LargeBinary


class Base(DeclarativeBase):
    pass


# ---------- 枚举 ----------
class Module(str, Enum):
    PROFESSIONAL_IDEA = "职业理念"
    PROFESSIONAL_ETHICS = "职业道德"
    EDU_LAW = "教育法律法规"
    CULTURE_LITERACY = "文化素养"
    BASIC_ABILITY = "基本能力"
    PERSONAL = "个人资料"  # 资料出题：用户上传文档生成，掌握度独立成维，不串入官方五维


# 官方五维（考纲模块）：用于题库覆盖度、掌握度雷达、复盘统计、薄弱考点排序。
# PERSONAL 是用户资料生成的独立维度，不属于官方考纲，不参与上述任何统计（A1 裁决）。
# 任何表达「官方模块」语义的地方都必须用此常量，严禁直接遍历 Module。
OFFICIAL_MODULES: tuple["Module", ...] = tuple(m for m in Module if m is not Module.PERSONAL)


class Stage(str, Enum):
    """学段。与科目共同决定「考纲 + 模块树」，是题库分类的第二维。

    三个取值即覆盖官方全部报考类别：**中职（文化课 / 专业课 / 实习指导）比照中学** ——
    科目代码同为 301/302，差别只在「考不考科目三」，那是报考规则而非学段。
    出处：`docs/用户需求文档.md` §1.1。
    """

    KINDERGARTEN = "幼儿园"
    PRIMARY = "小学"
    MIDDLE = "中学"  # 含初级中学 / 高级中学 / 中职（比照中学）


class Subject(str, Enum):
    """领域包（Domain Pack）：由「科目序号 × 学段」唯一确定的那份考纲 + 模块树。

    **旧口径（已废止）**：`COMPREHENSIVE` / `EDU_KNOWLEDGE` / `SUBJECT_KNOWLEDGE`。
    它的错误是「一个枚举值 = 一科」，而官方口径里**科目二随学段改名**：
    幼儿园《保教知识与能力》、小学《教育教学知识与能力》、中学《教育知识与能力》。
    用一个 `EDU_KNOWLEDGE` 指代三者，等于把三套不同考纲（模块数 7 / 7 / 8）压成一套。
    出处：`docs/用户需求文档.md` §1.1 / §1.2 的「纠正」，以及 §5 的 D2 / D3（🔴 致命）。

    **新口径**：枚举值 = 领域包目录名（`backend/app/domain_packs/<value>/`），
    于是 `Scope.subject` 可直接当目录名用，不必再维护一张「值 ↔ 目录」映射表。

    **成员数 = 已实现的包数，不是官方的科目数。** 官方有 6 个「科目序号 × 学段」槽位，
    本仓按 `docs/改造计划.md` §1 只实现 2 个；缺失的 4 个显式登记在 `OFFICIAL_SLOTS`，
    由 `missing_packs()` 报告 —— **「未实现」与「官方不存在」是两件不同的事**。
    """

    K1_COMPREHENSIVE = "k1_comprehensive"  # 科目一·综合素质（分卷命题 101/201/301，覆盖全学段）
    K2_MIDDLE = "k2_middle"  # 科目二·中学·教育知识与能力（302，8 模块）


@dataclass(frozen=True)
class SubjectMeta:
    """已实现领域包的官方口径元数据。"""

    no: int  # 科目序号：1 / 2 / 3
    name: str  # 官方科目名称（科目二随学段不同）
    stages: tuple[Stage, ...]  # 该科目包覆盖的学段


#: 已实现包的元数据。之所以要有这张表：科目二的名称随学段变化，
#: 若把这条规则写成各处的 if/else，就会出现「某个统计口径忘了区分学段」这类漂移 ——
#: 旧 `EDU_KNOWLEDGE` 正是这么来的。
SUBJECT_META: dict["Subject", SubjectMeta] = {
    Subject.K1_COMPREHENSIVE: SubjectMeta(
        1, "综合素质", (Stage.KINDERGARTEN, Stage.PRIMARY, Stage.MIDDLE)
    ),
    Subject.K2_MIDDLE: SubjectMeta(2, "教育知识与能力", (Stage.MIDDLE,)),
}


#: 官方试卷代码（`docs/用户需求文档.md` §1.1）。综合素质**分卷命题** ——
#: 同一科目包在不同学段对应不同代码，这正是「科目一共用一个包、但题目必须带学段标签」的依据。
PAPER_CODES: dict["Subject", dict[Stage, str]] = {
    Subject.K1_COMPREHENSIVE: {
        Stage.KINDERGARTEN: "101",
        Stage.PRIMARY: "201",
        Stage.MIDDLE: "301",
    },
    Subject.K2_MIDDLE: {Stage.MIDDLE: "302"},
}


#: 官方「科目序号 × 学段」的**全部合法槽位（§1.1 全表）**，含本仓未实现的。
#: 用途：让「官方有、本仓没有」这件事**显式可查**，避免后来者把「未实现」误读成
#: 「官方不存在」；也避免新增包时漏掉某个学段。
#: 注：音体美 A 代码（201A/202A/301A/302A）是**报考规则**，不改变槽位集合。
OFFICIAL_SLOTS: tuple[tuple[int, str, Stage], ...] = (
    (1, "综合素质", Stage.KINDERGARTEN),
    (1, "综合素质", Stage.PRIMARY),
    (1, "综合素质", Stage.MIDDLE),
    (2, "保教知识与能力", Stage.KINDERGARTEN),
    (2, "教育教学知识与能力", Stage.PRIMARY),
    (2, "教育知识与能力", Stage.MIDDLE),
)


def subject_of(no: int, stage: Stage) -> Subject | None:
    """「科目序号 × 学段」→ 已实现的领域包。

    **官方存在、本仓未实现时返回 None**（而不是抛错）—— 两者的区别由 `missing_packs()`
    表达，调用方据此给出「该科目暂未开放」而不是「不存在」。

    调用方仍须自行按报考规则判断该组合是否合法（如幼儿园不考科目三、
    中职专业课不笔试科目三）；本函数只做映射，不兼任报考规则判断。
    """
    for subject, meta in SUBJECT_META.items():
        if meta.no == no and stage in meta.stages:
            return subject
    return None


def paper_code_of(subject: Subject, stage: Stage) -> str | None:
    """科目包 × 学段 → 官方试卷代码；该组合不存在或未实现时返回 None（不猜、不兜底）。"""
    return PAPER_CODES.get(subject, {}).get(stage)


def missing_packs() -> list[tuple[int, str, Stage]]:
    """官方有、本仓未实现的「科目序号 × 学段」槽位（按 §1.1 全表比对）。"""
    implemented = {(meta.no, stage) for meta in SUBJECT_META.values() for stage in meta.stages}
    return [slot for slot in OFFICIAL_SLOTS if (slot[0], slot[2]) not in implemented]


# 官方科目全集（= 已实现包）。与 OFFICIAL_MODULES 同理：任何表达「官方科目」语义的地方
# 都必须用它，不要直接遍历 Subject（后续若引入「个人资料」等非官方科目，遍历会把它卷进来）。
OFFICIAL_SUBJECTS: tuple["Subject", ...] = tuple(Subject)


# 新增枚举一律 native_enum=False：
#   - PG 上若用原生 ENUM，迁移里要额外处理 CREATE TYPE / ALTER TYPE（见实施要点）；
#   - native_enum=False 渲染为 VARCHAR + CHECK，SQLite 与 PG 行为一致，零方言分支。
# 注意：SAEnum 落库的是**枚举成员名**（如 "K1_COMPREHENSIVE"）而非值——
# 已实测确认（Module 亦如此）。故手写 SQL 过滤时必须用成员名，不能用中文名或包名。
# 本次 `Subject` 的成员名与值一起改了口径，存量行仍是旧成员名（"COMPREHENSIVE" 等），
# 按决策「清空存量、全量重建」由迁移 `domain_model_fix` 直接删行，不做值回写
# （旧 `EDU_KNOWLEDGE` 覆盖三学段，无法无歧义地回写；且 8b 后非中学科目二已无对应包）。
SUBJECT_COL = SAEnum(Subject, native_enum=False, length=32, validate_strings=True)
STAGE_COL = SAEnum(Stage, native_enum=False, length=32, validate_strings=True)


class QuestionType(str, Enum):
    """题型（官方题型表见 `docs/用户需求文档.md` §1.5，此处按本期范围裁剪）。

    **注意本枚举的落库方式与 `Subject` 不同**：`Question` 上用的是 `SAEnum(QuestionType)`
    （未加 `native_enum=False`），在 PG 上是**原生 ENUM 类型** —— 新增成员需要
    `ALTER TYPE questiontype ADD VALUE`；而 `Subject` 是 VARCHAR + CHECK（重建约束即可）。
    迁移 `domain_model_fix` 必须分方言走这两条不同路径。
    """

    # ---- 客观题（科目一 39% 卷面）----
    SINGLE = "single"
    MULTIPLE = "multiple"
    JUDGE = "judge"      # 判断题（复用选项结构，答案为 正确/错误）
    BLANK = "blank"      # 填空题（answer 为可接受答案文本列表）
    SHORT = "short"      # 简答/名词解释（answer 为参考答案，不自动判分）

    # ---- 主观题（科目一 61% 卷面）----
    # 只加三型：材料分析 + 写作覆盖科目一 61% 卷面；教学设计服务于科目二 / 三。
    # §1.5 的其余题型（论述 / 辨析 / 解答 / 课例点评 / 诊断 / 活动设计）留到真正要批改时再加，
    # 避免先长出一批「有题型、无 rubric」的空壳。
    MATERIAL = "material"  # 材料（案例）分析 —— 科目一 42 分
    WRITING = "writing"    # 写作 —— 科目一 50 分
    DESIGN = "design"      # 教学设计 —— 科目二 / 科目三


class QuestionSource(str, Enum):
    POOL = "pool"
    REALTIME = "realtime"
    DOC = "doc"  # 用户文档生成（个人题）


# ---------- 题目来源口径（questions.source_kind，P1）----------
# 用字符串常量而非 Enum：口径会持续增加（P2 还会补真题改编的其他细分），
# 每加一个值都要改 PG 的 ENUM 类型，迁移成本远高于收益。
SOURCE_KIND_AI_VARIANT = "ai_variant"  # AI 变式题（官方池主力）
SOURCE_KIND_OFFICIAL_PAST = "official_past"  # 真题考点改编（只存考点与命题角度，不存真题原文）
SOURCE_KIND_SEED_TEMPLATE = "seed_template"  # 占位模板题：无学科内容，仅供本地演示与测试夹具
SOURCE_KIND_DOC_UPLOAD = "doc_upload"  # 用户上传资料生成（个人题）


class ProofreadStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    REJECTED = "rejected"


class SessionStatus(str, Enum):
    STARTED = "started"
    SUBMITTED = "submitted"


class ReportStatus(str, Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    REJECTED = "rejected"


# ---------- 表 ----------
class Candidate(Base):
    """考生。身份主键为 unionid（ADR-0001 / Implementation 25）。"""

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    unionid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    aigc_notice_acked: Mapped[bool] = mapped_column(Boolean, default=False)
    exam_date: Mapped[str | None] = mapped_column(String(32), nullable=True)  # ISO date，考期为锚（票 10）
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False)  # VIP 状态（票 12，内测占位无真实支付）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class KnowledgePoint(Base):
    """知识点树（自关联，P1）。

    为什么建表而不继续沿用 `questions.knowledge_point` 字符串：

    1. 字符串是**冗余副本**——改一个字就断链（题目指向的考点再也不匹配任何骨架）；
    2. 字符串**没有层级**——而「这个模块下哪个知识点 0 题」正是覆盖度度量的核心问题，
       没有层级就答不出来，也无法把掌握度从模块粒度下沉到知识点粒度。

    `level` 存的是**距根深度**（1=顶层模块）而非固定的「1/2/3」语义：
    当前科目一为 2 级（模块 → 知识点）；引入官方考纲语料后（P2）在中间插入「章节」层，
    知识点自然降为 3 级——用深度表示则插入层级时无需改表、无需改判定逻辑。
    """

    __tablename__ = "knowledge_points"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 稳定业务键：`{领域包}/{模块}/{知识点}` 路径（如 "k1_comprehensive/职业理念/教育观"）。
    # 种子幂等与环境对齐都以它为准——不能用 (subject, stage, parent_id, name) 做幂等键：
    # stage / parent_id 可为 NULL，而 SQL 唯一约束不约束 NULL，重复行会悄悄进来。
    code: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    subject: Mapped[Subject] = mapped_column(SUBJECT_COL, index=True)
    # NULL = 三学段通用。综合素质三学段考纲大体一致，P2 拿到分学段考纲后再细化填充。
    stage: Mapped[Stage | None] = mapped_column(STAGE_COL, nullable=True, index=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_points.id"), nullable=True, index=True
    )
    level: Mapped[int] = mapped_column(Integer, default=1, index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    # 真题频次（命题权重）。由 P2 的真题考点分布统计回填；0 表示未知、不参与加权。
    exam_frequency: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Question(Base):
    """题目实体（变式题）。携带考点归属、来源、审校状态、AIGC 标识、版本号（Implementation 21）。"""

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    module: Mapped[Module] = mapped_column(SAEnum(Module), index=True)
    knowledge_point: Mapped[str] = mapped_column(String(128), index=True)
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[str] = mapped_column(Text)  # JSON: [{"key","text"}]
    answer: Mapped[str] = mapped_column(Text)  # JSON: ["A", ...] / 填空可接受答案列表 / 简答参考答案
    explanation: Mapped[str] = mapped_column(Text)
    type: Mapped[QuestionType] = mapped_column(SAEnum(QuestionType), default=QuestionType.SINGLE)
    source: Mapped[QuestionSource] = mapped_column(SAEnum(QuestionSource), default=QuestionSource.POOL)
    proofread_status: Mapped[ProofreadStatus] = mapped_column(
        SAEnum(ProofreadStatus), default=ProofreadStatus.PENDING
    )
    aigc_flag: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    owner_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidates.id"), nullable=True, index=True
    )  # NULL=官方池；非 NULL=个人题归属上传者
    doc_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id"), nullable=True, index=True
    )  # 来源文档（个人题）
    # 溯源：生成该题目所依据的资料切片片段（跨文档出题用，便于回溯到原文）
    source_chunk: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------- P1：多维分类（科目 / 学段 / 知识点树 / 难度 / 来源）----------
    # 全部可空：存量 418 题与新列并存，迁移不回填也不用停机（新增列一律可空是本仓约定）。
    subject: Mapped[Subject | None] = mapped_column(SUBJECT_COL, nullable=True, index=True)
    # NULL = 三学段通用（见 KnowledgePoint.stage 的说明）
    stage: Mapped[Stage | None] = mapped_column(STAGE_COL, nullable=True, index=True)
    # easy / medium / hard。此前 real_questions.json 里本就有 difficulty，但入库时被丢弃
    # （表没有这一列），导致自适应组卷（弱项出 easy、掌握后上 hard）没有任何依据。
    difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # ai_variant / official_past / seed_template / doc_upload。
    # 用 String 而非 Enum：来源是会持续增加的口径（P2 还会加 official_past），
    # 每次加值都要改 PG 的 ENUM 类型，成本远高于收益。
    source_kind: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # 知识点树外键。P1 起为**权威归属**；`knowledge_point` 字符串保留作向后兼容与展示，
    # 二者由回填脚本保持一致。
    # **外键显式命名**：跨 SQLite/PG 的 batch 迁移在加带外键的列时要求约束有名字
    # （否则 `Constraint must have a name`），且命名后「新库 create_all」与
    # 「老库 alembic 迁移」两条路径产出的约束名一致，不会出现 schema 漂移。
    kp_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_points.id", name="fk_questions_kp_id"), nullable=True, index=True
    )

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
    # S3 模考：normal=普通闯关 / mock=模考（限时、不可回退、统一交卷）
    mode: Mapped[str] = mapped_column(String(16), default="normal")
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)  # 0 表示不限时
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeout: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否超时交卷


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
    # JSON: 已作答的题目 id 列表。用于校验"任务是否真的完成"，
    # 避免未做任何题目就直接点完成（逻辑漏洞）。
    done_ids: Mapped[str] = mapped_column(Text, default="[]")
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_hours: Mapped[int] = mapped_column(Integer, default=12)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RealtimeUsage(Base):
    """实时生成用量（票 14 / Implementation 14）。每人每日上限 3 次，超限降级纯池化。"""

    __tablename__ = "realtime_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # 本地日期 YYYY-MM-DD
    count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("candidate_id", "day", name="uq_realtime_candidate_day"),
    )


class ErrorReport(Base):
    """题目纠错报告（票 13 / 用户故事 #21-#22）。进入审校队列由最小后台处理。"""

    __tablename__ = "error_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), index=True)
    error_type: Mapped[str] = mapped_column(String(32))  # answer | explanation | knowledge_point
    detail: Mapped[str] = mapped_column(Text)
    status: Mapped[ReportStatus] = mapped_column(SAEnum(ReportStatus), default=ReportStatus.PENDING)
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


class Document(Base):
    """用户上传的资料（文档出题模块）。解析后落纯文本与切片，归属上传者本人。"""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(16))  # pdf / docx / txt / md
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/parsed/failed
    storage_path: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DocumentChunk(Base):
    """资料切片。

    采用「RAG 检索 + 分批生成」：切片经 embedding 后用于文档级语义检索（单文档约 200 chunk，
    内存余弦即可，不引向量数据库）；二期若做跨文档检索，可直接用本表 embedding 增量建索引。
    """

    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    # 结构路径（如 "第三章 > 3.2"）。PDF 无可靠标题结构时为 None，此情形靠向量检索兜底。
    heading_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 工单 14 + 20/W-5：单一向量列，按方言自动选择存储形态：
    #   PostgreSQL → VECTOR(1024)（可建 HNSW 索引、走 SQL 余弦检索）
    #   SQLite/dev → LargeBinary（float32 字节，走内存 numpy 混合检索）
    # 早期曾拆成 embedding(bytea) + embedding_vec(vector) 两列，W-5 合并回一列并去掉双写。
    embedding: Mapped[list[float] | None] = mapped_column(
        _EMBED_VEC_TYPE, nullable=True
    )
    # pending / ok / failed —— embedding 失败不得阻塞上传，降级走关键词检索
    embed_status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DocTask(Base):
    """文档出题异步任务。后台线程执行，前端轮询进度（done/total/status）。"""

    __tablename__ = "doc_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/running/done/failed
    # #26：协作式取消——置 True 后任务线程在**批次边界**停止；已生成的题目保留为部分卷
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    # paper=整卷（章节配额分配，覆盖均匀） / spot=定点（Top-K 语义检索，精准命中）
    mode: Mapped[str] = mapped_column(String(16), default="paper")
    spec: Mapped[str] = mapped_column(Text, default="[]")  # JSON: [{"type","count"}]
    difficulty: Mapped[str] = mapped_column(String(16), default="medium")  # easy/medium/hard
    # JSON: 知识点范围（章节 heading_path 列表）；None=全文档
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    focus: Mapped[str | None] = mapped_column(Text, nullable=True)  # 用户补充的侧重说明
    done: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_question_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON: [id,...]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class KbTask(Base):
    """知识库出题异步任务（跨文档范围出题）。

    独立于 doc_tasks：后者绑定单文档（document_id 非空外键），本表仅按 candidate_id
    跨其个人资料库出题，进度/结果持久化到库（替代早期模块级内存字典 _tasks，进程重启不丢）。
    """

    __tablename__ = "kb_tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/running/done/failed
    done: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_question_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON: [id,...]
    # #25：协作式取消——置 True 后任务线程在批次边界停止，**已生成的题目保留**（D3）
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    # 原始请求参数 JSON（scope/spec/difficulty/focus/route），供失败后「仅重试缺口」使用（D5）
    request_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class KbTaskEvent(Base):
    """出题过程事件（#25 生题流式展示）。

    为什么用独立表而不是 `KbTask` 上的 JSON 列（D2）：
    - 写入：append 单行即可；JSON 列是「读整列→改→写整列」，事件多时 O(n²) 放大
    - 查询：`WHERE task_id=? AND seq>?` 走索引，天然支持 `since` 增量拉取
    - 清理：可按 task_id 单独删除，不必重写整列

    `seq` 为全局自增主键，单调递增，兼作前端增量游标。
    """

    __tablename__ = "kb_task_events"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("kb_tasks.task_id"), index=True)
    # stage/retrieve/grade/rewrite/batch/selfcheck/question/done/failed/cancelled
    type: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)  # 时间线一句话
    # 可展开内容：切片摘要 / 题数据 JSON。按 D1 只存摘要，全文由 /api/kb/chunk/{id} 按需取
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DocTaskEvent(Base):
    """文档出题（路线①）过程事件，与 KbTaskEvent 同构（#26 首批）。

    为什么独立建表而不复用 `kb_task_events`：后者外键挂在 `kb_tasks` 上，
    而「按资料出题」用的是 `doc_tasks`（整型的 task.id），无法写入同一张表。
    """

    __tablename__ = "doc_task_events"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("doc_tasks.id"), index=True)
    # stage/retrieve/batch/selfcheck/question/done/failed/cancelled
    type: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)  # 时间线一句话
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Streak(Base):
    """S1：连续完成每日任务的自然日数（强留存机制）。"""

    __tablename__ = "streaks"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), unique=True, index=True)
    current: Mapped[int] = mapped_column(Integer, default=0)  # 当前连胜
    max: Mapped[int] = mapped_column(Integer, default=0)  # 历史最高
    last_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # 最近完成日 YYYY-MM-DD
    cards: Mapped[int] = mapped_column(Integer, default=0)  # 补签卡余量，上限 MAX_MAKEUP_CARDS
    total_days: Mapped[int] = mapped_column(Integer, default=0)  # 累计达标天数
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class StreakMakeup(Base):
    """S1：补签流水。唯一约束保证同一天不可重复补（幂等）。"""

    __tablename__ = "streak_makeups"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    missed_date: Mapped[str] = mapped_column(String(10))  # 被补的那一天
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (UniqueConstraint("candidate_id", "missed_date", name="uq_streak_makeup"),)


# ---------- AI 模拟答（聊天式问答训练，#31；与选择题闯关 Session 完全独立） ----------
class ChatSession(Base):
    """AI 模拟答训练场（#31）。一场多题多轮追问；不与连胜/今日任务联动（用户决策）。"""

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    module: Mapped[Module] = mapped_column(SAEnum(Module))
    difficulty: Mapped[str] = mapped_column(String(16), default="medium")  # medium | hard
    persona: Mapped[str] = mapped_column(String(16), default="coach")  # coach | examiner
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | finished
    question_count: Mapped[int] = mapped_column(Integer, default=0)  # 已作答题数
    score_avg: Mapped[float] = mapped_column(Float, default=0.0)  # 终评均分
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ChatTurn(Base):
    """对话回合（#31）：ask=AI提问 probe=追问 feedback=终评 user=用户回答。"""

    __tablename__ = "chat_turns"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # 会话内自增序号
    role: Mapped[str] = mapped_column(String(8))  # ai | user
    turn_type: Mapped[str] = mapped_column(String(16))  # opening | ask | probe | feedback | user
    content: Mapped[str] = mapped_column(Text)
    question_id: Mapped[int | None] = mapped_column(ForeignKey("questions.id"), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # feedback 轮 0-100
    points_hit: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 数组
    points_missed: Mapped[str | None] = mapped_column(Text, nullable=True)
    points_wrong: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)  # 改进建议
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
