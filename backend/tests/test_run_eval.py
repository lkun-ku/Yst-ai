"""评测 harness 集成测试：以子进程运行三路线对比实验（fake 模式），验证可复现且产出报告。

用子进程而非直接 import，避免 run_eval 设置的 DATABASE_URL 污染主测试会话的数据库。
"""
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import pytest

from app.config import settings

# eval/ 是本地实验目录（#40 清理后不入仓库）：脚本不存在时跳过
_EVAL_SCRIPT = os.path.join(_ROOT, "backend", "eval", "run_eval.py")


@pytest.mark.skipif(
    settings.llm_mode == "real",
    reason="run_eval 的 fake 产出断言依赖 fake LLM 模式（用户决策 2026-03-04 切 real），跳过",
)
@pytest.mark.skipif(
    not os.path.exists(_EVAL_SCRIPT),
    reason="eval/ 为本地实验目录，仓库中不存在 run_eval.py，跳过",
)
def test_run_eval_fake_produces_report():
    r = subprocess.run(
        [sys.executable, "backend/eval/run_eval.py"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stderr

    results_dir = os.path.join(_ROOT, "backend", "eval", "results")
    jsons = [f for f in os.listdir(results_dir) if f.endswith(".json")]
    assert jsons, "应产出评测 JSON 报告"

    # 校验关键结论：质量闭环路线成本高于基线（fake 下质量一致）
    import json

    with open(os.path.join(results_dir, sorted(jsons)[-1]), "r", encoding="utf-8") as f:
        result = json.load(f)
    summary = result["route_summary"]
    assert set(summary) == {"baseline", "kb_handwritten", "kb_langgraph"}
    assert summary["kb_handwritten"]["total_llm_calls"] > summary["baseline"]["total_llm_calls"]
    assert summary["kb_langgraph"]["total_llm_calls"] > summary["baseline"]["total_llm_calls"]
