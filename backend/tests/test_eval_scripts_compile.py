"""评测脚本必须能编译。

**为什么需要这条看起来多余的守卫**：`eval/` 下的脚本**没有任何测试导入它们**
（指标纯函数在 `eval/metrics.py` 里被单测覆盖，但 `citation_eval.py` / `retrieval_eval.py` /
`run_eval.py` 是"入口脚本"，测试从不 import）。于是它们的语法/导入错误**不会让测试变红** ——
`pytest` 全绿，而脚本一跑就崩。

这不是假想的：本轮 `citation_eval.py` 第 426 行的内层引号用了半角 `"`，
写在双引号字符串里提前闭合 → `SyntaxError`，而 **461 个测试全绿**。
发现它的方式只是"我恰好又跑了一次脚本"。这类问题若在交付时才暴露，
表现就是"评测跑不起来"，比一个功能缺陷更难解释。

守卫用 `compile()` 而不是 `import`：这些脚本在导入时会执行 `sys.path` 注入与第三方依赖
（如 `onnxruntime`）的加载，import 会带来不必要的副作用与依赖；`compile()` 只做语法检查，
代价近零、且正是这类错误所在的那一层。
"""
import pathlib

import pytest

EVAL_DIR = pathlib.Path(__file__).resolve().parents[1] / "eval"


def _scripts() -> list[pathlib.Path]:
    return sorted(EVAL_DIR.glob("*.py"))


def test_评测脚本目录非空():
    """守卫本身也要有守卫：目录找不到时不能让用例静默通过。"""
    assert EVAL_DIR.is_dir(), f"找不到评测目录：{EVAL_DIR}"
    assert len(_scripts()) >= 3, f"评测脚本数异常：{[p.name for p in _scripts()]}"


@pytest.mark.parametrize("path", _scripts(), ids=lambda p: p.name)
def test_脚本可以编译(path: pathlib.Path):
    """语法错误在这一层就能抓到，不必等到真的跑脚本。"""
    source = path.read_text(encoding="utf-8")
    try:
        compile(source, str(path), "exec")
    except SyntaxError as e:  # pragma: no cover - 失败路径只在出错时走到
        pytest.fail(f"{path.name} 第 {e.lineno} 行语法错误：{e.msg}\n  {e.text}")
