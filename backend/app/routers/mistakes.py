"""错题本（票 09 / Implementation 8 / 用户故事 #31-#33）。

- 答错的题在提交判定（票 06）时自动写入 MistakeBook，无需手动记录。
- 本路由按考点聚合呈现（同一考点反复错合并，显示错次），按错次降序。
- 一键重练复用票 03 的 POST /api/sessions/start（knowledge_point 发起入口）。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_candidate
from ..models import Candidate, MistakeBook, Module, Question
from ..schemas import MistakeGroupOut

router = APIRouter(prefix="/api/mistakes", tags=["mistakes"])


@router.get("", response_model=list[MistakeGroupOut])
def list_mistakes(
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> list[MistakeGroupOut]:
    """错题本：按考点聚合（错次累计、按错次降序）。"""
    rows = (
        db.query(MistakeBook, Question.module)
        .join(Question, Question.id == MistakeBook.question_id)
        .filter(MistakeBook.candidate_id == c.id)
        .all()
    )

    groups: dict[str, dict] = {}
    for mb, module in rows:
        g = groups.setdefault(
            mb.knowledge_point,
            {"wrong_count": 0, "question_ids": [], "last_wrong_at": "", "module": module},
        )
        g["wrong_count"] += mb.wrong_count
        g["question_ids"].append(mb.question_id)
        last = mb.last_wrong_at.isoformat() if mb.last_wrong_at else ""
        if last > g["last_wrong_at"]:
            g["last_wrong_at"] = last

    out = [
        MistakeGroupOut(
            knowledge_point=kp,
            module=g["module"],
            wrong_count=g["wrong_count"],
            question_count=len(g["question_ids"]),
            question_ids=sorted(g["question_ids"]),
            last_wrong_at=g["last_wrong_at"],
        )
        for kp, g in groups.items()
    ]
    out.sort(key=lambda x: (-x.wrong_count, x.knowledge_point))
    return out
