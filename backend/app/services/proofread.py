"""审校管线辅助（票 13 / Implementation 19-21）。

- 抽检比例按模块配置：文化素养最高（常识/历史/科技最易出错），可调。
- 待审队列从 PENDING 池按比例抽样；驳回即弃（REJECTED 不再进入出题池）。
"""

import math
import random

from sqlalchemy.orm import Session as DBSession

from ..models import Module, ProofreadStatus, Question

# 抽检比例配置（可调）：文化素养最高；其余模块默认值。
DEFAULT_SAMPLE_RATIO = 0.1
SAMPLE_RATIOS: dict[Module, float] = {
    Module.CULTURE_LITERACY: 0.3,
}


def sample_pending_questions(db: DBSession) -> list[Question]:
    """按模块抽检比例从待审池抽样，供人工审校。"""
    pending = db.query(Question).filter(Question.proofread_status == ProofreadStatus.PENDING).all()
    by_module: dict[Module, list[Question]] = {}
    for q in pending:
        by_module.setdefault(q.module, []).append(q)

    sampled: list[Question] = []
    for module, rows in by_module.items():
        ratio = SAMPLE_RATIOS.get(module, DEFAULT_SAMPLE_RATIO)
        n = min(len(rows), math.ceil(len(rows) * ratio))
        sampled.extend(random.sample(rows, n))
    return sampled


def get_valid_knowledge_points() -> set[str]:
    """考点归属白名单：以官方考纲考点骨架（种子数据）为准。"""
    from ..seed.questions_data import KNOWLEDGE_POINTS

    return {kp for kps in KNOWLEDGE_POINTS.values() for kp in kps}
