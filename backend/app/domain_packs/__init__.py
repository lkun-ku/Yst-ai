"""领域包（Domain Pack）：把「科目 × 学段」的差异表达为**数据**，而不是代码分支。

对外只暴露 loader 的公共 API；调用方一律：

    from ..domain_packs import load_all, module_weights, missing_packs

**不要**在业务代码里直接读 `pack.yaml` —— 校验（三条不变量）在 loader 里，
绕过它就绕过了「配置错立刻报错」这条保证。
"""

from .loader import (
    PACKS_DIR,
    DomainPackError,
    Pack,
    PackModule,
    load_all,
    load_pack,
    missing_packs,
    module_weights,
    pack_of,
)

__all__ = [
    "PACKS_DIR",
    "DomainPackError",
    "Pack",
    "PackModule",
    "load_all",
    "load_pack",
    "missing_packs",
    "module_weights",
    "pack_of",
]
