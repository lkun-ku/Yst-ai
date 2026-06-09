"""给**已有的** dev/test SQLite 库补上模型演进的差异（dev 专用，生产走 Alembic）。

## 为什么需要它（2026-06-15 真机踩到，两个症状都极具误导性）

`app/db.py:init_db()` 只做 `Base.metadata.create_all()` —— 它**只建缺失的表，
既不给已有的表加列，也不改已有列的约束**。于是"新模型 + 旧 dev.db"的表现是：

| 差异 | 症状 | 为什么难查 |
| --- | --- | --- |
| **缺列** | `no such column: documents.is_official` —— 闯关 / 出题 / 问答三条主链路的查询直接报错 | 报的是底层 SQLite 错误，看起来像"代码写错了"，其实是**本机库没跟上** |
| **约束不一致** | `NOT NULL constraint failed: documents.candidate_id` —— **官方语料一条都插不进去** | 报的是写入失败，看起来像"插入逻辑写错了"；实际是旧库里那一列还带着 `NOT NULL` |

`pytest` 永远发现不了这两类：测试用的是**按运行唯一的新库**（`conftest.py` 里那串踩坑注释
写的正是这件事），新库 `create_all()` 出来列与约束永远是对的。

## 两条必须遵守的规则

1. **只补列 + 只放宽约束，不改类型、不删列、不动数据** —— 这是"让本机库跟上模型"的最小动作。
   - 布尔列**必须回填**：SQLite 给已有行填的是 NULL，而检索条件写的是 `is_official IS FALSE`
     —— NULL 不匹配任何一边，于是**用户的个人资料会集体消失**。补列后紧跟一条 UPDATE。
   - 只处理「模型可空、库里 NOT NULL」这个**危险方向**；反过来（模型 NOT NULL、库里可空）
     不会让现有代码崩，不动它。
2. **先备份、再动手**：SQLite 改约束必须**重建表**（改名 → 按模型建新表 → 拷数据 → 删旧表），
   这是有数据风险的操作，所以脚本第一步先复制一份 `.bak`。

跨库拒绝：非 sqlite 的 URL 一律报错退出（生产用 `alembic upgrade head`）。

用法（在 backend/ 下）：

    python scripts/repair_dev_schema.py --dry-run   # 只看要补什么
    python scripts/repair_dev_schema.py             # 真的修（先备份）
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import sqlite3
import sys

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402

#: 模型类型 → SQLite 列类型。缺的列一律**可空**（新列加 NOT NULL 会让已有行违约）。
_TYPE_MAP = {
    "Boolean": "BOOLEAN",
    "Integer": "INTEGER",
    "BigInteger": "INTEGER",
    "Float": "FLOAT",
    "Numeric": "NUMERIC",
    "DateTime": "DATETIME",
    "Date": "DATE",
    "Text": "TEXT",
    "String": "VARCHAR",
    "Enum": "VARCHAR",
    "JSON": "JSON",
}

#: 布尔列补列时的默认值 —— 见模块注释第 1 条：不回填会让个人资料从检索里消失。
_BOOL_DEFAULT = "0"


def _sqlite_path() -> str:
    url = settings.database_url
    if not url.startswith("sqlite"):
        raise SystemExit(
            f"只修 dev/test 的 SQLite 库（生产请用 `alembic upgrade head`）：{url}"
        )
    return url.split("///", 1)[-1].lstrip("./")


def _tables(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]


def plan(db_path: str) -> list[tuple[str, str, str, str | None]]:
    """要补的列 → [(表, 列, SQL 类型, 默认值或 None)]。**纯读，不改库。**"""
    con = sqlite3.connect(db_path)
    try:
        tables = _tables(con)
        out: list[tuple[str, str, str, str | None]] = []
        for tname, table in Base.metadata.tables.items():
            if tname not in tables:
                continue  # 缺整表交给 create_all，不在这里处理
            have = {r[1] for r in con.execute(f"PRAGMA table_info({tname})")}
            for col in table.columns:
                if col.name in have:
                    continue
                sql_type = _TYPE_MAP.get(type(col.type).__name__, "VARCHAR")
                default = _BOOL_DEFAULT if sql_type == "BOOLEAN" else None
                out.append((tname, col.name, sql_type, default))
        return out
    finally:
        con.close()


def constraint_plan(db_path: str) -> list[tuple[str, list[str]]]:
    """要重建的表 → [(表, [模型可空但库里 NOT NULL 的列])]。**纯读，不改库。**"""
    con = sqlite3.connect(db_path)
    try:
        tables = _tables(con)
        out: list[tuple[str, list[str]]] = []
        for tname, table in Base.metadata.tables.items():
            if tname not in tables:
                continue
            # PRAGMA 结果：cid, name, type, notnull, dflt_value, pk
            info = {r[1]: r for r in con.execute(f"PRAGMA table_info({tname})")}
            bad = [
                c.name
                for c in table.columns
                if c.name in info and info[c.name][3] == 1 and c.nullable
            ]
            if bad:
                out.append((tname, bad))
        return out
    finally:
        con.close()


def apply_columns(db_path: str, items: list[tuple[str, str, str, str | None]]) -> list[str]:
    """执行补列 + 布尔回填，返回做过的事（供打印与测试断言）。"""
    done: list[str] = []
    con = sqlite3.connect(db_path)
    try:
        for tname, col, sql_type, default in items:
            ddl = f"ALTER TABLE {tname} ADD COLUMN {col} {sql_type}"
            if default is not None:
                ddl += f" DEFAULT {default}"
            con.execute(ddl)
            done.append(f"ALTER {tname}.{col} {sql_type}")
            if default is not None:
                cur = con.execute(f"UPDATE {tname} SET {col} = {default} WHERE {col} IS NULL")
                done.append(f"  回填 {tname}.{col} ← {default}（{cur.rowcount} 行）")
        con.commit()
    finally:
        con.close()
    return done


def apply_rebuilds(db_path: str, items: list[tuple[str, list[str]]]) -> list[str]:
    """按模型重建这些表（保留数据）。

    ⚠️ **必须先删旧索引**：SQLite 的 `ALTER TABLE ... RENAME TO` 会把索引**定义**跟着改到
    新名字上，但索引**名字不变** —— 于是"按模型建新表"时同名索引会撞车（`index ... already exists`）。
    先删掉、让它随最终 `DROP TABLE` 一起消失。
    """
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    done: list[str] = []
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for tname, bad in items:
            table = Base.metadata.tables[tname]
            old_cols = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({tname})")}
            tmp = f"{tname}__old"
            conn.exec_driver_sql(f"ALTER TABLE {tname} RENAME TO {tmp}")
            for (idx_name,) in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index' "
                f"AND tbl_name='{tmp}' AND name NOT LIKE 'sqlite_%'"
            ):
                conn.exec_driver_sql(f"DROP INDEX {idx_name}")
            table.create(conn)
            shared = [c.name for c in table.columns if c.name in old_cols]
            collist = ", ".join(shared)
            conn.exec_driver_sql(
                f"INSERT INTO {tname} ({collist}) SELECT {collist} FROM {tmp}"
            )
            kept = conn.exec_driver_sql(f"SELECT COUNT(*) FROM {tname}").scalar()
            conn.exec_driver_sql(f"DROP TABLE {tmp}")
            done.append(f"重建 {tname}（取消 NOT NULL：{'、'.join(bad)}）→ 保留 {kept} 行")
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="补齐 dev SQLite 库与模型的差异")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不改库")
    args = ap.parse_args(argv)

    db_path = _sqlite_path()
    if not pathlib.Path(db_path).exists():
        print(f"库不存在（新库会在启动时 create_all 建全）：{db_path}")
        return 0

    cols = plan(db_path)
    rebuilds = constraint_plan(db_path)
    if not cols and not rebuilds:
        print(f"✅ {db_path} 与模型一致，无需修复")
        return 0

    print(f"库：{db_path}")
    if cols:
        print(f"缺 {len(cols)} 列：")
        for tname, col, sql_type, default in cols:
            tail = f"（默认 {default} 并回填）" if default else ""
            print(f"  - {tname}.{col} {sql_type}{tail}")
    if rebuilds:
        print(f"需重建 {len(rebuilds)} 张表（模型可空、库里 NOT NULL）：")
        for tname, bad in rebuilds:
            print(f"  - {tname}：{'、'.join(bad)}")
    if args.dry_run:
        print("\n（--dry-run：未改库）")
        return 0

    bak = f"{db_path}.bak"
    shutil.copyfile(db_path, bak)
    print(f"\n已备份 → {bak}")

    for msg in apply_columns(db_path, cols):
        print(f"  {msg}")
    for msg in apply_rebuilds(db_path, rebuilds):
        print(f"  {msg}")

    left_c, left_r = plan(db_path), constraint_plan(db_path)
    print(f"\n✅ 修复完成（剩缺列 {len(left_c)} / 待重建 {len(left_r)}）。**重启服务**后生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
