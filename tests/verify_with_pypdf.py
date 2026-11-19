# -*- coding: utf-8 -*-
"""
verify_with_pypdf.py —— 使用第三方库 pypdf 独立验证转换产物的真实兼容性。

pypdf 仅用于本测试，不是转换器的运行依赖。
运行：
    pip install pypdf
    python tests/verify_with_pypdf.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BATCH = os.path.join(HERE, "batch_out")
SAMPLES = os.path.join(HERE, "samples")

try:
    from pypdf import PdfReader
except ImportError:
    print("请先安装 pypdf：python -m pip install pypdf")
    sys.exit(2)


def verify(path: str, expect_pages: int) -> None:
    reader = PdfReader(path)
    n = len(reader.trailer["/Root"])  # 触发根对象解析
    pages = len(reader.pages)
    assert pages == expect_pages, f"{path}: 页数 {pages} != 期望 {expect_pages}"
    # 遍历每页，确保内容对象可访问
    for i, page in enumerate(reader.pages):
        _ = page.mediabox
        _ = page.get("Resources")
    print(f"  [OK] {os.path.basename(path)}  页数={pages}  根对象可解析")


def main() -> int:
    # 批量目录：caj(2页)、kdh(1页)、pdf(1页)
    targets = sorted(
        os.path.join(BATCH, f) for f in os.listdir(BATCH) if f.endswith(".pdf")
    )
    assert len(targets) == 3, f"应有3个输出，实际 {len(targets)}"

    # 按文件大小区分：CAJ 两页的文件最大
    by_size = sorted(targets, key=lambda p: os.path.getsize(p), reverse=True)
    verify(by_size[0], 2)   # CAJ 两页
    verify(by_size[1], 1)   # 其余两个各一页
    verify(by_size[2], 1)

    print("\npypdf 独立验证全部通过：生成的 PDF 可被标准解析器正常读取。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
