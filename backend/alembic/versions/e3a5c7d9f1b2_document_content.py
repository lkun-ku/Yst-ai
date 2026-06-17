"""document_content

`documents` 新增 `content`（**可空**）：上传时**解析得到的原文文本**。

## 为什么留它

2026-03-04 实测反馈原话："查看资料看到的是切分后的，没法看原文"。根因是上传时
连**解析后的文本**都没留，只能靠"按切片拼回去"重建 —— 而重建是有损的
（实测 7980 → 7935 字，少掉的正是被提取成 `heading_path` 的章节标题行）。

A3 决策删掉的是**原始文件**（降低版权暴露面）；正文本来就以切片形式全在库里，
再留一份连续文本**不增加暴露面**，却让"查看原文"变成读原文。

**可空 + 不回填**：存量资料没有原文 → 详情接口回退到"按 seq 拼接切片"并如实标注。
新增列一律可空、不要求停机，是本仓约定。

## ⚠️ 为什么这条迁移必须写

dev/test 是 SQLite，我在本机靠 `scripts/repair_dev_schema.py` 补了列 —— 那个脚本
**只修 SQLite**。生产是 PG，只走 Alembic；不补这条，线上就是"模型有列、库里没有"，
症状与我在 dev 上诊断的 `no such column` **一模一样**（而这类缺失只会在碰到该列的
那条链路上炸，平时完全看不出来）。

## 幂等

与 `d2f4a6b8c0e1` 同理：`0001_initial` 用 `create_all` 建的是**当前 models 的全量 schema**，
所以全新库上该列已存在 —— 每一步都先探测再执行，否则全新库上迁移会直接失败。
"""
from alembic import op
import sqlalchemy as sa


revision = 'e3a5c7d9f1b2'
down_revision = 'd2f4a6b8c0e1'
branch_labels = None
depends_on = None


def _cols(bind, table: str) -> set:
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if 'content' not in _cols(bind, 'documents'):
        with op.batch_alter_table('documents', schema=None) as batch_op:
            batch_op.add_column(sa.Column('content', sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if 'content' in _cols(bind, 'documents'):
        # ⚠️ 下迁会**丢掉原文**（存量资料本就没有，新资料会退回"重建"路径）。
        # 这是有意的取舍，但必须是显式的：不静默处理，也不假装能恢复。
        with op.batch_alter_table('documents', schema=None) as batch_op:
            batch_op.drop_column('content')
