"""question_dims_and_knowledge_points

P1 打地基：给题库补上多维分类，并把扁平考点名升维成知识点树。

1. 新表 `knowledge_points`：自关联知识点树（模块 → … → 知识点），
   覆盖度统计与知识点级掌握度的聚合锚点。
2. `questions` 新增 5 列：`subject` / `stage` / `difficulty` / `source_kind` / `kp_id`。
   **全部可空**——存量 418 题与新列并存，迁移不需要停机、不需要回填即可上线。

**幂等化**：`0001_initial` 以 `Base.metadata.create_all()` 建立**当前 models 的全量** schema，
全新库上这些对象已经存在。因此每一步都必须先探测再执行，否则 `alembic upgrade head`
会因 duplicate table / duplicate column / duplicate index 失败（本仓已有前车之鉴，
见 6c138b351314 与 12ead2620c5f 的「幂等化」说明）。

**本迁移只建 schema，不灌数据**：
- 知识点树是**参考数据**（考纲骨架），不在迁移里逐行插入——那会把 150+ 行考纲
  永久冻死在迁移文件里，后续修订考纲就要再写一条数据迁移。
  它由 `db.init_db()`（`AUTO_MIGRATE=true` 的启动路径）或
  `python scripts/seed_knowledge_tree.py` 幂等写入。
- `questions` 的维度回填同理，见同一个脚本。

⚠️ 生产若以 `AUTO_MIGRATE=false` 启动，**升级后必须显式执行**：
    python scripts/seed_knowledge_tree.py
否则 `questions.kp_id` 恒为空、覆盖度统计没有锚点。
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7f1c2d3e4a5'
down_revision = 'd9e6096bc0af'
branch_labels = None
depends_on = None


#: questions 新增列 → 列名、类型、索引名（索引名与 SQLAlchemy `index=True` 的默认命名一致）
_QUESTION_COLUMNS = (
    ('subject', sa.String(length=32), 'ix_questions_subject'),
    ('stage', sa.String(length=32), 'ix_questions_stage'),
    ('source_kind', sa.String(length=32), 'ix_questions_source_kind'),
    ('kp_id', sa.Integer(), 'ix_questions_kp_id'),
)
#: difficulty 不建索引：它是「组卷时按难度取题」的过滤条件，基数只有 3，
#: 而题库量级下真正的选择度来自 module / subject —— 低基数索引只会拖慢写入。
#: （留在此处显式说明，免得后来者以为是漏了。）


def _has_table(bind, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _cols(bind, table: str) -> set:
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _indexes(bind, table: str) -> set:
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return set()
    return {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # ---------- 1) 知识点树（新表）----------
    if not _has_table(bind, 'knowledge_points'):
        op.create_table(
            'knowledge_points',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('code', sa.String(length=255), nullable=False),
            sa.Column('subject', sa.String(length=32), nullable=False),
            sa.Column('stage', sa.String(length=32), nullable=True),
            sa.Column('parent_id', sa.Integer(), nullable=True),
            sa.Column('level', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=128), nullable=False),
            sa.Column('exam_frequency', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['parent_id'], ['knowledge_points.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('code'),
        )
        for name, cols in (
            ('ix_knowledge_points_code', ['code']),
            ('ix_knowledge_points_subject', ['subject']),
            ('ix_knowledge_points_stage', ['stage']),
            ('ix_knowledge_points_parent_id', ['parent_id']),
            ('ix_knowledge_points_level', ['level']),
            ('ix_knowledge_points_name', ['name']),
        ):
            op.create_index(name, 'knowledge_points', cols, unique=False)

    # ---------- 2) questions 维度列 ----------
    question_cols = _cols(bind, 'questions')
    with op.batch_alter_table('questions', schema=None) as batch_op:
        if 'subject' not in question_cols:
            batch_op.add_column(sa.Column('subject', sa.String(length=32), nullable=True))
        if 'stage' not in question_cols:
            batch_op.add_column(sa.Column('stage', sa.String(length=32), nullable=True))
        if 'difficulty' not in question_cols:
            batch_op.add_column(sa.Column('difficulty', sa.String(length=16), nullable=True))
        if 'source_kind' not in question_cols:
            batch_op.add_column(sa.Column('source_kind', sa.String(length=32), nullable=True))
        if 'kp_id' not in question_cols:
            # **外键必须命名**：batch 模式在重建表时要靠名字处理约束，
            # 匿名外键会直接抛 `Constraint must have a name`（实测踩到）。
            # 名字与 models 里一致，保证「新建库 create_all」与「老库迁移」两条路径
            # 产出同名约束，不出现 schema 漂移。
            batch_op.add_column(
                sa.Column(
                    'kp_id',
                    sa.Integer(),
                    sa.ForeignKey('knowledge_points.id', name='fk_questions_kp_id'),
                    nullable=True,
                )
            )

    # ---------- 3) questions 新列索引 ----------
    # 单独建而不放进 batch_op：SQLite 的 batch 模式在重建表时对内联索引支持不稳定。
    question_indexes = _indexes(bind, 'questions')
    for _col, _type, index_name in _QUESTION_COLUMNS:
        if index_name not in question_indexes:
            index_col = index_name[len('ix_questions_'):]
            op.create_index(index_name, 'questions', [index_col], unique=False)


def downgrade() -> None:
    bind = op.get_bind()

    question_indexes = _indexes(bind, 'questions')
    for _col, _type, index_name in _QUESTION_COLUMNS:
        if index_name in question_indexes:
            op.drop_index(index_name, table_name='questions')

    question_cols = _cols(bind, 'questions')
    with op.batch_alter_table('questions', schema=None) as batch_op:
        for col, _type, _index in _QUESTION_COLUMNS:
            if col in question_cols:
                batch_op.drop_column(col)
        if 'difficulty' in question_cols:
            batch_op.drop_column('difficulty')

    if _has_table(bind, 'knowledge_points'):
        # 先删 questions.kp_id 的外键引用方才的表，否则 PG 会拒绝 DROP TABLE
        op.drop_table('knowledge_points')
