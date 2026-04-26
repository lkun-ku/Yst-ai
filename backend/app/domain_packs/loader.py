"""领域包（Domain Pack）：加载与校验。

**设计主张**：科目差异是**数据**，不是代码分支 —— 模块、权重、题型、版本全在 `pack.yaml`，
加科目时不需要动这些逻辑。背景与取舍见 `docs/改造计划.md` §1、ADR-0012。

**职责边界（避免「两份真相」）**：

| 内容 | 权威位置 | 理由 |
| --- | --- | --- |
| 身份：枚举成员、科目序号、官方名称、覆盖学段、试卷代码 | `app/models.py`（`Subject` / `SUBJECT_META` / `PAPER_CODES`） | 枚举成员必须存在于代码，`Question.subject` 才能在类型与迁移上受控 |
| 内容：模块与权重、题型、考纲版本 | `pack.yaml` | 会随考纲修订而变，且需要写注释 |

`pack.yaml` **不重复声明身份元数据** —— 两处都写就会出现「改了其中一份、没有任何报错」的漂移。

四条不变量（不满足即抛 `DomainPackError`，**不做「尽量修正」** —— 配置错必须立刻可见）：
1. 目录名 == `pack.yaml` 的 `key`。否则 `Subject.value` 与目录脱节，
   而「枚举值即目录名」正是新口径的核心便利（`Scope.subject` 可直接当目录名用）。
2. `key` 必须在 `Subject` 枚举里。否则会出现「有包、无科目」的幽灵包：
   目录能加载，但任何按 `Subject` 过滤的逻辑都看不到它。
3. `question_types` 的每一项都必须是 `QuestionType` 的成员名。
   写错一个（如 `materials`）会让组卷静默漏掉该题型。
4. **声明了权重的**包，模块权重之和必须为 `1.0`（容差 1e-3）。
   只要有一个模块没写 `weight`，本包即整体视为「未标权重」，跳过求和校验 ——
   不混合、不补默认值、不用平均值兜底。

`missing_packs()` 报告「官方有、本仓未实现」的槽位，让缺口**可查**，
避免后来者把「未实现」误读成「官方不存在」。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from ..models import (
    OFFICIAL_SLOTS,
    PAPER_CODES,
    SUBJECT_META,
    Module,
    QuestionType,
    Stage,
    Subject,
    missing_packs as _official_gaps,
)

PACKS_DIR = Path(__file__).resolve().parent

#: 权重求和的容差。0.15 + 0.15 + 0.10 + 0.12 + 0.48 在二进制浮点下不精确等于 1.0。
WEIGHT_SUM_TOLERANCE = 1e-3


class DomainPackError(ValueError):
    """领域包配置错误。启动期抛出，不带兜底 —— 配置错要立刻可见，而不是静默用默认值。"""


@dataclass(frozen=True)
class PackModule:
    """包内的一个模块。

    `enum` 为 None 表示「该模块尚未进入 `Module` 枚举」—— 目前只有科目二属于这种情况
    （`Module` 是科目一专属的五个模块，直接并入会污染科目一的五维统计口径）。
    详见 ADR-0012 的「已知边界」。
    """

    name: str
    enum: Module | None = None
    weight: float | None = None  # 官方未公布时为 None（不猜、不兜底）


@dataclass(frozen=True)
class Pack:
    """一个领域包的完整内容（身份元数据由 `models` 注入，不来自 YAML）。"""

    key: str
    subject: Subject
    no: int
    name: str
    stages: tuple[Stage, ...]
    paper_codes: dict[Stage, str]
    modules: tuple[PackModule, ...]
    question_types: tuple[str, ...]
    version: str
    source: Path

    @property
    def has_weights(self) -> bool:
        """本包是否声明了完整权重（决定要不要做求和校验）。"""
        return bool(self.modules) and all(m.weight is not None for m in self.modules)

    def weight_sum(self) -> float:
        return round(sum(m.weight or 0.0 for m in self.modules), 6)


# ---------------- 解析 ----------------

def _parse_module(raw: object, *, where: str) -> PackModule:
    if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
        raise DomainPackError(f"{where}：模块项必须是含非空 name 的映射，收到 {raw!r}")

    enum_name = raw.get("enum")
    enum: Module | None = None
    if enum_name:
        enum = next((m for m in Module if str(enum_name).strip() in (m.name, m.value)), None)
        if enum is None:
            raise DomainPackError(
                f"{where}：模块 enum={enum_name!r} 不是 Module 成员（应为 {[m.name for m in Module]}）"
            )

    weight = raw.get("weight")
    if weight is not None:
        try:
            weight = float(weight)
        except (TypeError, ValueError) as exc:
            raise DomainPackError(f"{where}：模块权重 {weight!r} 不是数字") from exc
        if not 0.0 < weight <= 1.0:
            raise DomainPackError(f"{where}：模块权重应在 (0, 1] 内，收到 {weight}")

    return PackModule(name=str(raw["name"]).strip(), enum=enum, weight=weight)


def load_pack(path: Path) -> Pack:
    """加载并校验单个领域包目录（`path` 为包目录，读取其下的 `pack.yaml`）。"""
    yaml_path = path / "pack.yaml"
    if not yaml_path.is_file():
        raise DomainPackError(f"{path.name}：缺少 pack.yaml（{yaml_path}）")

    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise DomainPackError(f"{path.name}：pack.yaml 顶层必须是映射")

    where = f"领域包 {path.name}"
    key = str(raw.get("key") or "").strip()

    # 不变量 1：目录名 == key
    if key != path.name:
        raise DomainPackError(f"{where}：key={key!r} 与目录名不一致（不变量 1）")

    # 不变量 2：key 必须是 Subject 成员
    subject = next((s for s in Subject if s.value == key), None)
    if subject is None:
        raise DomainPackError(
            f"{where}：key 不在 Subject 枚举中（不变量 2；当前枚举：{[s.value for s in Subject]}）"
        )

    # 身份元数据从 models 注入 —— 不在 YAML 里重复声明，避免两份真相
    meta = SUBJECT_META[subject]

    modules = tuple(_parse_module(m, where=f"{where} modules") for m in (raw.get("modules") or []))
    if not modules:
        raise DomainPackError(f"{where}：modules 不能为空")

    question_types = tuple(str(t).strip() for t in (raw.get("question_types") or []))
    # 不变量 3：题型必须是 QuestionType 成员名
    valid_types = {t.name for t in QuestionType}
    unknown = [t for t in question_types if t not in valid_types]
    if unknown:
        raise DomainPackError(
            f"{where}：题型 {unknown} 不是 QuestionType 成员（不变量 3；合法值：{sorted(valid_types)}）"
        )

    pack = Pack(
        key=key,
        subject=subject,
        no=meta.no,
        name=meta.name,
        stages=meta.stages,
        paper_codes=dict(PAPER_CODES.get(subject, {})),
        modules=modules,
        question_types=question_types,
        version=str(raw.get("version") or "").strip(),
        source=yaml_path,
    )

    # 不变量 4：声明了权重就必须和为 1.0
    if pack.has_weights and abs(pack.weight_sum() - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise DomainPackError(f"{where}：模块权重之和为 {pack.weight_sum()}，应为 1.0（不变量 4）")

    return pack


# ---------------- 加载 ----------------

@lru_cache(maxsize=1)
def load_all() -> dict[str, Pack]:
    """加载 `domain_packs/` 下的全部领域包（按 key 索引）。结果缓存，测试可 `cache_clear()`。"""
    packs: dict[str, Pack] = {}
    for child in sorted(PACKS_DIR.iterdir()):
        if not child.is_dir() or not (child / "pack.yaml").is_file():
            continue  # 跳过 __pycache__ 等非包目录
        pack = load_pack(child)
        packs[pack.key] = pack
    return packs


def pack_of(subject: Subject) -> Pack:
    """按科目包取领域包；未实现时抛错（不返回空包，避免上游拿空数据继续跑）。"""
    try:
        return load_all()[subject.value]
    except KeyError as exc:
        raise DomainPackError(
            f"科目 {subject.value} 没有对应的领域包目录；"
            f"已实现：{sorted(load_all())}，官方缺口：{missing_packs()}"
        ) from exc


def missing_packs() -> list[tuple[int, str, Stage]]:
    """官方有、本仓未实现的「科目序号 × 学段」槽位（口径在 `models.OFFICIAL_SLOTS`）。"""
    return _official_gaps()


# ---------------- 供业务层使用的便捷入口 ----------------

def module_weights(subject: Subject) -> list[tuple[Module, float]]:
    """领域包的模块权重 → `[(Module, 占比小数), ...]`，供组卷按权重分配题量。

    **权重的唯一真相在领域包**：调用方不得另抄一份 —— 两份权重并存时，
    改了其中一处就会出现「卷面结构与考纲不符」，而且不会有任何报错。

    raises:
        DomainPackError：本包未声明权重（官方未公布），或模块尚未映射到 `Module`。
    """
    pack = pack_of(subject)
    if not pack.has_weights:
        raise DomainPackError(
            f"{pack.key}：模块权重的唯一真相应在领域包，但本包未声明完整权重（官方未公布）。"
            f"请勿在代码里补一份默认值。"
        )
    missing_enum = [m.name for m in pack.modules if m.enum is None]
    if missing_enum:
        raise DomainPackError(
            f"{pack.key}：模块 {missing_enum} 尚未映射到 Module，无法用于组卷（见 ADR-0012）。"
        )
    return [(m.enum, float(m.weight)) for m in pack.modules if m.enum is not None]  # type: ignore[misc]


def official_slots() -> tuple[tuple[int, str, Stage], ...]:
    """官方「科目序号 × 学段」全表（含未实现），供启动自检与文档生成。"""
    return OFFICIAL_SLOTS
