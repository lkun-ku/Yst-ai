"""闲时批量生成脚本（票 14 / ADR-0002 后果）：扩充题目池至 1 万级。

- 按考纲考点骨架逐考点生成变式题；结构化校验不通过即弃（Implementation 19-20）。
- 产物以 source=POOL、proofread_status=PENDING 入库（池化供给，人工抽检把关）。
- 默认走接缝 B 假实现（不耗额度）；生产配置 LLM_MODE=real + LLM_API_KEY 后真实生成。
- 运行：python -m app.pipeline.batch_generate --per-kp 2
"""

import argparse
import json

from sqlalchemy.orm import Session as DBSession

from ..models import ProofreadStatus, Question, QuestionSource, QuestionType
from ..seed.questions_data import KNOWLEDGE_POINTS
from ..services.llm_client import GenerationRequest, LLMClient, get_llm_client
from ..services.validation import validate_question_payload

_VALID_KPS = {kp for kps in KNOWLEDGE_POINTS.values() for kp in kps}


def batch_generate(db: DBSession, per_kp: int = 1, client: LLMClient | None = None) -> dict:
    """按考点批量生成变式题并入库，返回 {generated, rejected} 统计。"""
    client = client or get_llm_client()
    generated = 0
    rejected = 0

    existing = {
        (q.module, q.knowledge_point, q.stem)
        for q in db.query(Question.module, Question.knowledge_point, Question.stem).all()
    }

    for module, points in KNOWLEDGE_POINTS.items():
        for kp in points:
            for _ in range(per_kp):
                res = client.generate(
                    GenerationRequest(kind="variant", knowledge_point=kp, context={"module": module.value})
                )
                payload = res.payload
                errors = validate_question_payload(payload, _VALID_KPS) if payload else ["empty payload"]
                if errors:
                    rejected += 1  # 不通过即弃
                    continue

                stem = payload["stem"]
                if (module, kp, stem) in existing:
                    rejected += 1  # 重复弃
                    continue

                db.add(
                    Question(
                        module=module,
                        knowledge_point=kp,
                        stem=stem,
                        options=json.dumps(payload["options"], ensure_ascii=False),
                        answer=json.dumps(payload["answer"], ensure_ascii=False),
                        explanation=payload["explanation"],
                        type=QuestionType(payload.get("type", "single")),
                        source=QuestionSource.POOL,
                        proofread_status=ProofreadStatus.PENDING,
                        aigc_flag=True,
                    )
                )
                existing.add((module, kp, stem))
                generated += 1

    db.commit()
    return {"generated": generated, "rejected": rejected}


def main() -> None:
    parser = argparse.ArgumentParser(description="闲时批量生成变式题扩充题目池")
    parser.add_argument("--per-kp", type=int, default=1, help="每考点生成题数")
    args = parser.parse_args()

    from ..db import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        stats = batch_generate(db, per_kp=args.per_kp)
        total = db.query(Question).count()
        print(f"batch-generate: {stats} total_pool={total}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
