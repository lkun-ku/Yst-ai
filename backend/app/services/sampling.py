"""随机抽样的**唯一写法** —— 别处再抄一遍就会漏（这是踩出来的）。

## 为什么单列一个模块

2026-06-15 核实「性能欠账」时发现：`sessions._select_by_weight` 与 `daily._get_or_create_task`
**原本是同一个写法** —— `.all()` 把全表拉进内存再 `random.shuffle`。
`sessions.py` 那处按计划改成了 SQL 层随机抽样，而 **`daily.py` 那处漏了**：
同一个缺陷，**改了一处、留着另一处**。

所以把"只回需要的行"的写法收口到这里 —— 下次要随机抽样，**调用它，而不是再写一遍 `.all()`**。

## 取舍（写在这里，免得将来重新讨论）

`ORDER BY RANDOM()` 在 PG 上是**全表扫描 + 排序**。题库到十万级应换成
「按 id 随机区间取行」或维护随机排序列；当前量级（万级以内）不值得上那个复杂度 ——
但**必须只回 n 行**：`.all()` 的代价随题库增长线性放大，而随机抽样的代价不该。

## 语义说明

- `exclude_ids` 为空时**不加过滤**（`NOT IN ()` 在各方言下行为不一，也没必要）；
- `n <= 0` 直接返回空，不发查询（返回 [] 而不是让调用方拿到全表）。
"""

from __future__ import annotations

from sqlalchemy import func


def random_query(db, model, filters=None, n: int = 1, exclude_ids=None):
    """构造"随机取 n 行"的查询（**不执行**）。

    单独拆出来是为了能被测试断言：编译出的 SQL 里**必须带 `LIMIT`** ——
    否则"改造"可能只是把 `.all()` 挪了个位置，看起来变了、代价没变。
    """
    q = db.query(model)
    if filters:
        q = q.filter(*filters)
    if exclude_ids:
        q = q.filter(model.id.notin_(list(exclude_ids)))
    return q.order_by(func.random()).limit(max(0, n))


def random_rows(db, model, filters=None, n: int = 1, exclude_ids=None) -> list:
    """从 `model` 里随机取至多 `n` 行，可排除若干 id。**只回 n 行，不全表拉取。**"""
    if n <= 0:
        return []
    return random_query(db, model, filters, n, exclude_ids).all()
