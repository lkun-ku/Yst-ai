"""把**扫描版** PDF 逐页 OCR 成文本（供后续结构化）。

## 为什么自建

真题 PDF 是**扫描图**：每页 `get_text()` 长度 0、图像数 1 —— `fitz`/`pdfplumber` 都抽不出文字。
本机没有 `tesseract` 二进制，但有纯 Python 的 `rapidocr-onnxruntime`（ONNX，无需外部程序）。
实测：200 dpi 下约 **10 秒/页**，中文识别质量高（与人工读图逐字对照过），
但**非零错误率** —— 所以产物必须能被抽样核对，答案键尤其不能盲信。

## 为什么必须支持续跑

全量是**小时级**任务（单份 275 页 ≈ 46 分钟）。中途网断/关机/手动中断都很正常，
不能每次从头再来 —— 已存在的 `.txt` 默认跳过（`--force` 覆盖）。

## 输出格式

    <输出目录>/<pdf名>.txt

每页之间插入 `===== page N =====` 分隔。**保留页码是刻意的**：
结构化的答案需要与题号对齐、出错时要能回到原页核对，丢了页码就没法追溯。

## 用法

    python scripts/ocr_pdf_to_text.py <pdf 或目录> <输出目录> [--pages N] [--dpi 200] [--force]

`--pages N` 只处理每份的前 N 页（**先小片段验证再放量**，别一上来就烧几小时）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_OCR = None


def _get_ocr():
    """惰性初始化：模型加载慢，且只有真要 OCR 时才需要它。"""
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR

        _OCR = RapidOCR()
    return _OCR


def _existing_pages(path: Path) -> int:
    """已 OCR 的页数（按 `===== page N =====` 标记数）。文件不存在或读不了都算 0。"""
    try:
        if not path.exists():
            return 0
        return path.read_text(encoding="utf-8", errors="ignore").count("===== page ")
    except OSError:
        return 0


def ocr_pdf(pdf: Path, out_txt: Path, *, pages: int | None = None, dpi: int = 200,
            force: bool = False) -> dict:
    """OCR 一份 PDF → 文本文件（**逐页落盘**）。返回统计（页数 / 字符数 / 耗时）。"""
    import fitz

    doc = fitz.open(pdf)
    total = doc.page_count
    limit = min(pages, total) if pages else total
    ocr = _get_ocr()
    # ⚠️ 目录必须**在循环之前**建好：每页渲染出的中间 PNG 就写在这个目录里。
    # （第一版把 mkdir 放在循环之后，于是每一份都会在第一步就失败 —— 踩过。）
    out_txt.parent.mkdir(parents=True, exist_ok=True)

    # **逐页落盘 + 按页数续跑**（第一版把整本攒在内存、最后一次性 write ——
    # 那样"可续跑"是假的：275 页要跑约 70 分钟，在第 270 页崩掉就全白跑。
    # 现在每页写完即 flush，中断后重跑会从已有的页数接着来。）
    already = 0 if force else _existing_pages(out_txt)
    if already >= limit:
        return {"pdf": pdf.name, "pages": already, "total_pages": total, "chars": 0,
                "seconds": 0.0, "resumed_from": already, "skipped": True}

    t0 = time.time()
    chars = 0
    mode = "a" if already else "w"
    with out_txt.open(mode, encoding="utf-8") as fh:
        if already:
            print(f"    {pdf.name} 续跑：已有 {already} 页，从第 {already + 1} 页继续", flush=True)
        for i in range(already, limit):
            pix = doc[i].get_pixmap(dpi=dpi)
            tmp = out_txt.with_suffix(f".p{i}.png")
            pix.save(tmp)
            try:
                res, _ = ocr(str(tmp))
            finally:
                tmp.unlink(missing_ok=True)  # 中间图不落地留存，省空间也少一份版权副本
            text = "".join(x[1] for x in (res or []))
            chars += len(text)
            fh.write(f"===== page {i + 1} =====\n{text}\n\n")
            fh.flush()  # ← 每页落盘，崩溃/断电只损失当前这一页
            if (i + 1) % 5 == 0 or i + 1 == limit:
                print(f"    {pdf.name} [{i + 1}/{limit}] 累计 {chars} 字 {time.time() - t0:.0f}s", flush=True)

    return {"pdf": pdf.name, "pages": limit, "total_pages": total, "chars": chars,
            "seconds": round(time.time() - t0, 1), "resumed_from": already}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="PDF 文件或包含 PDF 的目录")
    ap.add_argument("out", help="输出目录")
    ap.add_argument("--pages", type=int, default=None, help="每份只处理前 N 页（先小片段验证）")
    ap.add_argument("--dpi", type=int, default=200, help="渲染分辨率（默认 200）")
    ap.add_argument("--force", action="store_true", help="已存在的 .txt 也重做")
    args = ap.parse_args()

    src, out_dir = Path(args.src), Path(args.out)
    pdfs = sorted(src.glob("*.pdf")) if src.is_dir() else [src]
    if not pdfs:
        print(f"（{src} 下没有 PDF）")
        return 1

    done = skipped = 0
    for pdf in pdfs:
        target = out_dir / f"{pdf.stem}.txt"
        # ⚠️ **不再"文件存在就跳过"** —— 那会把"续跑"变成"不跑"：
        # 跑到一半中断时文件是存在的，跳过就永远补不齐后半本。
        # 是否已完成由 `ocr_pdf` 按**页数**判断（并自行从断点接着写）。
        try:
            st = ocr_pdf(pdf, target, pages=args.pages, dpi=args.dpi, force=args.force)
            if st.get("skipped"):
                print(f"  ↷ 已完成 {st['pages']} 页，跳过 {target.name}")
                skipped += 1
                continue
            resumed = f"（续跑，自第 {st['resumed_from'] + 1} 页）" if st.get("resumed_from") else ""
            print(f"  ✓ {st['pdf']} → {target.name}　{st['pages']}/{st['total_pages']} 页"
                  f"　{st['chars']} 字　{st['seconds']}s{resumed}")
            done += 1
        except Exception as e:  # noqa: BLE001 — 单份失败不该中断整批
            print(f"  ✗ {pdf.name} 失败：{type(e).__name__}: {str(e)[:120]}")
    print(f"\n完成 {done} / 跳过 {skipped} / 共 {len(pdfs)}")
    print("⚠️ OCR 有非零错误率：产物**必须抽样核对**，答案键尤其不能盲信。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
