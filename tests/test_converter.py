# -*- coding: utf-8 -*-
"""
test_converter.py —— caj_converter 自检脚本（纯标准库）

构造最小 PDF，并打包成 CAJ / KDH / 原生 PDF / HN 等容器，
验证提取、解密、Catalog/Pages 补全、xref 重建与批量流程。

运行：
    python tests/test_converter.py
"""

import os
import struct
import sys

# 允许从项目根目录导入 caj_converter
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import caj_converter as cc  # noqa: E402

SAMPLE_DIR = os.path.join(HERE, "samples")
os.makedirs(SAMPLE_DIR, exist_ok=True)


# --------------------------------------------------------------------------- #
# 构造最小 PDF 片段
# --------------------------------------------------------------------------- #

def content_obj(num: int) -> bytes:
    stream = b"BT /F1 24 Tf 72 720 Td (Hello CAJ) Tj ET"
    return (
        f"{num} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
        + stream
        + b"\nendstream\nendobj\n"
    )


def page_obj(num: int, parent_num: int, content_num: int) -> bytes:
    return (
        f"{num} 0 obj\n<< /Type /Page /Parent {parent_num} 0 R "
        f"/MediaBox [0 0 612 792] /Resources << >> /Contents {content_num} 0 R >>\nendobj\n"
    ).encode("latin-1")


def inner_two_pages() -> bytes:
    """两个 Page（编号 2、5），父对象 4/99 故意缺失，模拟 CNKI 剥离 Pages/Catalog。"""
    parts = []
    parts.append(content_obj(3))
    parts.append(page_obj(2, 4, 3))
    parts.append(content_obj(6))
    parts.append(page_obj(5, 99, 6))
    return b"".join(parts)


def build_full_pdf() -> bytes:
    """结构完整的最小 PDF（含 xref），用于 KDH 与原生 PDF 测试。"""
    objs = {}
    objs[1] = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    objs[2] = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    objs[3] = page_obj(3, 2, 4)
    objs[4] = content_obj(4)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for n in sorted(objs):
        offsets[n] = len(out)
        out += objs[n]
    xref = len(out)
    size = max(objs) + 1
    out += f"xref\n0 {size}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for n in range(1, size):
        out += f"{offsets[n]:010d} 00000 n \n".encode("latin-1")
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin-1")
    return bytes(out)


# --------------------------------------------------------------------------- #
# 容器构造
# --------------------------------------------------------------------------- #

def build_caj_container(inner: bytes, page_count: int) -> bytes:
    """模拟 CAJ：0x10 页数、0x14 指针 -> 指针处存 PDF 起始偏移。"""
    header = bytearray(0x200)
    header[0:4] = b"CAJ\x00"
    struct.pack_into("<i", header, 0x10, page_count)
    pointer = 0x40
    struct.pack_into("<i", header, 0x14, pointer)
    pdf_start = 0x200
    struct.pack_into("<i", header, pointer, pdf_start)
    return bytes(header) + inner


def xor_encrypt(data: bytes) -> bytes:
    key = cc.KDH_PASSPHRASE
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def build_kdh_container(full_pdf: bytes) -> bytes:
    head = bytearray(b"KDH ")
    head += b"\x00" * (254 - len(head))
    assert len(head) == 254
    return bytes(head) + xor_encrypt(full_pdf)


def build_hn_container() -> bytes:
    data = bytearray(b"HN\xc8\x00")
    data += b"\x00" * 256
    return bytes(data)


# --------------------------------------------------------------------------- #
# 校验工具
# --------------------------------------------------------------------------- #

def assert_valid_pdf(pdf: bytes, expect_pages: int) -> None:
    assert pdf.startswith(b"%PDF-"), "缺少 %PDF 头"
    tail = pdf.rstrip(b"\r\n")
    assert tail.endswith(b"%%EOF"), "缺少 %%EOF"
    assert not tail.endswith(b"%%%EOF"), "EOF 标记的百分号数量错误（应为两个）"

    # startxref 数字行后必须紧跟 %%EOF，且指向 xref 表
    import re
    sx = re.search(rb"startxref\r?\n(\d+)\r?\n%%EOF", pdf)
    assert sx, "startxref 格式不正确"
    xref_pos = int(sx.group(1))
    assert pdf[xref_pos:xref_pos + 4] == b"xref", "startxref 未指向 xref 表"

    # trailer 含 /Root
    assert b"/Root" in pdf and b"/Catalog" in pdf, "缺少 Catalog/Root"

    # 页数
    import re
    found = len(re.findall(rb"/Type\s*/Page\b", pdf))
    assert found == expect_pages, f"页数不符：期望 {expect_pages}，实际 {found}"

    # xref 声明的每个在用偏移都能定位到 "N G obj"
    objects = cc.parse_pdf_objects(pdf)
    assert objects, "重建后无法解析出对象"

    # xref 表条目与实际对象偏移一致性
    xref_block = pdf[xref_pos:]
    import re as _re
    entries = _re.findall(rb"(\d{10}) (\d{5}) n ", xref_block)
    used = [(int(off), int(gen)) for off, gen in entries]
    obj_offsets = {}
    for om in cc._RE_OBJ_HEAD.finditer(pdf):
        obj_offsets[int(om.group(1))] = om.start()
    matched = 0
    for off, gen in used:
        if off in obj_offsets.values():
            matched += 1
    assert matched == len(objects), f"xref 偏移与对象不一致（{matched}/{len(objects)}）"


def re_finditer(pattern: bytes, data: bytes):
    import re
    return re.finditer(pattern, data)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main() -> int:
    failures = []

    def check(name, fn):
        try:
            fn()
            print(f"  [PASS] {name}")
        except AssertionError as e:
            failures.append((name, str(e)))
            print(f"  [FAIL] {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failures.append((name, repr(e)))
            print(f"  [ERROR] {name}: {e!r}")

    # 1) CAJ 容器（内嵌缺 Catalog/Pages 的两页）
    caj_path = os.path.join(SAMPLE_DIR, "sample.caj")
    with open(caj_path, "wb") as f:
        f.write(build_caj_container(inner_two_pages(), 2))

    def t_caj():
        r = cc.convert_file(caj_path)
        assert r.ok, r.message
        assert r.fmt == "CAJ"
        with open(r.target, "rb") as f:
            assert_valid_pdf(f.read(), 2)
    check("CAJ 提取 + Catalog/Pages 补全 + xref 重建（2页）", t_caj)

    # 2) KDH 容器（完整 PDF 加密）
    kdh_path = os.path.join(SAMPLE_DIR, "sample.kdh")
    with open(kdh_path, "wb") as f:
        f.write(build_kdh_container(build_full_pdf()))

    def t_kdh():
        r = cc.convert_file(kdh_path)
        assert r.ok, r.message
        assert r.fmt == "KDH"
        with open(r.target, "rb") as f:
            assert_valid_pdf(f.read(), 1)
    check("KDH XOR 解密 + 重建（1页）", t_kdh)

    # 3) 原生 PDF
    pdf_path = os.path.join(SAMPLE_DIR, "sample.pdf")
    with open(pdf_path, "wb") as f:
        f.write(build_full_pdf())

    def t_pdf():
        r = cc.convert_file(pdf_path)
        assert r.ok, r.message
        with open(r.target, "rb") as f:
            assert_valid_pdf(f.read(), 1)
    check("原生 PDF 规范化重写（1页）", t_pdf)

    # 4) HN 不支持，应抛清晰错误
    nh_path = os.path.join(SAMPLE_DIR, "sample.nh")
    with open(nh_path, "wb") as f:
        f.write(build_hn_container())

    def t_hn():
        r = cc.convert_file(nh_path)
        assert not r.ok and "JBIG" in r.message, "HN 应给出不支持提示"
    check("HN/C8 明确报不支持并给出替代方案", t_hn)

    # 5) 批量（文件夹）。输出目录放在样本目录之外，避免 os.walk 递归扫描到自身产物
    out_dir = os.path.join(HERE, "batch_out")
    os.makedirs(out_dir, exist_ok=True)

    def t_batch():
        results = cc.convert_many([SAMPLE_DIR], out_dir)
        # 含 caj/kdh/pdf/nh 四个样本
        assert len(results) == 4, f"应收集4个文件，实际 {len(results)}"
        ok = [r for r in results if r.ok]
        assert len(ok) == 3, f"应成功3个，实际 {len(ok)}"
        # 三个同名源文件必须产出三个互不相同、真实存在的 PDF（不互相覆盖）
        targets = [os.path.abspath(r.target) for r in ok]
        assert len(set(targets)) == 3, f"输出路径存在覆盖：{targets}"
        for r in ok:
            assert os.path.isfile(r.target), f"输出不存在：{r.target}"
    check("文件夹批量转换（3成功 / 1不支持，同名不覆盖）", t_batch)

    print(f"\n样本目录：{SAMPLE_DIR}")
    if failures:
        print(f"\n存在 {len(failures)} 项失败。")
        return 1
    print("\n全部自检通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
