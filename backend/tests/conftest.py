import os
import uuid

#: **文件名按运行唯一**（进程号 + 随机后缀）。
#:
#: 这里原本是固定的 `.test_tmp.db`，它会造成**间歇性的唯一约束失败**：
#: 上一次运行若没退干净（中断、被杀，或被工具当成"长驻服务"留在后台），
#: 或两个 pytest 并发运行，两者就会**共用同一个 SQLite 文件** ——
#: 一个在 `drop_all`/`create_all`，另一个正在插数据，于是撞 `candidates.id/unionid`。
#: 本仓真实踩过：**同一条命令三次跑出 12 红 / 全绿 / 3 红**，而代码一行没改。
#:
#: 文件名唯一之后，"上次没清干净"从「会污染下一次」降级成「只占几 KB 的垃圾文件」。
_DB_FILE = f".test_tmp_{os.getpid()}_{uuid.uuid4().hex[:6]}.db"

# 必须在导入 app 之前设定，确保测试使用独立 SQLite 文件库。
os.environ["DATABASE_URL"] = f"sqlite:///./{_DB_FILE}"
os.environ["AUTO_MIGRATE"] = "true"
# 精排默认关闭：真实模型是 266MB 的本地下载，让单测依赖它有两重代价 ——
# 慢，且在一台没下过权重的机器上行为不同。需要验证重排链路的用例显式设
# RERANK_IMPL=fake 并用 FakeReranker 替身；**真实模型的收益由
# eval/retrieval_eval.py --rerank 测量**，不在单测里。
os.environ.setdefault("RERANK_IMPL", "off")
# 用户决策（2026-04-09）：测试使用**真实环境**（backend/.env 的 real 模式），
# 不强制 fake——真实模型下的出题/判分/复盘才可信。代价：测试变慢（doc 生成 30s+/次）。

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine, init_db


@pytest.fixture(autouse=True)
def _isolate_answer_bank(tmp_path, monkeypatch):
    """**默认把答案库指到空目录** —— 测试必须与仓库数据无关。

    ⚠️ 这条是踩出来的：`search_kb` 改成"答案库优先"之后，仓库里的
    `data/answer_bank/真题答卷库.json`（294 条真实真题）会进入检索结果，
    于是 `test_teacher_agent` 中**与数据无关的断言**（如"工具轮次有硬上限"）被
    仓库内容改变了结果 —— 测试于是变成"看数据吃饭"。

    需要答案库的用例（`tests/test_answer_bank.py`）自行 monkeypatch 指向自己的临时库。
    """
    from app.services import answer_bank as ab

    monkeypatch.setattr(ab.settings, "answer_bank_enabled", True)
    monkeypatch.setattr(ab.settings, "answer_bank_dir", str(tmp_path / "_no_bank"))


@pytest.fixture(autouse=True)
def _force_fake_llm(monkeypatch):
    """**测试一律走 Fake 模型** —— 不许看环境变量吃饭。

    ⚠️ 这条是踩出来的（2026-06-15）：外层 shell 里 `LLM_MODE=real` 时，测试中
    `get_llm_client()` 会返回**真实客户端**并真的发请求 —— 实测三个用例合计跑了 **400 秒**，
    而且**偷偷消耗 API 额度**；更糟的是断言是按 fake 写的，**红不了**（付出代价却拿不到信号）。
    同一个原因还会让 `test_pipeline.test_fake_client_is_default_no_api_quota` 在 real 环境下必红。

    与本文件 `_isolate_answer_bank`（测试与仓库数据无关）是同一条纪律：
    **测试不许依赖环境状态**。确实需要真实客户端的用例，请自己 `monkeypatch` 打开并写明理由。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "llm_mode", "fake")


@pytest.fixture(scope="session", autouse=True)
def _reset_db():
    """会话级重置：本次运行使用一个**全新的**库文件，跑完就删。

    ⚠️ 落盘的库**不是**本次运行的（见 `_DB_FILE`）就一律不当自己的 ——
    删别人的库等于删一个可能正在使用的文件（Windows 上还会因句柄占用而报错）。
    顺手清掉本目录下的**陈旧**临时库：它们是历史中断运行留下的垃圾，
    不清会一直堆着。清理是**尽力而为**：删不掉（被别的进程占着）就跳过，
    绝不因此让整个测试会话失败 —— 那正是"清理比被测对象更脆弱"的经典错误。
    """
    import glob
    import app.models  # noqa: F401  确保元数据含全部表

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()
    for path in [f"./{_DB_FILE}", *glob.glob("./.test_tmp_*.db")]:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass  # 被别的进程占着 → 留给下一次清理


@pytest.fixture
def db_session():
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
