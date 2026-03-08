from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .db import get_db
from .models import Candidate


def get_current_candidate(
    x_unionid: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Candidate:
    """MVP 期以 X-Unionid 头识别考生（真实环境由微信开放平台签发，D1 已确认按 unionid 设计）。"""
    if not x_unionid:
        raise HTTPException(status_code=401, detail="missing X-Unionid")
    cand = db.query(Candidate).filter(Candidate.unionid == x_unionid).first()
    if cand is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    return cand
