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
