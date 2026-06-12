"""ONNX 权重下载 —— rerank 与 embedding **共用同一份实现**。

## 为什么抽成独立模块（2026-06-16）

两处各写一份下载器必然分叉，而这里的分叉症状特别难归因：**一方能下、一方下不动**，
表现只是"某个模型总是不可用"，看不出是网络、是路径、还是代码。

## 站点顺序由**实测吞吐**决定，不由名气决定

本机实测（各下 12 秒，2026-06-16）：

| 站点 | 吞吐 | 结论 |
| --- | --- | --- |
| `modelscope.cn`（api 下载接口） | **5223 KB/s** | 首选，311MB 约 1 分钟 |
| `hf-mirror.com` | 21 KB/s | 同一份文件要 **9 小时** —— 只作兜底 |
| `huggingface.co` | 连接超时（WinError 10060） | 直连不可达 |
| `mirror.sjtu.edu.cn` / `hf-api.gitee.com` | HTTP 404 | 该路径下没有这个仓库 |

⚠️ **原实现硬编码 `https://huggingface.co`** —— 国内不可达；换成 hf-mirror 虽能通，
但 21 KB/s 意味着"能下"与"下得完"是两件事。所以源是可配置的列表，且**默认按实测排序**。

## 两处工程细节

- **`.part` + 断点续传**：写到临时文件再改名，中断不会留下半截文件被误判为"已下载"；
  已经下了一半的，下次带 `Range` 续传（311MB 重来一次很贵）。
  若服务器忽略 `Range` 返回 200（整份），则**从头写**而不是追加 —— 追加会得到损坏文件。
- **站点级降级**：一个文件在某站点失败就换下一个；全失败才跳过并打印原因，不抛异常
  （调用方仍能拿到"缺哪个文件"，而不是一个栈）。
"""

from __future__ import annotations

import os
import pathlib
import urllib.request

#: `(站点名, URL 模板)`。模板里 `{repo}` 是仓库、`{path}` 是仓库内路径。
#:
#: modelscope 是**国内模型站**，用它的文件下载接口（`/api/v1/models/<repo>/repo`）取
#: HF 上的同名仓库 —— 它是镜像，所以拿到的是同一份权重。
DEFAULT_SOURCES: tuple[tuple[str, str], ...] = (
    (
        "modelscope",
        "https://www.modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={path}",
    ),
    ("hf-mirror", "https://hf-mirror.com/{repo}/resolve/main/{path}"),
    ("huggingface", "https://huggingface.co/{repo}/resolve/main/{path}"),
)

#: `HF_ENDPOINT` 是 HuggingFace 官方约定的镜像开关；用户显式指定的排在最前（HF 风格路径）。
HF_STYLE = "{base}/{repo}/resolve/main/{path}"


def resolve_sources() -> list[tuple[str, str]]:
    """本次要依次尝试的站点（去重保序）。"""
    sources: list[tuple[str, str]] = []
    env = (os.getenv("HF_ENDPOINT") or "").strip().rstrip("/")
    if env:
        sources.append(("HF_ENDPOINT", HF_STYLE.format(base=env, repo="{repo}", path="{path}")))
    for name, tpl in DEFAULT_SOURCES:
        if not any(tpl == t for _, t in sources):
            sources.append((name, tpl))
    return sources


def _download(url: str, tmp: pathlib.Path) -> int:
    """下单个文件到 `tmp`，支持断点续传。返回本次新增的字节数。"""
    resume_at = tmp.stat().st_size if tmp.exists() else 0
    headers = {"User-Agent": "curl/8"}  # 部分站点对 python-urllib 直接拒绝
    if resume_at > 0:
        headers["Range"] = f"bytes={resume_at}-"
    req = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(req, timeout=60) as resp, open(
        tmp, "ab" if resume_at else "wb"
    ) as f:
        # 请求了 Range 却拿到 200 = 服务器不支持续传、发的是**整份**体；
        # 这时若继续追加，文件会变成"半截旧数据 + 整份新数据"的损坏品。
        status = getattr(resp, "status", 200)
        if resume_at and status != 206:
            f.seek(0)
            f.truncate()
            resume_at = 0
        added = 0
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            added += len(chunk)
    return added


def fetch_files(
    repo: str,
    files: tuple[tuple[str, str], ...],
    dst: str | pathlib.Path,
    *,
    min_bytes: int = 1024,
) -> int:
    """下载 `files`（`(仓库内路径, 本地文件名)`）到 `dst`，返回本次下载的字节数。

    已存在且大于 `min_bytes` 的文件直接跳过（**幂等**：可反复跑）。
    """
    target = pathlib.Path(dst)
    target.mkdir(parents=True, exist_ok=True)
    sources = resolve_sources()
    total = 0

    for remote, local in files:
        out = target / local
        if out.exists() and out.stat().st_size > min_bytes:
            print(f"  已存在 {local}")
            continue
        tmp = target / (local + ".part")
        errors: list[str] = []
        for name, tpl in sources:
            url = tpl.format(repo=repo, path=remote)
            try:
                added = _download(url, tmp)
                if tmp.stat().st_size <= min_bytes:
                    raise OSError(f"只拿到 {tmp.stat().st_size} 字节")
                tmp.replace(out)
                total += added
                print(f"  下载完 {local}（{out.stat().st_size / 1048576:.1f} MB，来源 {name}）")
                break
            except Exception as exc:  # noqa: BLE001 — 换站点重试；全失败才报
                errors.append(f"{name}: {type(exc).__name__}")
        else:
            print(f"  ⚠️ 跳过 {local}（{len(sources)} 个站点均失败 —— {'; '.join(errors)}）")
    return total
