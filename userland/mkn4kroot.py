#!/usr/bin/env python3
"""Wrap two files into the N4KROOT container that mockfs understands.

patches/xnu/mockfs-test-file.patch teaches mockfs to look at offset 0 for this
header instead of assuming the whole device is one Mach-O:

    char     magic[8]      "N4KROOT\\0"
    uint32_t version       1
    uint32_t count         2
    uint64_t launch_offset 4096
    uint64_t launch_size
    uint64_t test_offset
    uint64_t test_size

The kernel maps /launchd, /sbin/launchd to the first file and /probe.bin, /dyld
to the second, and rejects the image unless launch_offset is exactly 4096, the
second offset is 4 KiB aligned and at least 8192, the sizes fit the device, and
the whole thing is under 32 MiB (mockfs_vfsops.c, mountroot).
"""

import argparse
import json
import os
import struct

HEADER = 48
LAUNCH_OFFSET = 4096
LIMIT = 32 * 1024 * 1024


def align(value, to):
    return (value + to - 1) // to * to


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--launch', required=True, help='the Mach-O the kernel execs as /sbin/launchd')
    ap.add_argument('--test', required=True, help='second file, visible as /probe.bin and /dyld')
    ap.add_argument('--output', required=True)
    ap.add_argument('--report')
    args = ap.parse_args()

    launch = open(args.launch, 'rb').read()
    test = open(args.test, 'rb').read()

    test_offset = align(LAUNCH_OFFSET + len(launch), 4096)
    total = align(test_offset + len(test), 4096)

    if test_offset < 8192:
        raise SystemExit('second file would land below the 8192 floor')
    if len(launch) > test_offset - LAUNCH_OFFSET:
        raise SystemExit('launch file overlaps the second file')
    if total > LIMIT:
        raise SystemExit(f'image is {total} bytes; mockfs rejects anything over {LIMIT}')

    image = bytearray(total)
    struct.pack_into('<8sII4Q', image, 0, b'N4KROOT\0', 1, 2,
                     LAUNCH_OFFSET, len(launch), test_offset, len(test))
    image[LAUNCH_OFFSET:LAUNCH_OFFSET + len(launch)] = launch
    image[test_offset:test_offset + len(test)] = test

    with open(args.output, 'wb') as f:
        f.write(image)

    if args.report:
        import hashlib
        with open(args.report, 'w') as f:
            json.dump({
                'image': args.output,
                'bytes': total,
                'launch': {'size': len(launch), 'offset': LAUNCH_OFFSET,
                           'sha256': hashlib.sha256(launch).hexdigest()},
                'test': {'size': len(test), 'offset': test_offset,
                         'sha256': hashlib.sha256(test).hexdigest()},
                'limit_bytes': LIMIT,
            }, f, indent=2)
            f.write('\n')

    print(f'{args.output}: {total} bytes, launch {len(launch)} @ {LAUNCH_OFFSET}, '
          f'test {len(test)} @ {test_offset}')


if __name__ == '__main__':
    main()
