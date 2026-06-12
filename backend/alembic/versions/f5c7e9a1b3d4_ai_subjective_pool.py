"""ai_subjective_pool

新增 `ai_subjective_questions`：**AI 主观题池** —— AI 按考纲生成的主观题，生成一次、复用多次。

## 为什么要这张表

批改页的「AI 出一道」第一版是**每次现生成**：点一次等 10–30 秒、花一次额度，
而且同一道题不复用。加池子之后：**先查池、没有再生成**，常态是秒回。

## 为什么不复用 `questions`

`questions` 是题库，闯关抽题 / 模考组卷 / 覆盖度统计 / 薄弱考点都挂在它上面。
AI 题**未经人审**，混进去会同时踩两个已知形态的坑：

- 闯关过滤是 `source == POOL` + `proofread_status != REJECTED` → **`PENDING` 会被抽中**，
  未审校的 AI 题就直接进了闯关；
- `module` / `knowledge_point` 必填且参与统计，而 AI 题的真实归属不确定，填猜的值会污染统计。

单独一表 = 只服务主观题练习，不参与任何既有查询。

## 幂等

`0001_initial` 用 `create_all` 建的是**当前 models 的全量 schema** —— 全新库上这张表已存在，
所以先探测再建（与 `d2f4a6b8c0e1` / `e3a5c7d9f1b2` 同一条约定）。
"""
from alembic import op
import sqlalchemy as sa


revision = 'f5c7e9a1b3d4'
down_revision = 'e3a5c7d9f1b2'
branch_labels = None
depends_on = None

_TABLE = 'ai_subjective_questions'


def _has_table(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('qtype', sa.String(length=16), nullable=False),
        sa.Column('label', sa.String(length=32), nullable=False, server_default=''),
        sa.Column('score', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('stem', sa.Text(), nullable=False),
        sa.Column('basis', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('used_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(f'ix_{_TABLE}_qtype', _TABLE, ['qtype'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE):
        return
    # ⚠️ 下迁会**丢掉池里已生成的题**（它们没有别处的副本）。
    # 只损失"再来一次生成"的成本，不损失用户数据 —— 但仍是有意的取舍，不静默处理。
    op.drop_index(f'ix_{_TABLE}_qtype', table_name=_TABLE)
    op.drop_table(_TABLE)
