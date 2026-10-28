#!/usr/bin/env python3
"""Test adhoc.py against a synthetic Mach-O, so signing is verified without clang.

The signer is the one part of this directory that can silently produce a binary
the kernel will refuse, and CI has no codesign to compare against. So build a
Mach-O with the same shape lld produces, run the signer, then re-derive every
number it claimed:

  * the load command is inserted and counted;
  * the CodeDirectory is version 0x20400 with the fields cs_validate_codedirectory
    in bsd/kern/ubc_subr.c demands (page shift, hash size, offsets, identifier);
  * every page hash equals SHA-256 of the bytes it covers;
  * the cdhash is SHA-256 of the CodeDirectory, and the trust cache names exactly
    that cdhash in the container DarwinBoot's ValidTrustCaches accepts.
"""

import hashlib
import json
import pathlib
import struct
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
LC_SEGMENT_64 = 0x19
LC_CODE_SIGNATURE = 0x1D
LC_LOAD_DYLINKER = 0x0E


def build_macho():
    """__PAGEZERO + __TEXT(__text) + __LINKEDIT, load commands ending at 0x800."""
    text = bytes(range(256)) * 64          # 16 KiB of body
    linkedit = b'\x11' * 0x100
    text_off, link_off = 0x800, 0x800 + len(text)

    def segment(name, vmaddr, vmsize, fileoff, filesize, nsects=0, prot=7):
        cmd = struct.pack('<II16sQQQQIIII', LC_SEGMENT_64, 72 + 80 * nsects, name,
                          vmaddr, vmsize, fileoff, filesize, prot, prot, nsects, 0)
        return cmd

    def section(sectname, segname, addr, size, offset):
        return struct.pack('<16s16sQQIIIIIIII', sectname, segname, addr, size,
                           offset, 4, 0, 0, 0x80000400, 0, 0, 0)

    cmds = b''
    cmds += segment(b'__PAGEZERO', 0, 0x100000000, 0, 0)
    cmds += segment(b'__TEXT', 0x100000000, 0x4000, 0, text_off + len(text), 1)
    cmds += section(b'__text', b'__TEXT', 0x100000800, len(text), text_off)
    cmds += segment(b'__LINKEDIT', 0x100004000, 0x1000, link_off, len(linkedit), 0, 1)

    # What ld64.lld really emits when it answers "-static" with "not yet implemented":
    # a dynamic-linker command and the flags that go with it.
    dyld_path = b'/usr/lib/dyld\x00'
    dyld_path += b'\x00' * ((8 - len(dyld_path) % 8) % 8)
    cmds += struct.pack('<III', 0x0E, 24 + len(dyld_path), 12) + dyld_path
    flags = 0x4 | 0x200000          # MH_DYLDLINK | MH_PIE, as the linker leaves them

    ncmds = 5
    header = struct.pack('<IIIIIIII', 0xFEEDFACF, 0x0100000C, 0, 2, ncmds, len(cmds), flags, 0)
    image = bytearray(header + cmds)
    # -headerpad: leave real zeros between the commands and the first section.
    image += b'\0' * (text_off - len(image))
    image += text
    image += linkedit
    return bytes(image)


def macho_commands(image):
    ncmds, = struct.unpack_from('<I', image, 16)
    off = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', image, off)
        yield off, cmd, size
        off += size


def check(cond, message):
    if not cond:
        print(f'FAIL: {message}')
        sys.exit(1)
    print(f'ok: {message}')


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        binary = tmp / 'launchd.macho'
        binary.write_bytes(build_macho())
        tc_path = tmp / 'trust.bin'
        report_path = tmp / 'report.json'

        subprocess.run([sys.executable, str(HERE / 'adhoc.py'), str(binary),
                        '--trust-cache', str(tc_path),
                        '--report', str(report_path)], check=True)

        image = binary.read_bytes()
        report = json.loads(report_path.read_text())

        magic, _, _, _, ncmds, sizeofcmds, _, _ = struct.unpack_from('<8I', image, 0)
        check(magic == 0xFEEDFACF, 'still a 64-bit Mach-O')

        # Walk the commands, find the signature and __LINKEDIT.
        off, sig_off, linkedit_off, names = 32, None, None, {}
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', image, off)
            if cmd == LC_CODE_SIGNATURE:
                sig_off = off
            elif cmd == LC_SEGMENT_64:
                name = image[off + 8:off + 24].rstrip(b'\0')
                names[name] = off
                if name == b'__LINKEDIT':
                    linkedit_off = off
            off += size
        check(sig_off is not None, 'LC_CODE_SIGNATURE was inserted')

        # Nothing may point at a dyld this root does not have.
        cmds_present = {c for _, c, _ in macho_commands(image)}
        check(LC_LOAD_DYLINKER not in cmds_present, 'LC_LOAD_DYLINKER was removed')
        flags_after, = struct.unpack_from('<I', image, 24)
        check(not flags_after & 0x4, 'MH_DYLDLINK was cleared')
        check(not flags_after & 0x200000, 'MH_PIE was cleared')
        check(flags_after & 0x1, 'MH_NOUNDEFS was set')
        check(32 + sizeofcmds <= 0x800, 'load commands still fit the header padding')
        check(b'__TEXT' in names and b'__PAGEZERO' in names, 'both segments are present')

        dataoff, datasize = struct.unpack_from('<II', image, sig_off + 8)
        check(dataoff == len(image) - datasize, 'signature is the tail of the file')
        check(dataoff % 16 == 0, 'signature starts 16-byte aligned')

        blob = image[dataoff:dataoff + datasize]
        magic, length, count = struct.unpack_from('>3I', blob, 0)
        check(magic == 0xFADE0CC0, 'SuperBlob magic')
        check(length <= datasize, 'SuperBlob length fits LC_CODE_SIGNATURE')
        check(count == 1, 'SuperBlob holds one blob')
        btype, boff = struct.unpack_from('>2I', blob, 12)
        check(btype == 0, 'the blob is a CodeDirectory')

        cd = blob[boff:]
        cd_magic, cd_len, version, flags = struct.unpack_from('>4I', cd, 0)
        hash_off, ident_off, n_special, n_code, code_limit = struct.unpack_from('>5I', cd, 16)
        hash_size, hash_type, platform, page_shift = struct.unpack_from('>4B', cd, 36)
        check(cd_magic == 0xFADE0C02, 'CodeDirectory magic')
        check(cd_len <= len(cd), 'CodeDirectory length fits')
        check(version == 0x20400, 'version 0x20400')
        check(flags == 2, 'ad-hoc flags')
        check(page_shift == 12, 'page shift is 4 KiB, the kernel page size here')
        check((hash_type, hash_size) == (2, 32), 'SHA-256 with 32-byte slots')
        check(code_limit == dataoff, 'codeLimit covers everything before the signature')
        check(n_code == (code_limit + 4095) // 4096, 'one slot per 4 KiB page')
        check(n_special == 0, 'no special slots')
        check(ident_off == 88, 'identifier follows the 88-byte 0x20400 header')
        ident = cd[ident_off:hash_off]
        check(ident.endswith(b'\0') and len(ident) > 1, 'identifier is NUL terminated')
        check(hash_off + 32 * n_code <= cd_len, 'hash slots fit inside the blob')

        for i in range(n_code):
            page = image[i * 4096:(i + 1) * 4096]
            slot = cd[hash_off + i * 32:hash_off + (i + 1) * 32]
            if hashlib.sha256(page).digest() != slot:
                check(False, f'page {i} hash matches the bytes it covers')
        check(True, f'all {n_code} page hashes match')

        cdhash = hashlib.sha256(cd[:cd_len]).digest()[:20]
        check(cdhash.hex() == report['cdhash'], 'cdhash is SHA-256 of the CodeDirectory')

        # __TEXT, not __PAGEZERO, is what the exec segment fields point at.
        text_off = names[b'__TEXT']
        exec_base, exec_limit = struct.unpack_from('>QQ', cd, 64)
        text_fileoff, text_filesize = struct.unpack_from('<QQ', image, text_off + 40)
        check((exec_base, exec_limit) == (text_fileoff, text_filesize),
              'exec segment fields record __TEXT')

        le_off = linkedit_off + 48
        link_filesize, = struct.unpack_from('<Q', image, le_off)
        check(link_filesize == len(image), '__LINKEDIT was extended over the signature')

        tc = tc_path.read_bytes()
        tc_count, tc_first = struct.unpack_from('<2I', tc, 0)
        check(tc_count == 1 and tc_first == 8, 'trust cache: version 1 with one module')
        tc_version, = struct.unpack_from('<I', tc, 8)
        check(tc_version == 2, 'trust cache module version 2')
        entries, = struct.unpack_from('<I', tc, 28)
        check(entries == 1, 'trust cache names one cdhash')
        check(tc[32:52] == cdhash, 'the entry is that cdhash')
        check(tc[52] in (1, 2, 3, 4), 'hash type byte is in the range ValidTrustCaches allows')
        check(len(tc) == 8 + 24 + 24 * entries, 'module length matches the entry count')

        # Signing twice must not stack a second signature.
        subprocess.run([sys.executable, str(HERE / 'adhoc.py'), str(binary),
                        '--trust-cache', str(tc_path)], check=True,
                       stdout=subprocess.DEVNULL)
        again = binary.read_bytes()
        check(again == image, 're-signing an already signed image is a no-op')

    print('adhoc.py: all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
