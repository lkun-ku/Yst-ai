"""闲时批量生成脚本（票 14 / ADR-0002 后果）：扩充题目池至 1 万级。

- 按考纲考点骨架逐考点生成变式题；结构化校验不通过即弃（Implementation 19-20）。
- 产物以 source=POOL、proofread_status=PENDING 入库（池化供给，人工抽检把关）。
- 默认走接缝 B 假实现（不耗额度）；生产配置 LLM_MODE=real + LLM_API_KEY 后真实生成。
- 运行：python -m app.pipeline.batch_generate --per-kp 2

⚠️ **P0 安全闸（止血）**：`LLM_MODE` 默认为 `fake`，此时 `get_llm_client()` 返回的
`FakeLLMClient` 产出的变式题是**同模板占位题**（题干形如「（实时变式1）下列关于《XX》
的表述，正确的是？」）。它们以 `proofread_status=PENDING` 写进官方池后**会被抽题命中**
（`routers/sessions.py` 只过滤 `REJECTED`），用户将直接刷到废题。
故 `main()` 默认拒绝执行，必须显式 `--allow-fake` 才放行（仅供本地演示/联调）。

若库中已有此类占位题，请运行 `python scripts/import_real_bank.py`（在 backend/ 下执行）
重建官方池——该脚本会先 clear 掉全部 `source=POOL` 的历史数据再导入真实题库。
"""

import argparse
import json
import logging

from sqlalchemy.orm import Session as DBSession

from ..config import settings
from ..models import (
    ProofreadStatus,
    Question,
    QuestionSource,
    QuestionType,
    SOURCE_KIND_AI_VARIANT,
)
from ..seed.knowledge_tree import TREE_STAGE, TREE_SUBJECT, index_by_module_kp
from ..seed.questions_data import KNOWLEDGE_POINTS
from ..services.dedup import bucket_of, find_duplicate, load_official_index
from ..services.llm_client import GenerationRequest, LLMClient, get_llm_client
from ..services.validation import validate_question_payload

logger = logging.getLogger(__name__)

_VALID_KPS = {kp for kps in KNOWLEDGE_POINTS.values() for kp in kps}

#: 合法难度取值（与 questions.difficulty 的约定一致）
DIFFICULTIES = ("easy", "medium", "hard")


def _difficulty_of(payload: dict) -> str | None:
    """取模型回传的难度；非法值**留空**而不硬塞默认值——宁缺勿错。

    塞个默认 medium 会让「这个考点全是 medium」看起来像统计结论，实际是数据缺失，
    后续自适应组卷会被误导。
    """
    value = str(payload.get("difficulty") or "").strip().lower()
    return value if value in DIFFICULTIES else None


def batch_generate(db: DBSession, per_kp: int = 1, client: LLMClient | None = None) -> dict:
    """按考点批量生成变式题并入库。

    返回 `{generated, rejected, deduped}`：
    - `rejected`：结构化校验不通过（产物本身有问题）；
    - `deduped`：与**已有题目**重复被拦（考点上已经有能用的题了）。

    两者分开计数是有用的运维信号：前者说明生成质量差，后者说明选题规划在重复劳动
    （该考点已经不缺题了，却还在给它出）——见 `services/dedup.py` 的三层判重。

    判重口径：逐考点比对，**不跨考点**。跨考点近似比对会系统性误杀
    （「受教育权」与「受教育权保护」这类相邻考点名天生长得像）。
    """
    client = client or get_llm_client()
    generated = 0
    rejected = 0
    deduped = 0

    # 同模板实现（fake）产出的是彼此只差编号的伪题，近似判重会成批误杀 → 退化为精确判重
    near_dup_enabled = bool(getattr(client, "produces_varied_stems", True))
    index = load_official_index(db)
    kp_ids = index_by_module_kp(db)

    for module, points in KNOWLEDGE_POINTS.items():
        for kp in points:
            bucket = bucket_of(index, module.value, kp)
            kp_id = kp_ids.get((module.value, kp))
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
                hit, hit_id = find_duplicate(stem, existing=bucket, enable_near=near_dup_enabled)
                if hit:
                    deduped += 1
                    logger.info(
                        "batch_generate: 判重命中（%s，与题目 %s 重复），丢弃：%s",
                        hit,
                        hit_id,
                        stem[:30],
                    )
                    continue

                question = Question(
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
                    # P1 维度：科目 / 学段 / 知识点树 / 难度 / 来源。
                    # 缺了这几轴，题库扩到万级也只是堆在一个格子里，无法按维度聚合与配额。
                    subject=TREE_SUBJECT,
                    stage=TREE_STAGE,
                    difficulty=_difficulty_of(payload),
                    source_kind=SOURCE_KIND_AI_VARIANT,
                    kp_id=kp_id,
                )
                db.add(question)
                db.flush()  # 取回自增 id：同轮内后续题目命中重复时要能指出撞上了哪一道
                bucket.append((question.id, stem))
                generated += 1

    db.commit()
    return {"generated": generated, "rejected": rejected, "deduped": deduped}


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。返回进程退出码（0=成功，2=被安全闸拒绝）。

    `argv` 显式传入便于测试（None 时取 sys.argv）。

    安全闸依据 `settings.llm_mode` 而非注入的 client：CLI 不注入 client，
    `get_llm_client()` 会按该开关返回 Fake/Real，故它是唯一可信判据。
    （库函数 `batch_generate(db, client=...)` 不加闸——测试与显式注入场景需要它。）
    """
    parser = argparse.ArgumentParser(description="闲时批量生成变式题扩充题目池")
    parser.add_argument("--per-kp", type=int, default=1, help="每考点生成题数")
    parser.add_argument(
        "--allow-fake",
        action="store_true",
        help="允许在 LLM_MODE=fake 下写入官方池（产物为占位模板题，仅供本地演示，勿进生产库）",
    )
    args = parser.parse_args(argv)

    if settings.llm_mode != "real" and not args.allow_fake:
        print(
            "[batch-generate] 已拒绝执行：LLM_MODE=%s 下 get_llm_client() 返回 FakeLLMClient，\n"
            "  其变式题产物是**占位模板题**（无真实学科内容），写入官方池后会被抽题命中并展示给用户。\n"
            "  真实扩池：配置 LLM_MODE=real + LLM_API_KEY 后重跑本脚本。\n"
            "  重建官方池：python scripts/import_real_bank.py（在 backend/ 下运行）\n"
            "  仅本地演示可加 --allow-fake（产物请自行清理，勿进生产库）。"
            % settings.llm_mode
        )
        return 2

    from ..db import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        stats = batch_generate(db, per_kp=args.per_kp)
        total = db.query(Question).count()
        print(f"batch-generate: {stats} total_pool={total}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
