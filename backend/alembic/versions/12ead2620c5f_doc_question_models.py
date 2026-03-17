"""doc question models

文档出题模块：questions 表新增 owner_candidate_id / doc_id 列与 answer 扩为 Text；
documents / document_chunks / doc_tasks 三张新表由 0001_initial 的 create_all(metadata) 在建库时一并创建，
本迁移只负责既有 questions 表的增量变更，避免与 0001 重复建表。

Revision ID: 12ead2620c5f
Revises: 0001_initial
Create Date: 2026-05-24 23:31:46.753493
"""
from alembic import op
import sqlalchemy as sa


revision = '12ead2620c5f'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('questions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('owner_candidate_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('doc_id', sa.Integer(), nullable=True))
        batch_op.alter_column(
            'answer',
            existing_type=sa.VARCHAR(length=64),
            type_=sa.Text(),
            existing_nullable=False,
        )
        batch_op.create_index(batch_op.f('ix_questions_doc_id'), ['doc_id'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_questions_owner_candidate_id'), ['owner_candidate_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_questions_doc_id', 'documents', ['doc_id'], ['id']
        )
        batch_op.create_foreign_key(
            'fk_questions_owner_candidate_id', 'candidates', ['owner_candidate_id'], ['id']
        )


def downgrade() -> None:
    with op.batch_alter_table('questions', schema=None) as batch_op:
        batch_op.drop_constraint('fk_questions_owner_candidate_id', type_='foreignkey')
        batch_op.drop_constraint('fk_questions_doc_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_questions_owner_candidate_id'))
        batch_op.drop_index(batch_op.f('ix_questions_doc_id'))
        batch_op.alter_column(
            'answer',
            existing_type=sa.Text(),
            type_=sa.VARCHAR(length=64),
            existing_nullable=False,
        )
        batch_op.drop_column('owner_candidate_id')
        batch_op.drop_column('doc_id')
