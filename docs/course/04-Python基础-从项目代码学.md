# 第 04 节 · Python 基础：从项目代码学

**本节特点**：每个知识点都配一段**你自己项目里真实存在的代码**。
不背语法，只记住「我在这里用过它，它解决什么问题」。

## 学习目标

读完能准确说出下面每个语法**为什么存在**（而不只是它怎么写）：

类型注解 · dataclass · `Enum(str, Enum)` · 可变默认参数陷阱 · 推导式 ·
集合运算 · 闭包 · `**kwargs` · 异常分级 · 上下文管理器 · 常用内置函数速查

---

## 1. 类型注解

```python
def retrieve_by_scope(db, candidate_id: int, scope: str, k: int = 8, embed_fn=None) -> list[dict]:
```

两个现代写法值得注意：

| 写法 | PEP | Python 版本 | 旧写法 |
|---|---|---|---|
| `list[dict]` | [PEP 585](https://peps.python.org/pep-0585/) | 3.9+ | `from typing import List` + `List[dict]` |
| `str \| None` | [PEP 604](https://peps.python.org/pep-0604/) | 3.10+ | `Optional[str]` |

**为什么要写类型注解？**

1. **IDE 能自动补全和报错** —— 你写 `sess.` 时能提示 `question_ids`
2. **FastAPI 靠它做自动参数校验和文档生成** —— 你后端的 `/docs` 页面就是这么来的
3. 它是**可执行的文档** —— 比注释更不容易过期

⚠️ **重要**：Python 的类型注解**运行时不做检查**（除非用 pydantic/FastAPI）。
它是给人和工具看的，不是给解释器执行的。

```python
def f(x: int) -> str:
    return x + 1        # 传 str 也不会报错，注解被忽略
```

---

## 2. dataclass

```python
@dataclass
class GenerationRequest:
    kind: str  # "variant" | "review_paragraph"
    knowledge_point: str
    context: dict | None = None
```

`@dataclass` 自动生成 `__init__` / `__repr__` / `__eq__`。等价于手写：

```python
class GenerationRequest:
    def __init__(self, kind, knowledge_point, context=None):
        self.kind = kind
        self.knowledge_point = knowledge_point
        self.context = context
```

**为什么用它而不是 dict？**

| | dict | dataclass |
|---|---|---|
| 取值 | `req["knowledge_point"]` | `req.knowledge_point` |
| 打错字 | 静默返回 `None`（危险） | 立刻 `AttributeError`（安全） |
| IDE 补全 | 无 | 有 |
| 默认值 | 要自己写 `.get(k, v)` | 声明即可 |

**「打错字会静默失败」是 dict 最危险的地方** —— dataclass 把运行期 bug 变成了开发期错误。

---

## 3. `Enum(str, Enum)` —— 为什么双重继承

```python
class Module(str, Enum):
    PROFESSIONAL_IDEA = "职业理念"
    PROFESSIONAL_ETHICS = "职业道德"
```

| 继承 | 得到什么 |
|---|---|
| `Enum` | 枚举能力：`Module.PROFESSIONAL_IDEA`、不可重复、可遍历、`in` 判断 |
| `str` | **可以直接当字符串用**：能 JSON 序列化、能存进数据库、能直接比较 |

验证一下：

```python
Module.PROFESSIONAL_IDEA == "职业理念"   # True（因为是 str 子类）
json.dumps(Module.PROFESSIONAL_IDEA)     # '"职业理念"'（可直接序列化）
```

⚠️ **一个具体的坑**：

```python
str(Module.PROFESSIONAL_IDEA)
# Python < 3.11: 'Module.PROFESSIONAL_IDEA'   ← 不是你想要的值！
# Python >= 3.11: '职业理念'
```

所以项目里专门写了工具函数：

```python
def _val(v) -> str:
    """把 Enum（含 str Enum）安全转成字符串值。"""
    if isinstance(v, Enum):
        return str(v.value)
    return str(v) if v is not None else ""
```

**【原理】边界处统一转换。** 在数据入口做一次归一化，后面就不用到处判断类型。

---

## 4. 可变默认参数陷阱（最重要的一条）

看项目里的函数签名：

```python
def _generate_batch(client, scope, qtype, count, difficulty, focus, picked, seen, extra=None, bloom_mix=None,
                    emit=None, covered_kps=None):
```

**注意：所有可选参数都是 `None`，没有一个是 `[]` 或 `{}`。** 这不是巧合。

反例：

```python
# ❌ 经典错误
def add(item, bucket=[]):
    bucket.append(item)
    return bucket

add("a")   # ['a']
add("b")   # ['a', 'b']   ← 期望 ['b']，实际复用了同一个列表！
add("c")   # ['a', 'b', 'c']
```

**原因**：Python 的默认参数在**函数定义时**求值一次，之后所有调用**共享同一个对象**。

正确写法 —— 在函数内部创建：

```python
def bloom_distribution(count: int, levels: list[str]) -> dict[str, int]:
    if count <= 0 or not levels:
        return {}
    base, extra = divmod(count, len(levels))
    out: dict[str, int] = {}
    ...
```

**【原理】`None` 是不可变的哨兵值。** 用 `None` 当默认值，在函数体内再创建真正的容器，
就避开了共享。**这条规则适用于 `list` / `dict` / `set` 以及任何自定义可变对象。**

---

## 5. 推导式与集合运算

### 三种推导式

```python
# 列表推导（带条件过滤）
usable = [(i, c["embedding"]) for i, c in enumerate(chunks) if c["has_vec"]]

# 集合推导（自动去重）
return {c["id"] for c in picked if c["id"] is not None}

# 字典推导
module_by_qid = {q.id: q.module for q in qs}
```

【原理】推导式比 `for` + `append` 更快（CPython 有专门优化）也更短。
但**嵌套超过两层就该换回 for 循环** —— 可读性优先。

### 集合运算（判分里到处都是）

```python
correct_set = set(json.loads(q.answer))
selected_set = set(selected)
is_correct = selected_set == correct_set        # 相等
partial = correct_set & selected_set            # 交集：至少选对一个
```

| 运算符 | 含义 | `{1,2,3}` 与 `{2,3,4}` |
|---|---|---|
| `&` | 交集 | `{2,3}` |
| `\|` | 并集 | `{1,2,3,4}` |
| `-` | 差集 | `{1}` |
| `<=` | 子集 | `False` |
| `==` | 相等 | `False` |

`validation.py` 里用 `<=` 一行表达完整语义：

```python
if not set(answer) <= set(keys):
    errors.append(f"答案 {answer} 必须命中选项键 {keys}")
```

「answer 是 keys 的子集」= 答案里不能出现不存在的选项键。

---

## 6. 闭包与嵌套函数

```python
def generate_by_scope(db, candidate_id, scope, ...):
    def progress(done: int, total: int) -> None:
        if on_progress:
            on_progress(done, total)

    def emit(type_: str, text: str, detail=None) -> None:
        if on_event:
            on_event(type_, text, detail)

    ...
    emit("retrieve", f"已检索到 {len(chunks)} 个相关片段", slice_previews(chunks))
```

`progress` 和 `emit` 是**闭包** —— 它们「记住」了外层的 `on_progress` / `on_event`。

**为什么这样写？** 主流程里有 20 多处需要「发个通知」。如果每次都写：

```python
if on_event:
    on_event("retrieve", f"已检索到 {len(chunks)} 个相关片段", slice_previews(chunks))
```

那 `if on_event:` 这个判空会重复 20 遍。包一层 `emit()` 后，主流程全是 `emit("retrieve", ...)`。

**【原理】给噪音起个名字。** 这是重构的基本手法之一，用闭包实现最简单（不必传参数）。

---

## 7. `**kwargs`：参数透传

```python
def _add_turn(db, s, role: str, turn_type: str, content: str, **kw) -> ChatTurn:
    seq = (db.query(ChatTurn).filter(ChatTurn.session_id == s.id).count() or 0) + 1
    t = ChatTurn(session_id=s.id, seq=seq, role=role, turn_type=turn_type, content=content, **kw)
```

调用方：

```python
fb = _add_turn(
    db, s, "ai", "feedback", str(payload.get("feedback", "")),
    score=score,
    points_hit=json.dumps(hit, ensure_ascii=False),
    points_missed=json.dumps(missed, ensure_ascii=False),
    ...
)
```

`**kw` 把「任意关键字参数」打包成 dict，再 `**kw` 展开传给 `ChatTurn(...)`。
作用是**透传参数** —— `_add_turn` 不需要知道有哪些字段。

**【原理】`*args` 收位置参数，`**kwargs` 收关键字参数。**
⚠️ 代价是**失去类型检查**。只在「包装/转发」场景用，核心业务逻辑尽量避免。

---

## 8. 异常处理的分级

### 捕获具体异常 + 翻译成人话

```python
try:
    text = transcribe(audio, ext)
except RuntimeError as e:
    print(f"[chat/voice] ASR failed: {e}")
    raise HTTPException(503, "语音识别暂时不可用，请改用文字输入")
```

两个要点：

1. **捕获具体异常**（`RuntimeError`）而不是 `except Exception`
2. **技术错误翻译成用户能懂的话** —— 用户看不懂 `RuntimeError`，
   但前端能直接展示「语音识别暂时不可用」

### 宽泛捕获必须写清理由

```python
        for attempt in range(3):
            try:
                return self._do_chat(prompt, timeout=timeout)
            except Exception as e:  # noqa: BLE001 — 重试耗尽后才降级
                last_err = e
```

这里用了宽泛捕获，但**注释说明了为什么可以**（这是重试链路的最后一层，
任何异常都要吃掉并降级）。`# noqa: BLE001` 是给 linter 的豁免标记。

**【原理】宽泛捕获不是不能写，而是必须写清楚理由。** 没有理由的
`except Exception: pass` 是隐藏 bug 的最佳方式。

---

## 9. 上下文管理器（`with`）

```python
try:
    with open(tmp, "wb") as f:
        shutil.copyfileobj(file.file, f)
    ...
finally:
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except Exception:
        pass
```

`with` 保证文件**一定被关闭**（即使中间抛异常）。

数据库会话是同一个模式（因为 SQLAlchemy Session 不实现上下文管理器协议，
所以手写 try/finally）：

```python
db = SessionLocal()
try:
    ...
finally:
    db.close()
```

**【原理】RAII / 资源获取即初始化** —— 资源的释放绑定在作用域退出上，
不依赖程序员记得写 `close()`。

---

## 10. 速查表：用得最多的小技巧

| 语法 | 项目里的例子 | 说明 |
|---|---|---|
| f-string | `f"已检索到 {len(chunks)} 个相关片段"` | 字符串插值，优于 `%` 和 `.format()` |
| `dict.get` 默认值 | `fused.get(ci, 0.0)` | 不存在返回默认值，不抛 `KeyError` |
| `next(gen, default)` | `next((t for t in reversed(turns) if t.turn_type == "ask"), None)` | 找第一个满足条件的，没有则 `None` |
| `or` 兜底 | `(db.query(...).count() or 0) + 1` | `None or 0` → `0` |
| `enumerate` | `for i, c in enumerate(chunks)` | 同时拿下标和值 |
| `reversed` | `reversed(turns)` | 反向迭代，不复制列表 |
| `isinstance` | `isinstance(v, Enum)` | 类型判断（比 `type() ==` 好，支持继承） |
| `getattr` 带默认 | `getattr(q.type, "value", q.type)` | 有属性就取，没有就返回对象本身 |
| `global` | `global _FAKE_Q_IDX` | 函数内改模块级变量必须声明 |
| `if __name__ == "__main__":` | `import_questions.py` 末尾 | 只有直接运行才执行 |
| 相对导入 | `from ..config import settings` | `..` = 上一级包 |
| `dict.fromkeys` 去重保序 | `list(dict.fromkeys(sizes))` | 比 set 多了「保序」 |
| `divmod` | `divmod(count, len(levels))` | 一次拿到商和余数 |

### `getattr` 那个例子的展开

```python
qtype = str(getattr(q.type, "value", q.type) or "single")
```

原意是「如果是 Enum 就取 `.value`，否则用原值；都没有则用 `"single"`」。

⚠️ **可读性提醒**：这行同时做了三件事（取属性、兜底、转字符串），
**适合底层工具函数，不适合业务代码**。业务代码里应该展开：

```python
raw = q.type if q.type is not None else "single"
qtype = str(raw.value) if isinstance(raw, Enum) else str(raw)
```

**「能写一行」不等于「该写一行」。** 这是初学者最容易走偏的地方。

---

## 11. 本节小结

| 知识点 | 一句话记住 |
|---|---|
| 类型注解 | 给人和工具看，运行时不管 |
| dataclass | 把「打错字静默失败」变成开发期报错 |
| `Enum(str, Enum)` | 既能当枚举，又能当字符串 |
| 可变默认参数 | **永远用 `None` 做默认值**，函数体内再创建 |
| 集合运算 | `&` 交集判「半对」，`<=` 子集判合法性 |
| 闭包 | 给噪音起个名字 |
| 异常分级 | 捕具体异常；宽泛捕获必须写理由 |
| `with` / try-finally | 资源释放不靠人记得 |

---

## 12. 检索练习

**Q1.** `def f(items=[])` 和 `def f(items=None)` 的区别是什么？

**Q2.** `Module.PROFESSIONAL_IDEA == "职业理念"` 为什么是 `True`？

**Q3.** `set(a) & set(b)` 和 `set(a) <= set(b)` 分别表达什么语义？

**Q4.** 为什么闭包能"记住"外层的变量？

**Q5.** `except Exception: pass` 什么时候可以接受？

<details>
<summary>参考答案</summary>

**A1.** `items=[]` 的列表在**函数定义时创建一次**，所有调用共享同一个对象，追加会累积。`items=None` 每次调用都是新的 `None`，函数体内再 `items = items or []` 创建独立容器。

**A2.** 因为 `Module` 同时继承了 `str`。`Module.PROFESSIONAL_IDEA` 本身**就是**一个字符串对象（值为 `"职业理念"`），所以与普通字符串比较相等。这也让它能直接 `json.dumps` 和存数据库。

**A3.** `&` 是交集 —— 至少有一个共同元素（判分里的「半对」）。`<=` 是子集 —— a 的所有元素都在 b 里（校验「答案必须都是合法选项键」）。

**A4.** Python 的函数对象持有对其**定义时所在作用域**的引用（`__closure__` 里的 cell）。只要闭包函数还活着，被引用的外层变量就不会被回收。

**A5.** 只在两种情况：① 这是最后一道防线，任何异常都必须吃掉以保证主流程不崩（如重试链路的末尾）；② 明确不需要处理，且**写了注释说明原因**。没有注释的 `except: pass` 一律视为 bug。

</details>

---

## 13. 延伸阅读

- [Python 官方教程（中文）](https://docs.python.org/zh-cn/3/tutorial/) —— 第 4 章「更多控制流工具」讲了闭包与参数传递
- [PEP 8 代码风格](https://peps.python.org/pep-0008/)
- **《Fluent Python》(Luciano Ramalho, O'Reilly)** —— 第二版覆盖 3.10。
  第 2 章讲序列、第 7 章讲函数与闭包、第 8 章讲类型注解。**读完这本，本节所有内容都会了然。**

---

**上一节** → [第 03 节 · AI 模拟答与事件溯源](./03-AI模拟答与事件溯源.md)
**下一节** → [第 05 节 · Python 进阶](./05-Python进阶.md)
