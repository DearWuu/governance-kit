"""确定性视觉门控：自解析 PNG 逐像素比对，输出 PASS/FAIL/UNABLE 三态证据。

上游：CI 或 agent 以 baseline（人工审批入库）与 actual（当次截图）调用本脚本。
下游：门控编排器读取 JSON 的 status 与 diff_ratio；UNABLE 一律不折算为 PASS。
约束：仅 stdlib；支持 8-bit RGB/RGBA、非隔行、过滤类型 0-4。
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import tempfile
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {2: 3, 6: 4}  # color type -> 通道数
# 过滤类型 0-4 之外的字节视为损坏
MAX_FILTER = 4


class PngError(ValueError):
    """PNG 不可解码（格式不支持或数据损坏）。"""


def _iter_chunks(data: bytes):
    pos = len(PNG_SIGNATURE)
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if len(body) != length or pos + 12 + length > len(data):
            raise PngError("chunk 长度越界，文件被截断")
        crc = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0]
        if zlib.crc32(ctype + body) & 0xFFFFFFFF != crc:
            raise PngError("chunk CRC 校验失败")
        yield ctype, body
        pos += 12 + length


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> bytes:
    stride = width * bpp
    expected = (stride + 1) * height
    if len(raw) != expected:
        raise PngError(f"图像数据长度不符：期望 {expected}，实际 {len(raw)}")
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
        else:
            raise PngError(f"未知过滤类型 {ftype}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return bytes(out)


def parse_png(data: bytes) -> dict:
    """解析 PNG，返回 {width, height, channels, pixels}；不支持/损坏则抛 PngError。"""
    if not data.startswith(PNG_SIGNATURE):
        raise PngError("不是 PNG（签名不符）")
    header = None
    idat = bytearray()
    saw_iend = False
    for ctype, body in _iter_chunks(data):
        if ctype == b"IHDR":
            if len(body) != 13:
                raise PngError("IHDR 长度非法")
            header = struct.unpack(">IIBBBBB", body)
        elif ctype == b"IDAT":
            idat.extend(body)
        elif ctype == b"IEND":
            saw_iend = True
    if header is None:
        raise PngError("缺少 IHDR")
    if not saw_iend:
        raise PngError("缺少 IEND，文件被截断")
    if not idat:
        raise PngError("缺少 IDAT")

    width, height, depth, color_type, comp, filt, interlace = header
    if depth != 8:
        raise PngError(f"仅支持 8-bit，实际 bit depth={depth}")
    if color_type not in CHANNELS:
        raise PngError(f"仅支持 RGB/RGBA，实际 color type={color_type}")
    if comp != 0 or filt != 0:
        raise PngError("压缩/过滤方法非 0，不支持")
    if interlace != 0:
        raise PngError("不支持隔行扫描（interlaced）")
    if width <= 0 or height <= 0:
        raise PngError("图像尺寸非法")

    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise PngError(f"IDAT 解压失败：{exc}") from exc
    channels = CHANNELS[color_type]
    pixels = _unfilter(raw, width, height, channels)
    return {"width": width, "height": height, "channels": channels, "pixels": pixels}


def _result(status: str, ratio: float, width: int, height: int, reason: str = "") -> dict:
    return {
        "status": status,
        "diff_ratio": round(float(ratio), 6),
        "width": int(width),
        "height": int(height),
        "reason": reason,
    }


def compare_files(baseline: str, actual: str, threshold: float, pixel_tolerance: int) -> dict:
    """读取两张 PNG 并比对；任何读不到/解不开/规格不符都返回 UNABLE。"""
    try:
        with open(baseline, "rb") as fh:
            base = parse_png(fh.read())
    except (OSError, PngError) as exc:
        return _result("UNABLE", 0.0, 0, 0, f"baseline 不可用：{exc}")
    try:
        with open(actual, "rb") as fh:
            act = parse_png(fh.read())
    except (OSError, PngError) as exc:
        return _result("UNABLE", 0.0, base["width"], base["height"], f"actual 不可用：{exc}")

    if (base["width"], base["height"]) != (act["width"], act["height"]):
        return _result(
            "UNABLE", 0.0, base["width"], base["height"],
            f"尺寸不一致：baseline {base['width']}x{base['height']}，"
            f"actual {act['width']}x{act['height']}",
        )
    if base["channels"] != act["channels"]:
        return _result("UNABLE", 0.0, base["width"], base["height"], "通道数不一致（RGB vs RGBA）")

    width, height, ch = base["width"], base["height"], base["channels"]
    a, b = base["pixels"], act["pixels"]
    total = width * height
    if a == b:
        return _result("PASS", 0.0, width, height)

    diff = 0
    if ch == 4:
        for i in range(0, total * 4, 4):
            if a[i + 3] == 0 and b[i + 3] == 0:
                continue
            if (
                abs(a[i] - b[i]) > pixel_tolerance
                or abs(a[i + 1] - b[i + 1]) > pixel_tolerance
                or abs(a[i + 2] - b[i + 2]) > pixel_tolerance
                or abs(a[i + 3] - b[i + 3]) > pixel_tolerance
            ):
                diff += 1
    else:
        for i in range(0, total * 3, 3):
            if (
                abs(a[i] - b[i]) > pixel_tolerance
                or abs(a[i + 1] - b[i + 1]) > pixel_tolerance
                or abs(a[i + 2] - b[i + 2]) > pixel_tolerance
            ):
                diff += 1

    ratio = diff / total if total else 0.0
    if ratio <= threshold + 1e-12:
        return _result("PASS", ratio, width, height)
    return _result(
        "FAIL", ratio, width, height,
        f"差异像素 {diff}/{total}，占比 {ratio:.6f} 超过阈值 {threshold}",
    )


def _encode_png(width: int, height: int, channels: int, pixels: bytes,
                depth: int = 8, filter_type: int = 0) -> bytes:
    """仅测试用的最小编码器：生成合法 PNG（filter_type 用于覆盖 0-4 解码分支）。"""
    bpp = channels * (depth // 8)
    stride = width * bpp
    raw = bytearray()
    prev = bytes(stride)
    for y in range(height):
        line = pixels[y * stride:(y + 1) * stride]
        raw.append(filter_type)
        if filter_type == 0:
            raw.extend(line)
        elif filter_type == 1:
            enc = bytearray(line)
            for i in range(stride - 1, bpp - 1, -1):
                enc[i] = (enc[i] - line[i - bpp]) & 0xFF
            raw.extend(enc)
        elif filter_type == 2:
            enc = bytearray(line)
            for i in range(stride):
                enc[i] = (enc[i] - prev[i]) & 0xFF
            raw.extend(enc)
        else:
            # 3/4 的编码器实现成本高，仅 selftest 的过滤覆盖放在 parse 侧用 0 过滤生成；
            # 这里退回 0，避免测试代码复杂化。
            raw[-1] = 0
            raw.extend(line)
        prev = line
    color_type = 2 if channels == 3 else 6

    def chunk(ctype: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + ctype + body
                + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, depth, color_type, 0, 0, 0)
    return (PNG_SIGNATURE + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def _make_pixels(width: int, height: int, channels: int) -> bytes:
    px = bytearray()
    for y in range(height):
        for x in range(width):
            px.extend((x * 7 % 256, y * 11 % 256, (x + y) * 5 % 256))
            if channels == 4:
                px.append(255)
    return bytes(px)


def _selftest() -> int:
    failures = []

    def check(name: str, cond: bool, detail: str) -> None:
        print(f"[{'PASS' if cond else 'FAIL'}] {name}: {detail}")
        if not cond:
            failures.append(name)

    tmp = tempfile.mkdtemp(prefix="visual-gate-selftest-")
    try:
        w, h = 8, 8
        base_px = _make_pixels(w, h, 4)
        base_path = os.path.join(tmp, "baseline.png")
        same_path = os.path.join(tmp, "same.png")
        diff_path = os.path.join(tmp, "diff.png")
        with open(base_path, "wb") as fh:
            fh.write(_encode_png(w, h, 4, base_px))
        with open(same_path, "wb") as fh:
            fh.write(_encode_png(w, h, 4, base_px))

        changed = bytearray(base_px)
        changed[0:4] = bytes((255, 0, 0, 255))  # 左上角改一像素
        with open(diff_path, "wb") as fh:
            fh.write(_encode_png(w, h, 4, bytes(changed)))

        same = compare_files(base_path, same_path, 0.002, 8)
        check("相同图 -> PASS", same["status"] == "PASS" and same["diff_ratio"] == 0.0,
              json.dumps(same, ensure_ascii=False))

        diff = compare_files(base_path, diff_path, 0.002, 8)
        check("改一像素 -> FAIL", diff["status"] == "FAIL" and diff["diff_ratio"] > 0.002,
              json.dumps(diff, ensure_ascii=False))

        relaxed = compare_files(base_path, diff_path, 0.05, 8)
        check("阈值 0.05 -> PASS", relaxed["status"] == "PASS",
              json.dumps(relaxed, ensure_ascii=False))

        # 尺寸不一致 -> UNABLE
        small_path = os.path.join(tmp, "small.png")
        with open(small_path, "wb") as fh:
            fh.write(_encode_png(4, 4, 4, _make_pixels(4, 4, 4)))
        mismatched = compare_files(base_path, small_path, 0.002, 8)
        check("尺寸不一致 -> UNABLE", mismatched["status"] == "UNABLE",
              json.dumps(mismatched, ensure_ascii=False))

        # 损坏文件 -> UNABLE（绝不能是 PASS）
        broken_path = os.path.join(tmp, "broken.png")
        with open(broken_path, "wb") as fh:
            fh.write(PNG_SIGNATURE + b"\x00\x00\x00\x0dIHDR" + b"junk")
        broken = compare_files(base_path, broken_path, 0.002, 8)
        check("损坏文件 -> UNABLE", broken["status"] == "UNABLE",
              json.dumps(broken, ensure_ascii=False))

        # 16-bit 不在支持范围 -> UNABLE
        deep_path = os.path.join(tmp, "deep.png")
        with open(deep_path, "wb") as fh:
            fh.write(_encode_png(2, 2, 4, _make_pixels(2, 2, 4), depth=16))
        deep = compare_files(base_path, deep_path, 0.002, 8)
        check("16-bit -> UNABLE", deep["status"] == "UNABLE",
              json.dumps(deep, ensure_ascii=False))

        # RGB 通道解析 + 过滤类型 0-4 解码分支
        rgb_px = _make_pixels(8, 8, 3)
        rgb_path = os.path.join(tmp, "rgb.png")
        rgb_path2 = os.path.join(tmp, "rgb2.png")
        with open(rgb_path, "wb") as fh:
            fh.write(_encode_png(8, 8, 3, rgb_px))
        with open(rgb_path2, "wb") as fh:
            fh.write(_encode_png(8, 8, 3, rgb_px, filter_type=0))
        rgb = compare_files(rgb_path, rgb_path2, 0.002, 8)
        check("RGB 相同 -> PASS", rgb["status"] == "PASS",
              json.dumps(rgb, ensure_ascii=False))

        # 手工构造 filter 1/2/3/4 的 IDAT，验证解码还原为 filter 0 的图像
        for ftype in (1, 2, 3, 4):
            enc = _filtered_rows(rgb_px, 8, 8, 3, ftype)
            path = os.path.join(tmp, f"rgb-f{ftype}.png")
            with open(path, "wb") as fh:
                fh.write(_raw_png(8, 8, 2, enc))
            got = compare_files(rgb_path, path, 0.0, 0)
            check(f"过滤类型 {ftype} 解码一致", got["status"] == "PASS",
                  json.dumps(got, ensure_ascii=False))
    finally:
        for name in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass

    if failures:
        print(f"selftest 失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("selftest 全部通过")
    return 0


def _filtered_rows(pixels: bytes, width: int, height: int, channels: int, ftype: int) -> bytes:
    """按指定过滤类型编码原始扫描线（含过滤字节），供解码测试。"""
    stride = width * channels
    out = bytearray()
    prev = bytes(stride)
    for y in range(height):
        line = pixels[y * stride:(y + 1) * stride]
        out.append(ftype)
        enc = bytearray(stride)
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = prev[i]
            upleft = prev[i - channels] if i >= channels else 0
            if ftype == 1:
                pred = left
            elif ftype == 2:
                pred = up
            elif ftype == 3:
                pred = (left + up) >> 1
            else:
                pred = _paeth(left, up, upleft)
            enc[i] = (line[i] - pred) & 0xFF
        out.extend(enc)
        prev = line
    return bytes(out)


def _raw_png(width: int, height: int, color_type: int, raw: bytes) -> bytes:
    def chunk(ctype: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + ctype + body
                + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (PNG_SIGNATURE + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="PNG 视觉门控（三态：PASS/FAIL/UNABLE）")
    parser.add_argument("--baseline", help="基线 PNG（人工审批入库）")
    parser.add_argument("--actual", help="当次截图 PNG")
    parser.add_argument("--threshold", type=float, default=0.002, help="允许的差异像素占比上限")
    parser.add_argument("--pixel-tolerance", type=int, default=8, help="单通道差值容差 0-255")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    parser.add_argument("--selftest", action="store_true", help="生成极小 PNG 自检")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.baseline or not args.actual:
        parser.error("需要 --baseline 与 --actual（或 --selftest）")
    if not 0.0 <= args.threshold <= 1.0:
        parser.error("--threshold 必须在 [0, 1] 区间")
    if not 0 <= args.pixel_tolerance <= 255:
        parser.error("--pixel-tolerance 必须在 [0, 255] 区间")

    result = compare_files(args.baseline, args.actual, args.threshold, args.pixel_tolerance)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"visual_gate: {result['status']} diff_ratio={result['diff_ratio']:.6f} "
              f"{result['width']}x{result['height']} threshold={args.threshold}")
        if result["reason"]:
            print(f"reason: {result['reason']}")
    return {"PASS": 0, "FAIL": 1}.get(result["status"], 2)


if __name__ == "__main__":
    sys.exit(main())