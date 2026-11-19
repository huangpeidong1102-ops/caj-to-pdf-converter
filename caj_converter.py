# -*- coding: utf-8 -*-
"""
caj_converter.py —— 免费、纯本地、离线的 CAJ 家族文件转 PDF 引擎

特点：
  * 仅使用 Python 标准库，无需联网、无需安装任何第三方包；
  * 支持中国知网(CNKI)常见文件：
      - CAJ  : 最常见，本质是内嵌 PDF（外层加了 CNKI 头/尾，且常被剥离 Catalog/Pages）
      - KDH  : 简单 XOR 加密包裹的 PDF，使用固定口令 FZHMEI
      - PDF  : 本身就是 PDF，直接规范化重写
      - HN/C8: JBIG/JBIG2 图像格式（.nh 学位论文多见），本引擎暂不支持，会明确提示
  * 内置 PDF 对象扫描、Catalog/Pages 补全、xref 交叉引用表重建，
    可处理对象物理顺序错乱、缺少根对象、缺少 xref 等问题，输出标准可打开的 PDF。

命令行用法：
    python caj_converter.py 文件1.caj 文件2.kdh ...
    python caj_converter.py 某个文件夹 -o 输出目录
    python caj_converter.py paper.caj -o paper.pdf

作者：本地转换器（基于公开开源项目 caj2pdf 所披露的格式算法实现）
"""

from __future__ import annotations

import os
import re
import struct
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

# KDH 加密使用的固定口令（循环 XOR 密钥）
KDH_PASSPHRASE = b"FZHMEI"

# 支持批量识别的扩展名
SUPPORTED_INPUT_EXTS = (".caj", ".kdh", ".pdf")
UNSUPPORTED_EXTS = (".nh", ".c8", ".teb")

# 字节正则
_RE_OBJ_HEAD = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")
_RE_TYPE = re.compile(rb"/Type\s*/([A-Za-z]+)")
_RE_KIDS = re.compile(rb"/Kids\s*\[", re.DOTALL)
_RE_REF = re.compile(rb"(\d+)\s+(\d+)\s+R")
_RE_PARENT = re.compile(rb"/Parent\s+(\d+)\s+(\d+)\s+R")
_RE_CATALOG = re.compile(rb"/Catalog\b")
_RE_TRAILER_ROOT = re.compile(rb"/Root\s+(\d+)\s+(\d+)\s+R")
_RE_INFO = re.compile(rb"/Info\s+(\d+)\s+(\d+)\s+R")


# --------------------------------------------------------------------------- #
# 异常与结果
# --------------------------------------------------------------------------- #

class ConvertError(Exception):
    """转换过程中的可预期错误。"""


class UnsupportedFormat(ConvertError):
    """文件格式暂不支持（如 HN/C8 图像型）。"""


@dataclass
class ConvertResult:
    """单个文件的转换结果。"""
    source: str
    target: Optional[str] = None
    fmt: str = ""
    page_count: int = 0
    ok: bool = False
    message: str = ""


@dataclass
class _PdfObject:
    """内部表示：一个 PDF 间接对象。"""
    num: int
    gen: int
    raw: bytes                      # 从 "N G obj" 到 "endobj" 的完整原始字节
    kids: List[int] = field(default_factory=list)
    parent: Optional[int] = None
    type_name: Optional[str] = None


# --------------------------------------------------------------------------- #
# 文件类型识别
# --------------------------------------------------------------------------- #

def detect_format(raw: bytes) -> str:
    """根据文件头字节判断 CNKI 家族格式。

    返回 'CAJ' / 'KDH' / 'PDF' / 'HN' / 'C8' / 'TEB' / 'UNKNOWN'。
    """
    if len(raw) < 4:
        return "UNKNOWN"

    # 单字节签名：C8 以 0xC8 开头
    if raw[0:1] == b"\xc8":
        return "C8"

    # HN 开头两字节为 ASCII "HN"
    if raw[0:2] == b"HN":
        return "HN"

    # 4 字节签名（GB18030，内部可能含 NUL）
    try:
        sig = struct.unpack("4s", raw[0:4])[0].replace(b"\x00", b"").decode("gb18030")
    except (UnicodeDecodeError, struct.error):
        sig = ""

    if sig == "CAJ":
        return "CAJ"
    if sig == "HN":
        return "HN"
    if sig == "%PDF":
        return "PDF"
    if sig == "KDH ":
        return "KDH"
    if sig == "TEB":
        return "TEB"

    # 兜底：文件内存在 %PDF 标记，按 PDF 包裹处理
    if b"%PDF-" in raw[:4096]:
        return "PDF"
    return "UNKNOWN"


# --------------------------------------------------------------------------- #
# 各格式 -> 原始 PDF 字节提取
# --------------------------------------------------------------------------- #

def _extract_caj_pdf(raw: bytes) -> bytes:
    """从 CAJ 容器中提取内嵌的 PDF 内容（可能不含 %PDF 头、结构不完整）。"""
    pdf_start = None

    # 精确法：0x10 为页数，0x14 处存放一个指针，指针处再读一个 int 才是 PDF 起点
    try:
        if len(raw) > 0x18:
            (pointer,) = struct.unpack("<i", raw[0x14:0x18])
            if 0 <= pointer + 4 <= len(raw):
                (candidate,) = struct.unpack("<i", raw[pointer:pointer + 4])
                if 0 <= candidate < len(raw):
                    pdf_start = candidate
    except struct.error:
        pdf_start = None

    # 兜底法：扫描第一个 %PDF- 标记
    if pdf_start is None or not (0 <= pdf_start < len(raw)):
        m = re.search(rb"%PDF-", raw)
        if m:
            pdf_start = m.start()

    if pdf_start is None:
        raise ConvertError("未能在 CAJ 文件中定位到内嵌 PDF 数据。")

    # 结束位置：最后一个 endobj 之后
    last_endobj = raw.rfind(b"endobj", pdf_start)
    if last_endobj == -1:
        # 退而求其次：找 %%EOF
        eof = raw.rfind(b"%%EOF", pdf_start)
        if eof == -1:
            raise ConvertError("内嵌 PDF 数据不完整（找不到 endobj/EOF 标记）。")
        pdf_end = eof + 5
    else:
        pdf_end = last_endobj + 6

    return raw[pdf_start:pdf_end]


def _decrypt_kdh(raw: bytes) -> bytes:
    """解密 KDH：跳过前 254 字节，使用 FZHMEI 循环 XOR。"""
    body = raw[254:]
    key_len = len(KDH_PASSPHRASE)
    out = bytearray(len(body))
    for i, b in enumerate(body):
        out[i] = b ^ KDH_PASSPHRASE[i % key_len]
    data = bytes(out)

    eof = data.rfind(b"%%EOF")
    if eof == -1:
        # 没有 EOF 也继续交给重建器，但若连对象都没有会在后续报错
        return data
    return data[:eof + 5]


# --------------------------------------------------------------------------- #
# PDF 对象扫描
# --------------------------------------------------------------------------- #

def _find_matching_endobj(data: bytes, obj_start: int) -> int:
    """定位某个 obj 起点之后对应的 endobj 位置，正确跳过 stream 二进制区。

    返回 endobj 的起始索引；找不到返回 -1。

    思路：先取 obj 起点之后的第一个 endobj 作为候选边界，再在该边界之内
    判断对象本身是否为流对象（含 stream 关键字）。若是，则改用 endstream
    之后的 endobj，避免被流数据中偶然出现的 "endobj" 字节误导，同时也
    不会把后续对象的 stream 误判到当前对象上。
    """
    first_endobj = data.find(b"endobj", obj_start)
    if first_endobj == -1:
        return -1

    # 仅在当前对象到候选 endobj 的范围内查找流关键字
    region = data[obj_start:first_endobj]
    stream_m = re.search(rb"[\r\n>]stream\b", region)
    if stream_m:
        stream_kw = obj_start + stream_m.start()
        endstream = data.find(b"endstream", stream_kw)
        if endstream != -1:
            pos = data.find(b"endobj", endstream + len(b"endstream"))
            return pos
    return first_endobj


def parse_pdf_objects(data: bytes) -> Dict[int, _PdfObject]:
    """扫描 PDF 字节，返回 {对象编号: _PdfObject}。重复编号保留首次出现。"""
    objects: Dict[int, _PdfObject] = {}
    for m in _RE_OBJ_HEAD.finditer(data):
        num = int(m.group(1))
        gen = int(m.group(2))
        head = m.start()
        endobj_pos = _find_matching_endobj(data, head)
        if endobj_pos == -1:
            continue
        raw_block = data[head:endobj_pos + len(b"endobj")]

        # 类型
        type_name = None
        tm = _RE_TYPE.search(raw_block)
        if tm:
            type_name = tm.group(1).decode("latin-1")

        # Kids
        kids: List[int] = []
        if _RE_KIDS.search(raw_block):
            for ref in _RE_REF.finditer(raw_block):
                kids.append(int(ref.group(1)))

        # Parent
        parent = None
        pm = _RE_PARENT.search(raw_block)
        if pm:
            parent = int(pm.group(1))

        if num not in objects:  # 重复编号去重
            objects[num] = _PdfObject(
                num=num, gen=gen, raw=raw_block,
                kids=kids, parent=parent, type_name=type_name,
            )
    return objects


# --------------------------------------------------------------------------- #
# 结构分析与 Catalog/Pages 补全
# --------------------------------------------------------------------------- #

def _find_existing_catalog(objects: Dict[int, _PdfObject], data: bytes) -> Optional[int]:
    """寻找现有 Catalog 对象编号。"""
    for num, obj in objects.items():
        if obj.type_name == "Catalog":
            return num
        if _RE_CATALOG.search(obj.raw):
            return num
    # 再尝试从 trailer 的 /Root 找
    m = None
    for tm in _RE_TRAILER_ROOT.finditer(data):
        m = tm  # 取最后一个
    if m:
        root_num = int(m.group(1))
        if root_num in objects:
            return root_num
    return None


def _build_page_tree(objects: Dict[int, _PdfObject]) -> Tuple[int, bool]:
    """确定根 Pages 对象编号；必要时新建。

    返回 (根Pages编号, 是否新建了Pages)。
    """
    pages_nums = [n for n, o in objects.items() if o.type_name == "Pages"]
    page_nums = [n for n, o in objects.items() if o.type_name == "Page"]

    # 被其它 Pages 当作 Kids 引用的 Pages 都不是根
    referenced: set = set()
    for n in pages_nums:
        for kid in objects[n].kids:
            referenced.add(kid)

    roots = [n for n in pages_nums if n not in referenced]

    if len(roots) == 1:
        return roots[0], False

    if len(roots) > 1:
        # 多个无父 Pages：新建一个根把它们聚合
        new_num = max(objects.keys()) + 1
        kids = roots
        obj = _PdfObject(
            num=new_num, gen=0,
            raw=(_make_pages_bytes(new_num, kids, len(page_nums))),
            kids=kids, parent=None, type_name="Pages",
        )
        objects[new_num] = obj
        return new_num, True

    # 没有任何 Pages 对象：用全部 Page 新建一个
    new_num = (max(objects.keys()) + 1) if objects else 1
    kids = sorted(page_nums)
    obj = _PdfObject(
        num=new_num, gen=0,
        raw=_make_pages_bytes(new_num, kids, len(kids)),
        kids=kids, parent=None, type_name="Pages",
    )
    objects[new_num] = obj
    return new_num, True


def _make_pages_bytes(num: int, kids: List[int], count: int) -> bytes:
    kids_str = " ".join(f"{k} 0 R" for k in kids)
    return (
        f"{num} 0 obj\n<< /Type /Pages /Kids [{kids_str}] /Count {count} >>\nendobj"
    ).encode("latin-1")


def _make_catalog_bytes(num: int, root_pages_num: int) -> bytes:
    return (
        f"{num} 0 obj\n<< /Type /Catalog /Pages {root_pages_num} 0 R >>\nendobj"
    ).encode("latin-1")


# --------------------------------------------------------------------------- #
# 重新打包 + xref 重建
# --------------------------------------------------------------------------- #

def rebuild_pdf(data: bytes) -> bytes:
    """把可能结构不完整的 PDF 字节重写为标准、xref 完整的 PDF。"""
    objects = parse_pdf_objects(data)
    if not objects:
        raise ConvertError("文件中没有可识别的 PDF 对象，无法转换。")

    # 1) 确定/补全根 Pages
    root_pages_num, _ = _build_page_tree(objects)

    # 2) 确定/补全 Catalog
    catalog_num = _find_existing_catalog(objects, data)
    if catalog_num is None:
        catalog_num = max(objects.keys()) + 1
        objects[catalog_num] = _PdfObject(
            num=catalog_num, gen=0,
            raw=_make_catalog_bytes(catalog_num, root_pages_num),
            kids=[root_pages_num], parent=None, type_name="Catalog",
        )

    # 3) 可选保留文档信息对象 /Info
    info_num = None
    im = None
    for cand in _RE_INFO.finditer(data):
        im = cand
    if im:
        cand_num = int(im.group(1))
        if cand_num in objects:
            info_num = cand_num

    # 4) 顺序写出所有对象并记录偏移
    max_num = max(objects.keys())
    out = bytearray()
    out += b"%PDF-1.4\n"
    # 二进制注释，提示工具该文件含高位字节
    out += b"%\xe2\xe3\xcf\xd3\n"

    offsets: Dict[int, Tuple[int, int]] = {}
    # 按对象编号升序物理排列，便于阅读；xref 仍按编号索引
    for num in sorted(objects.keys()):
        obj = objects[num]
        offsets[num] = (len(out), obj.gen)
        block = obj.raw
        out += block
        if not block.endswith(b"\n") and not block.endswith(b"\r"):
            out += b"\n"

    # 5) 构建传统 xref 表
    xref_offset = len(out)
    size = max_num + 1
    lines = [b"xref\n", f"0 {size}\n".encode("latin-1")]
    # 条目 0 固定为 free
    lines.append(b"0000000000 65535 f \n")
    for num in range(1, size):
        if num in offsets:
            off, gen = offsets[num]
            lines.append(f"{off:010d} {gen:05d} n \n".encode("latin-1"))
        else:
            # 未使用编号记为 free
            lines.append(b"0000000000 00000 f \n")
    out += b"".join(lines)

    # 6) trailer
    trailer = f"trailer\n<< /Size {size} /Root {catalog_num} 0 R"
    if info_num is not None:
        trailer += f" /Info {info_num} 0 R"
    trailer += " >>\n"
    out += trailer.encode("latin-1")

    # 7) startxref 与 EOF（普通字符串中 % 不是转义符，直接写两个百分号）
    out += f"startxref\n{xref_offset}\n%%EOF\n".encode("latin-1")
    return bytes(out)


# --------------------------------------------------------------------------- #
# 顶层转换接口
# --------------------------------------------------------------------------- #

def convert_bytes(raw: bytes) -> Tuple[bytes, str, int]:
    """把读取到的原始字节转换为标准 PDF 字节。

    返回 (pdf_bytes, 格式名, 页数)。
    """
    fmt = detect_format(raw)

    if fmt in ("HN", "C8"):
        raise UnsupportedFormat(
            "该文件为 HN/C8 图像型格式（常见于 .nh 学位论文），使用 JBIG/JBIG2 压缩，"
            "本离线工具暂不支持。建议用知网 CAJViewer 打开后通过“打印 → Microsoft Print to PDF”转换。"
        )
    if fmt == "TEB":
        raise UnsupportedFormat(
            "TEB 格式暂不支持。建议使用 CAJViewer 打印为 PDF。"
        )

    if fmt == "CAJ":
        inner = _extract_caj_pdf(raw)
    elif fmt == "KDH":
        inner = _decrypt_kdh(raw)
    elif fmt == "PDF":
        inner = raw
    else:
        # 未知格式：尝试当作 PDF 处理，失败再报错
        if b"%PDF-" in raw or b" obj" in raw:
            inner = raw
            fmt = "PDF?"
        else:
            raise ConvertError("无法识别的文件类型，不是有效的 CAJ/KDH/PDF 文件。")

    pdf = rebuild_pdf(inner)

    # 统计页数（/Type /Page 数量）
    page_count = len(re.findall(rb"/Type\s*/Page\b", pdf))
    return pdf, fmt, page_count


def convert_file(
    source: str,
    target: Optional[str] = None,
) -> ConvertResult:
    """转换单个文件。target 为输出 PDF 路径或输出目录；缺省输出到同目录。"""
    result = ConvertResult(source=source)
    try:
        with open(source, "rb") as f:
            raw = f.read()

        pdf, fmt, page_count = convert_bytes(raw)
        result.fmt = fmt
        result.page_count = page_count

        out_path = _resolve_target_path(source, target)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(pdf)

        result.target = out_path
        result.ok = True
        result.message = f"转换成功（{fmt}，约 {page_count} 页）"
    except ConvertError as e:
        result.ok = False
        result.message = str(e)
    except Exception as e:  # noqa: BLE001 - 顶层兜底，保证批量不中断
        result.ok = False
        result.message = f"发生意外错误：{e}"
    return result


def _resolve_target_path(source: str, target: Optional[str]) -> str:
    """根据目标参数解析输出 PDF 路径。"""
    base = os.path.splitext(os.path.basename(source))[0] + ".pdf"
    if target is None:
        return os.path.join(os.path.dirname(os.path.abspath(source)), base)
    if os.path.isdir(target) or target.endswith(("\\", "/")):
        return os.path.join(target, base)
    # 目标是具体文件
    if os.path.splitext(target)[1].lower() != ".pdf":
        target += ".pdf"
    return target


def _unique_target(path: str, used: set) -> str:
    """若目标路径已在本批次被占用，则追加 _1、_2 …… 直到唯一。"""
    ap = os.path.abspath(path)
    if ap not in used:
        return path
    root, ext = os.path.splitext(path)
    n = 1
    while True:
        cand = f"{root}_{n}{ext}"
        if os.path.abspath(cand) not in used:
            return cand
        n += 1


def convert_many(
    paths: List[str],
    out_dir: Optional[str] = None,
    progress: Optional[Callable[[int, int, ConvertResult], None]] = None,
) -> List[ConvertResult]:
    """批量转换文件/文件夹。progress(当前索引, 总数, 本文件结果) 用于回调更新界面。

    同批次内不同源文件若指向同一输出路径（如 paper.caj 与 paper.kdh），
    会自动分配 paper.pdf 与 paper_1.pdf，避免互相覆盖。
    """
    files = _collect_files(paths)
    results: List[ConvertResult] = []
    assigned: set = set()
    total = len(files)
    for i, fp in enumerate(files, start=1):
        ext = os.path.splitext(fp)[1].lower()
        if ext in UNSUPPORTED_EXTS:
            # 明确不支持的格式不预占输出路径，直接产生失败结果
            r = convert_file(fp, None)
        else:
            base_target = _resolve_target_path(fp, out_dir)
            target = _unique_target(base_target, assigned)
            assigned.add(os.path.abspath(target))
            r = convert_file(fp, target)
        results.append(r)
        if progress:
            progress(i, total, r)
    return results


def _collect_files(paths: List[str]) -> List[str]:
    """把文件/文件夹路径展开为待转换文件清单（去重、保持顺序）。"""
    files: List[str] = []
    seen: set = set()

    def add(fp: str) -> None:
        ap = os.path.abspath(fp)
        if ap not in seen:
            seen.add(ap)
            files.append(fp)

    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, names in os.walk(p):
                for name in sorted(names):
                    ext = os.path.splitext(name)[1].lower()
                    if ext in SUPPORTED_INPUT_EXTS or ext in UNSUPPORTED_EXTS:
                        add(os.path.join(root, name))
        elif os.path.isfile(p):
            add(p)
        else:
            # 不存在的路径也加入，后续会产生错误结果
            add(p)
    return files


# --------------------------------------------------------------------------- #
# 命令行
# --------------------------------------------------------------------------- #

def _main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="免费、纯本地的 CAJ/KDH 转 PDF 工具（离线、无需第三方依赖）",
    )
    parser.add_argument(
        "inputs", nargs="+",
        help="一个或多个 .caj/.kdh/.pdf 文件，或包含这些文件的文件夹",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="输出目录（批量）或输出 PDF 文件路径（单文件）；缺省输出到源文件同目录",
    )
    args = parser.parse_args(argv)

    results = convert_many(args.inputs, args.output)

    ok_n = 0
    for r in results:
        if r.ok:
            ok_n += 1
            print(f"[成功] {r.source}  ->  {r.target}  ({r.message})")
        else:
            print(f"[失败] {r.source}  ->  {r.message}")

    print(f"\n完成：共 {len(results)} 个，成功 {ok_n} 个，失败 {len(results) - ok_n} 个。")
    return 0 if ok_n == len(results) else 1


if __name__ == "__main__":
    sys.exit(_main())
