"""domain_model_fix

领域模型纠偏：把 `Subject` 从「一个枚举值 = 一科」改为「科目序号 × 学段 → 领域包（Domain Pack）」。

修正的是 `docs/用户需求文档.md` §5 里两条 🔴 致命偏差：

- **D2**：综合素质**分学段命题**（101 / 201 / 301），不是「各学段通考」；
- **D3**：科目二**名称随学段变化**（保教 / 教育教学 / 教育知识）——
  单一 `EDU_KNOWLEDGE` 只适用于中学，是**错误数据**。

---

## ⚠️ 本迁移会删除数据（不可恢复）

按决策 `docs/改造计划.md` §6「Q11 = 甲（回滚重做）」：**清空存量题目与知识点树，全量重建**。

**为什么必须在迁移里清，而不能留给手工脚本**：

`subject` / `stage` 由 `SAEnum(native_enum=False, validate_strings=True)` 渲染，
实测 `create_constraint=False` → 落库是 **VARCHAR(32)，数据库侧没有任何 CHECK 约束**。
也就是说旧成员名（`COMPREHENSIVE` / `EDU_KNOWLEDGE` / `SUBJECT_KNOWLEDGE`）能**安然躺在列里**，
直到**读**的时候才由 Python 侧抛 `LookupError` —— 典型的「延迟爆雷」，排查成本极高。
清空是让 schema 与数据同时对齐、且不留下「能存不能读」状态的最小代价做法。

同理，旧 `KnowledgePoint.code` 的前缀是**中文科目名**（`综合素质/职业理念/教育观`），
与新口径「枚举值即目录名」（`k1_comprehensive/...`）不一致，一并清空由种子重建。

**清空范围**（外键与 JSON 引用都逐一处理，避免悬空引用）：

| 对象 | 处置 | 原因 |
| --- | --- | --- |
| `mistake_book` | `DELETE` | 外键 → `questions.id`（NOT NULL） |
| `error_reports` | `DELETE` | 外键 → `questions.id`（NOT NULL） |
| `chat_turns.question_id` | 置 NULL | 可空；保留对话记录本身 |
| `questions` | `DELETE` | 本迁移的主体 |
| `knowledge_points` | `DELETE` | 由种子重建（`scripts/seed_knowledge_tree.py`） |
| `sessions.question_ids` / `sessions.result_json` | 重置为 `'[]'` / NULL | JSON 里存的是题目 id 与提交结果 |
| `doc_tasks.generated_question_ids` / `kb_tasks.generated_question_ids` | 重置为 `'[]'` | 同上 |

**升级后必须显式执行**（两步都幂等）：

    python scripts/seed_knowledge_tree.py      # 重建知识点树
    （题库需重新出题或导入：python scripts/import_real_bank.py）

---

## 另一条路径：PG 原生 ENUM

`Question.type` 用的是 `SAEnum(QuestionType)`（**未**加 `native_enum=False`），
PG 上落成**原生 ENUM 类型 `questiontype`**。新增的三个题型成员必须
`ALTER TYPE questiontype ADD VALUE`；而 SQLite 上该列是 VARCHAR，无需任何 DDL。

**两类枚举的迁移路径不同，必须分方言处理**：
- `Subject` / `Stage` → VARCHAR，**无需 DDL**（也因此才必须清数据，见上）；
- `QuestionType` → PG 原生 ENUM，`ADD VALUE`。

PG 12+ 允许 `ADD VALUE` 在事务内执行，但**新值要到事务提交后才能被使用** ——
本迁移只新增、不使用，安全。仓库已验证的 TencentDB PostgreSQL 18.x 满足该前提。

`downgrade()` 只能回滚 schema 侧，**被删除的数据无法恢复**，且 PG 不支持删除 ENUM 成员 ——
这是一次**单向**迁移。
"""
from alembic import op
import sqlalchemy as sa


revision = 'c9d0e1f2a3b4'
down_revision = 'b7f1c2d3e4a5'
branch_labels = None
depends_on = None


#: 本次新增的题型成员（`QuestionType` 的成员名）。PG 原生 ENUM 需逐条 ADD VALUE。
_NEW_QUESTION_TYPES = ('MATERIAL', 'WRITING', 'DESIGN')

#: 原生的题型 ENUM 类型名（SQLAlchemy 默认以枚举类名小写命名）。
_QUESTION_TYPE_ENUM = 'questiontype'


def _has_table(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _exec(bind, sql: str) -> None:
    bind.execute(sa.text(sql))


def _enum_values(bind, type_name: str) -> set:
    """读取 PG 原生 ENUM 的现有取值；非 PG 或类型不存在时返回空集。"""
    if bind.dialect.name != 'postgresql':
        return set()
    rows = bind.execute(
        sa.text(
            "SELECT e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid "
            "WHERE t.typname = :n"
        ),
        {"n": type_name},
    ).fetchall()
    return {row[0] for row in rows}


def _clear_rows(bind, table: str) -> None:
    if _has_table(bind, table):
        _exec(bind, f'DELETE FROM {table}')


def _reset_json_ids(bind, table: str, column: str, extra: str = '') -> None:
    if _has_table(bind, table):
        _exec(bind, f"UPDATE {table} SET {column} = '[]'{extra}")


def upgrade() -> None:
    bind = op.get_bind()

    # ---------- 1) 清空存量：先删引用方，再删被引用方（顺序受外键约束）----------
    # mistake_book / error_reports 的 question_id 是 NOT NULL，只能整行删。
    _clear_rows(bind, 'mistake_book')
    _clear_rows(bind, 'error_reports')

    # chat_turns.question_id 可空 —— 置 NULL 保留对话记录本身，比整行删更保守。
    if _has_table(bind, 'chat_turns'):
        _exec(bind, 'UPDATE chat_turns SET question_id = NULL WHERE question_id IS NOT NULL')

    _clear_rows(bind, 'questions')
    # knowledge_points 有自关联 parent_id：单条 DELETE 会删掉全部行，
    # 外键在语句结束时校验，父子同时消失，不会报错。
    _clear_rows(bind, 'knowledge_points')

    # ---------- 2) 清掉 JSON 里残留的题目 id ----------
    _reset_json_ids(bind, 'sessions', 'question_ids', extra=', result_json = NULL')
    _reset_json_ids(bind, 'doc_tasks', 'generated_question_ids')
    _reset_json_ids(bind, 'kb_tasks', 'generated_question_ids')

    # ---------- 3) PG：原生 ENUM 追加题型成员 ----------
    existing = _enum_values(bind, _QUESTION_TYPE_ENUM)
    for value in _NEW_QUESTION_TYPES:
        # `existing` 为空 = 非 PG 或类型不存在（如 SQLite 的 VARCHAR 列），无需 DDL。
        if existing and value not in existing:
            # 值来自模块常量元组，无外部输入，不构成注入面。
            op.execute(f"ALTER TYPE {_QUESTION_TYPE_ENUM} ADD VALUE '{value}'")


def downgrade() -> None:
    """仅回滚 schema 侧；**被删除的数据无法恢复**。

    无具体动作，理由：
    - `subject` / `stage` 是 VARCHAR 且无 CHECK 约束 → 本来就没有 DDL 可回滚；
    - PG 不支持删除 ENUM 成员（`ALTER TYPE ... DROP VALUE` 不存在）；
    - 删除的行没有备份，无法恢复。

    保留空实现而非 `raise`：让 `alembic downgrade` 在开发库里仍然可用（只回滚版本号），
    但**不要**把它当作数据恢复手段。
    """
    pass
