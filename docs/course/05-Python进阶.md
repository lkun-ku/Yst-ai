# 第 05 节 · Python 进阶

**本节特点**：讲的是「换任何项目都用得上」的工程知识，
但每个概念都挂在本项目的真实代码上。

## 学习目标

1. 理解抽象基类与「接缝」如何决定可测试性
2. 看懂依赖注入在做什么（不只是会用 `Depends`）
3. 理解装饰器的本质
4. 知道 numpy 为什么快、GIL 为什么让多线程分场景
5. 理解事务快照（MVCC）这个并发坑
6. 认识「JSON 列」这类设计的代价

---

## 1. 抽象基类（ABC）与「接缝」

```python
class LLMClient(ABC):
    @abstractmethod
    def generate(self, req: GenerationRequest) -> GenerationResult: ...
```

**【原理】`ABC` + `@abstractmethod` 让这个类无法被实例化**：

```python
LLMClient()   # TypeError: Can't instantiate abstract class
```

强制子类实现所有抽象方法。**这是一个「编译期（其实是导入期）约束」**。

### 那一个函数决定了一切

```360:364:backend/app/services/llm_client.py
def get_llm_client() -> LLMClient:
    # 接缝 B 的唯一切换点：LLM_MODE=fake（默认/测试，不耗额度）| real。
    if settings.llm_mode == "real" and settings.llm_api_key:
        return RealLLMClient()
    return FakeLLMClient()
```

**这是整条 AI 链路可测试性的全部秘密。**

对比两种设计：

```
❌ 业务代码直接调 OpenAI
   generate_by_scope() → requests.post("https://api.openai.com/...")
   → 测试时必须真的调 API：花钱、慢、不稳定、结果不可复现

✅ 业务代码依赖抽象
   generate_by_scope() → LLMClient（抽象接口）
                          ├─ RealLLMClient   ← 生产
                          └─ FakeLLMClient   ← 测试
   → 全链路测试零 API 费用、毫秒级、结果确定
```

**【原理】依赖倒置原则（DIP）**：高层模块不应该依赖低层模块，两者都应该依赖抽象。

那个「抽象与实现的分界点」叫**接缝（seam）** —— 一个可以在不改代码的前提下
替换实现的位置。**接缝的位置决定了系统的可测试性上限。**

### Fake 实现怎么「假装」

```134:150:backend/app/services/llm_client.py
    def ask(self, prompt: str, timeout: int = 30) -> str | None:
        """离线确定性回应，按提示词标记分发（出题 / 相关性 / 自检 / 改写）。"""
        if "【生成题目】" in prompt:
            tm = re.search(r'type 固定为 "(\w+)"', prompt)
            qtype = tm.group(1) if tm else "single"
            cm = re.search(r"生成 (\d+) 道", prompt)
            count = int(cm.group(1)) if cm else 1
            items = _fake_doc_questions("个人资料-考点", qtype, count)
            return json.dumps({"questions": items}, ensure_ascii=False)
        if "【检索相关性评分】" in prompt:
            return '{"relevant": true, "score": 0.9}'
        if "【生成自检】" in prompt:
            return '{"passed": true, "score": 0.9, "issues": []}'
        if "【查询改写】" in prompt:
            return prompt.split("【查询改写】", 1)[1]
        return ""
```

它靠**约定标记**区分不同 prompt。这就是为什么 `prompts_kb.py` 里每个 prompt 末尾都粘了一个标记：

```python
lines.append("【生成题目】")
```

注释写明了原因：

```
# - 出题提示迁移自 `llm_client.build_doc_question_prompt` 的防幻觉 / 难度具象化 / 去重 / 严格 JSON 经验，
#   仅去除教资固化语境，并用 `【生成题目】` 标记便于 FakeLLMClient 分发。
```

**【原理】可测试性倒逼了更好的设计。** 为了让测试能分发，prompt 反而有了明确的结构标记，
生产代码也更清晰。**这是「测试驱动设计」的一个真实收益。**

### ABC vs Protocol

Python 3.8+ 还有 `typing.Protocol`，支持**结构化子类型**（鸭子类型）：

| | `ABC` | `Protocol` |
|---|---|---|
| 类型 | 名义子类型 | 结构化子类型 |
| 要求 | 必须显式继承 | 满足接口即可（不用继承） |
| 适合 | 想强制约束实现方 | 想描述「长得像就行」的接口 |

本项目用 `ABC` 是对的 —— 确实需要「必须继承」这个约束（防止有人写了个
`MyLLM` 忘了实现 `generate`，运行时才炸）。

---

## 2. 依赖注入（`Depends`）

```8:18:backend/app/deps.py
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
```

```python
@router.post("/submit", response_model=SubmitOut)
def submit_session(
    body: SubmitIn,
    c: Candidate = Depends(get_current_candidate),   # ← 注入
    db: Session = Depends(get_db),                   # ← 注入
) -> SubmitOut:
```

### `Depends` 到底做了什么

在调用你的函数之前，FastAPI 会：

```
1. 看到 c 依赖 get_current_candidate
2. 去调它（它又依赖 get_db，继续递归解析）
3. 把返回值作为 c 传进来
4. 任何环节抛 HTTPException → 整个请求直接返回错误
```

**【原理】控制反转（IoC）**：`submit_session` 不再**自己创建**依赖，
而是**声明需要什么**，由框架提供。

### 三个实际好处

**① 测试时能替换**

```python
def get_llm_client_dep() -> LLMClient:
    """可覆写的 LLM 客户端依赖：测试用 app.dependency_overrides 注入 fake。"""
    return get_llm_client()
```

注释直接写了用途。测试里：

```python
app.dependency_overrides[get_llm_client_dep] = lambda: FakeLLMClient()
```

**② 权限校验只写一次**

20 个接口都写 `c: Candidate = Depends(get_current_candidate)`，
越权检查自动生效。**这就是为什么 `deps.py` 里那个 18 行的函数是整个后端的安全基石。**

**③ 资源生命周期统一管理**

`get_db` 负责创建和关闭 Session，业务函数不用管。

---

## 3. 装饰器

```python
@router.post("/reply")
def chat_reply(...):
    ...
```

等价于：

```python
def chat_reply(...): ...
chat_reply = router.post("/reply")(chat_reply)
```

`router.post("/reply")` 返回一个**装饰器函数**，它接收 `chat_reply`，
把「路径 → 函数」的映射注册进路由表，然后返回原函数。

### 装饰器的本质

```python
def my_decorator(func):
    def wrapper(*args, **kwargs):
        print("调用前")
        result = func(*args, **kwargs)
        print("调用后")
        return result
    return wrapper
```

**【原理】装饰器是「接收函数、返回函数」的高阶函数。**
它只依赖两个基础特性：**函数是对象** + **闭包**。

本项目用到的装饰器：

| 装饰器 | 作用 |
|---|---|
| `@router.post/get/patch/delete` | 注册路由 |
| `@dataclass` | 自动生成 `__init__` / `__repr__` / `__eq__` |
| `@abstractmethod` | 标记抽象方法 |
| `@pytest.mark.skipif` | 条件跳过测试 |

---

## 4. numpy 向量化 vs Python 循环

对比项目里两段实现同样数学的代码：

```python
# ① Python 循环版（embedding.cosine_similarity，一次算一对）
dot = sum(x * y for x, y in zip(a, b))
na = math.sqrt(sum(x * x for x in a))
nb = math.sqrt(sum(y * y for y in b))
return dot / (na * nb)
```

```python
# ② numpy 矩阵版（kb_retrieval.vector_rank，一次算 N 对）
mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
sims = mn @ qn        # (n,1024) @ (1024,) → (n,)
```

### 为什么快

| 原因 | 说明 |
|---|---|
| **内存布局** | numpy 数组是连续 C 数组；Python list 是指针数组（每个元素是独立对象） |
| **BLAS** | 矩阵乘法调用高度优化的 Fortran/C 汇编，能用 SIMD 指令并行 |
| **无解释器开销** | 避免逐元素走一遍 Python 字节码 |

经验值：数值计算上 numpy 通常比 Python 循环快 **10~100 倍**。

**【原理】第一定律：能用数组运算就别写循环。** 这是 numpy/pandas 的核心心法。

### 顺带一个必须记住的坑

`keepdims=True` 不是可选项：

```python
np.linalg.norm(mat, axis=1)                    # (5,1024) → (5,)
np.linalg.norm(mat, axis=1, keepdims=True)     # (5,1024) → (5,1)
```

广播时 shape 对齐**从右往左**：

```
(5,1024) / (5,1)   ✅ 广播成 (5,1024) / (5,1024)
(5,1024) / (5,)    ❌ 会去匹配列 → 结果完全错（且可能不报错！）
```

**「算错但不报错」是 numpy 最危险的失败模式。**

---

## 5. GIL 与线程池

```28:41:backend/app/services/task_pool.py
def get_pool(name: str = GEN) -> ThreadPoolExecutor:
    """按用途取线程池（惰性创建）。"""
    pool = _pools.get(name)
    if pool is None:
        workers = settings.doc_embed_workers if name == EMBED else settings.doc_gen_workers
        pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix=name)
        _pools[name] = pool
    return pool
```

### GIL 是什么

**GIL（全局解释器锁）**：CPython 中同一时刻只有一个线程能执行 Python 字节码。

推论：

| 任务类型 | 多线程效果 | 应该用什么 |
|---|---|---|
| **IO 密集**（等网络、磁盘） | ✅ 有效（等待时 GIL 已释放） | `ThreadPoolExecutor` |
| **CPU 密集**（纯计算） | ❌ 无效（还要付切换开销） | `ProcessPoolExecutor` / 多进程 |

**本项目的任务是哪种？** 调 LLM API + 查数据库 + 调 embedding API —— **全部 IO 密集**。
所以 `ThreadPoolExecutor` 是正确选择。

⚠️ 注意：**numpy 矩阵运算是纯 CPU 计算**，多线程对它没有加速效果（甚至更慢）。
这就是为什么 `vector_rank` 要靠矩阵化而不是多线程并行。

### 那个「分池防死锁」的设计

```11:13:backend/app/services/task_pool.py
**为什么出题(gen)与向量化(embed)必须分池**：
出题任务在 worker 内会调用 `wait_embed_ready` 等待切片向量化完成；若两者共用
一个池，embed 任务可能全部排在队尾无法执行，而出题 worker 又在等它 → 死锁。
```

展开：

```
共用一个池（容量 4）：
  4 个出题任务占满 4 个槽位 → 都在等向量化完成
  向量化任务在队列里排队 → 永远拿不到槽位
  → 死锁

分池后：embed 池永远有自己的槽位 → 不会饿死
```

**【原理】当任务之间存在等待关系时，不要让它们共享同一个有限资源池。**
这在操作系统、数据库连接池、协程调度里都是同一个道理
（经典案例是「线程池饥饿」thread pool starvation）。

---

## 6. 事务与 MVCC —— 那个必须 `rollback` 的坑

```138:145:backend/app/services/embedding.py
    deadline = time.time() + timeout
    while True:
        # rollback 结束当前事务快照，否则读不到 embed 线程已提交的新状态（会一直等到超时）
        db.rollback()
        if _pending_count() == 0:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(interval)
```

### 为什么必须 rollback

大多数数据库默认隔离级别是**「可重复读（Repeatable Read）」**或更高。
一旦事务开始，**后续所有读取都看到事务开始那一刻的快照** —— 这就是 **MVCC（多版本并发控制）**。

```
t0: 出题线程开始事务，快照 = {embed_status: pending}
t1: embed 线程更新 embed_status = ok 并提交
t2: 出题线程再查 → 仍看到 t0 的快照 → pending！
t3: 永远等不到 → 超时（20 秒后返回 False）
```

`db.rollback()` 结束当前事务并开启新事务 → 拿到新快照 → 看到别人提交的数据。

**【原理】「可重复读」的意义是「同一事务内多次读到相同结果」**，
这在需要一致性的业务统计里是好事，在「轮询等待别人写入」时就成了陷阱。

**这是极高频的并发 bug 模式**，名字叫「轮询看不到另一个执行体的写入」。
写任何等待代码时都要问自己：**我的读取会不会看到陈旧快照？**

---

## 7. 循环导入与延迟导入

```python
def fallback_generate(qtype: str, count: int, ctx_chunks: list[dict], gen_fn) -> list[dict]:
    """复用 #26 的质量闭环：规则校验 → 降粒度重试（不通过时 3→2→1）。

    说明：
    - 延迟导入 _generate_batch_with_fallback：kb_generate 依赖本模块的 _persist_questions
      等，顶层互导会形成循环依赖。
    """
    from .kb_generate import _generate_batch_with_fallback
```

### 循环导入是怎么发生的

```
doc_generate 导入 kb_generate
kb_generate 导入 doc_generate
→ Python 加载 a 时执行到 import b，加载 b 时又 import a（但 a 还没加载完）
→ ImportError / 部分属性缺失
```

**解法：把 import 放进函数体内** —— 只有真正调用时才会执行导入，
那时两个模块都已加载完成。

同类还有（`embedding.py`）：

```python
    from ..models import Document, DocumentChunk  # 延迟导入：避免与 models 的模块级循环依赖
```

⚠️ **更好的解法是重构依赖方向**（把共享函数提到第三个模块，如 `_shared.py`）。
延迟导入是「能跑就行」的权宜之计，会让依赖关系更难追踪。

**【原理】循环依赖本身就是「这两个模块耦合太紧」的信号。** 它不只是语法问题。

---

## 8. 防御式编程的三个模式

### 模式一：边界处统一归一化

```python
def _val(v) -> str:
    """把 Enum（含 str Enum）安全转成字符串值。"""
    if isinstance(v, Enum):
        return str(v.value)
    return str(v) if v is not None else ""
```

**【原理】在数据入口做一次转换，后面就不用到处判断。**
这类函数常常只有几行，但能消灭几十处重复判断。

### 模式二：容错读取（假设历史数据是脏的）

```python
def _load_answer_list(q: Question) -> list[str]:
    """读取标准答案（DB 中统一以 JSON 存储），容错非 JSON 的存量数据。"""
    try:
        raw = json.loads(q.answer)
    except Exception:
        return [str(q.answer or "")]
```

**这段代码的存在本身就是一条历史信息**：说明数据库里曾经存过「answer 不是 JSON」的数据。

**【原理】永远假设历史数据是脏的。** 只要线上跑过一版，就有它留下的痕迹。

### 模式三：受限生成（白名单校验）

见第 02 节 8.3 —— 模型回传的 `source_id` 必须落在召回集合内才采信。

---

## 9. ⚠️ 反模式：JSON 列

项目里大量这样存数据：

```python
question_ids: Mapped[str] = mapped_column(Text, default="[]")   # JSON: [id,...]
spec: Mapped[str] = mapped_column(Text, default="[]")           # JSON: [{"type","count"}]
points_hit: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 数组
```

叫「**JSON 列**」或「伪数组」。什么时候合理？

| 场景 | 合理？ | 理由 |
|---|---|---|
| 只整体读写，不查询内容 | ✅ | 简单直接，避免过度设计 |
| 需要按内容过滤 | ❌ | `WHERE spec->>'type' = 'single'` 在 SQLite 上根本没法做 |
| 需要 join | ❌ | 只能全表拉到内存里算 |
| 需要引用完整性 | ❌ | 删了题，`question_ids` 里的 id 就变成**悬空指针** |

`Session.question_ids` 属于第 1 类（只整体读写），**当前可接受**。
但一旦要做「统计某考点的答题情况」，就必须全表扫 + 内存解析 ——
这也是 `_select_by_weight` 全表 `.all()` 的同类问题。

**改进方向**：

1. 用 SQLAlchemy 的 `JSON` 类型（PG 上是原生 `jsonb`，可建 GIN 索引）
2. 或拆成关联表（`session_questions`）

⚠️ 还要注意 `points_hit` 那种**语义复用**（第 03 节 2 提过）——
同一列在不同 `turn_type` 下含义不同，是未来维护者的陷阱。

---

## 10. 可读性 vs 技巧的取舍（本节最重要的软知识）

看这行：

```python
qtype = str(getattr(q.type, "value", q.type) or "single")
```

它同时做了三件事：取属性、兜底、转字符串。**紧凑，但需要读三遍。**

对比展开版：

```python
raw = q.type if q.type is not None else "single"
qtype = str(raw.value) if isinstance(raw, Enum) else str(raw)
```

多两行，但一眼看懂。

| 场景 | 建议 |
|---|---|
| 底层工具函数、被调用几百次 | 紧凑写法可接受（配清晰注释） |
| 业务逻辑、会被别人读 | **展开写** |
| 一行内做超过 2 个操作 | 一律展开 |

**【原理】代码被阅读的次数远多于被书写的次数。**
「能写一行」不等于「该写一行」—— 这是初学者最容易走偏的地方。

---

## 11. 本节小结

| 概念 | 一句话记住 |
|---|---|
| ABC + 接缝 | 接缝的位置决定可测试性上限 |
| Fake 实现 | 可测试性倒逼了更好的 prompt 设计 |
| `Depends` | 声明需要什么，而不是自己创建 |
| 装饰器 | 接收函数、返回函数（高阶函数 + 闭包） |
| numpy | 能用数组运算就别写循环 |
| `keepdims=True` | shape 广播从右往左对齐，算错不报错最危险 |
| GIL | IO 密集用线程，CPU 密集用进程 |
| 分池 | 有等待关系的任务不要共享资源池 |
| `rollback` 换快照 | MVCC 下轮询看不到别人的写入 |
| 延迟导入 | 循环依赖是「耦合太紧」的信号 |
| JSON 列 | 只整体读写可接受，要查询就该拆表 |
| 可读性 | 能被读懂比写得短重要 |

---

## 12. 检索练习

**Q1.** 如果去掉 `LLMClient` 抽象，直接在业务代码里调 OpenAI，测试成本会怎么变？

**Q2.** `Depends` 在测试里怎么替换掉真实依赖？

**Q3.** 为什么 `ThreadPoolExecutor` 对调 LLM API 有效，但对 numpy 矩阵运算无效？

**Q4.** `wait_embed_ready` 里删掉 `db.rollback()` 会怎样？

**Q5.** `(5,1024) / (5,)` 为什么危险？

<details>
<summary>参考答案</summary>

**A1.** 从「零成本、毫秒级、确定性」变成「每次跑测试都**花钱**、几十秒到几分钟、结果受网络与模型随机性影响」——测试会变得没人愿意跑，进而失去意义。这是抽象带来的最大收益。

**A2.** 用 `app.dependency_overrides[get_llm_client_dep] = lambda: FakeLLMClient()`。FastAPI 会优先使用覆写后的实现，这正是项目专门写 `get_llm_client_dep()` 这个包装函数的原因。

**A3.** 调 LLM API 时大部分时间在**等网络**（此时 GIL 已释放），多线程能重叠等待。numpy 矩阵运算是**纯 CPU 计算且持有 GIL**，多线程无法并行，还可能因线程切换变慢。

**A4.** 事务在 MVCC 下一直读**事务开始时的快照**，看不到 embed 线程后续提交的 `embed_status=ok` → `_pending_count()` 永远 > 0 → 每次都等到 20 秒超时返回 False → 检索静默退化成关键词模式，出题质量下降且无告警。

**A5.** shape 广播从右往左对齐，`(5,1024)` 与 `(5,)` 会对齐到**最后一维**（1024 vs 5）——要么报错，要么按列广播算出完全错误的结果。而且它**可能不报错**，是最危险的失败模式。

</details>

---

## 13. 延伸阅读

- **《Fluent Python》(Luciano Ramalho)** —— 第 5 章讲 dataclass、第 7 章讲装饰器与闭包、第 9 章讲装饰器与类
- **《Designing Data-Intensive Applications》(Martin Kleppmann)** —— 第 7 章事务与隔离级别（MVCC 讲得最透）、第 11 章流处理（append-only log）
- **numpy 广播**：[官方文档](https://numpy.org/doc/stable/user/basics.broadcasting.html)
- **依赖倒置 / 接缝**：Michael Feathers《Working Effectively with Legacy Code》第 4 章
- **线程池饥饿**：搜索 `thread pool starvation`

---

**上一节** → [第 04 节 · Python 基础：从项目代码学](./04-Python基础-从项目代码学.md)
**下一节** → [第 06 节 · 架构现状与改造方向](./06-架构现状与改造方向.md)
