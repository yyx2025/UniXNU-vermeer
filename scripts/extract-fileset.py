# -*- coding: utf-8 -*-
"""从 Mach-O kernelcache / XNU 内核里提取 fileset（kext 清单）。

用途：
  1. 看清某个 IPSW 的 kernelcache 到底带了哪些 Apple 专属 kext（判断能不能直接搬）；
  2. 对照 board.toml [kernel.prelink] roots，确认 prelink 只挑了需要的那些
     —— roots 列表本质就是"提取白名单"；
  3. 检查 cpusubtype：0=arm64（非 PAC），2=arm64e（需要 PAC 硬件）。

用法：
  python tools/extract_fileset.py <kernel.macho> [--grep 关键字] [--json out.json]

仅用标准库，无第三方依赖。
"""
import argparse
import json
import re
import struct
import sys
from pathlib import Path

MH_MAGIC_64 = 0xFEEDFACF
LC_FILESET_ENTRY = 0x35
LC_REQ_DYLD = 0x80000000

CPU_SUBTYPE_NAMES = {0: "arm64", 1: "arm64v8", 2: "arm64e"}


def parse(path):
    data = Path(path).read_bytes()
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds = struct.unpack_from("<IIIIII", data, 0)
    if magic != MH_MAGIC_64:
        raise SystemExit(f"{path}: not a 64-bit Mach-O (magic {magic:#x})")
    info = {
        "path": str(path),
        "cputype": cputype,
        "cpusubtype": cpusubtype & 0x00FFFFFF,
        "cpu": CPU_SUBTYPE_NAMES.get(cpusubtype & 0x00FFFFFF, hex(cpusubtype)),
        "filetype": filetype,
        "ncmds": ncmds,
        "entries": [],
    }
    off = 32
    end = 32 + sizeofcmds
    while off < end and len(info["entries"]) < 100000:
        cmd, cmdsize = struct.unpack_from("<II", data, off)
        if (cmd & ~LC_REQ_DYLD) == LC_FILESET_ENTRY:
            vmaddr, fileoff, entry_id, _reserved = struct.unpack_from("<QQII", data, off + 8)
            name_end = data.index(b"\0", off + entry_id)
            name = data[off + entry_id:name_end].decode(errors="replace")
            info["entries"].append({"name": name, "fileoff": fileoff, "vmaddr": vmaddr})
        off += cmdsize
    return info


GROUPS = (
    ("显示/图形", r"CLCD|MobileGraphics|IOSurface|GPU|Display|Backlight|AGX"),
    ("HID/输入", r"HID|EventDriver|Touch|Backlight"),
    ("存储", r"Storage|APFS|UFS|NAND|NVMe|Effaceable"),
    ("安全", r"AMFI|Sandbox|KeyStore|Image4|SEP|Trust"),
    ("平台/电源", r"ARMPlatform|PMGR|Watchdog|Platform|CPU|AOP|IOP"),
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kernel")
    ap.add_argument("--grep")
    ap.add_argument("--json")
    args = ap.parse_args()

    info = parse(args.kernel)
    print(f'{info["path"]}')
    print(f'  cpu={info["cpu"]}  filetype={info["filetype"]}  fileset_entries={len(info["entries"])}')
    if info["cpusubtype"] == 2:
        print("  注意：arm64e —— 需要带指针认证(PAC)的硬件与 Apple 的签名/entitlement 体系")
    elif info["cpusubtype"] == 0:
        print("  arm64（非 PAC）—— iPhone 8/X 与 A10 iPad 世代的做法")

    if args.grep:
        pat = re.compile(args.grep, re.I)
        hits = [e for e in info["entries"] if pat.search(e["name"])]
        print(f'\n匹配 "{args.grep}"：{len(hits)} 条')
        for e in hits:
            print(f'  {e["name"]:<52} off=0x{e["fileoff"]:x}')
    else:
        for label, pat in GROUPS:
            rx = re.compile(pat, re.I)
            hits = [e for e in info["entries"] if rx.search(e["name"])]
            print(f"\n[{label}] {len(hits)} 条")
            for e in hits[:14]:
                print(f'  {e["name"]}')

    if args.json:
        Path(args.json).write_text(json.dumps(info, indent=2), encoding="utf-8")
        print(f"\n写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
