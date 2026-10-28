#!/usr/bin/env python3
"""Sign a static Mach-O ad-hoc and emit the trust cache that names it.

Linux CI has no codesign(1), so this does the two things codesign would have
done for us:

  * append an LC_CODE_SIGNATURE whose SuperBlob holds one CodeDirectory, with a
    SHA-256 hash per 4 KiB page over everything before the signature;
  * report the cdhash, which is the SHA-256 of the CodeDirectory blob truncated
    to 20 bytes, and wrap it in the version-1/version-2 trust cache container
    that DarwinBoot validates (ValidTrustCaches in DarwinBoot.c) and that
    /dev/kmem-free XNU then consults.

Layout rules are taken from osfmk/kern/cs_blobs.h (CS_CodeDirectory, version
0x20400 = 88-byte header) and from cs_validate_codedirectory() in
bsd/kern/ubc_subr.c, which is what will actually accept or reject this blob.
"""

import argparse
import base64
import hashlib
import struct
import sys
import uuid

MH_MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
LC_CODE_SIGNATURE = 0x1D
LC_LOAD_DYLINKER = 0x0E
LC_LOAD_DYLIB = 0x0C
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001F
LC_LAZY_LOAD_DYLIB = 0x20
MH_NOUNDEFS = 0x1
MH_DYLDLINK = 0x4
MH_PIE = 0x200000
DYNAMIC_COMMANDS = {LC_LOAD_DYLINKER, LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB,
                    LC_REEXPORT_DYLIB, LC_LAZY_LOAD_DYLIB}

CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_CODEDIRECTORY = 0xFADE0C02
CS_ADHOC = 0x00000002
CD_VERSION = 0x20400
CD_HEADER_BYTES = 88          # up to and including execSegFlags
HASH_TYPE_SHA256 = 2
PAGE_SHIFT_4K = 12


def pages_of(buf, size):
    return [hashlib.sha256(buf[o:o + size]).digest()
            for o in range(0, len(buf), size)]


class MachO:
    def __init__(self, data):
        self.data = bytearray(data)
        magic, self.cputype, self.cpusubtype, self.filetype, self.ncmds, \
            self.sizeofcmds, self.flags, _ = struct.unpack_from('<8I', self.data, 0)
        if magic != MH_MAGIC_64:
            raise SystemExit(f'not a 64-bit Mach-O: 0x{magic:08x}')
        self.commands = []
        off = 32
        for _ in range(self.ncmds):
            cmd, size = struct.unpack_from('<II', self.data, off)
            self.commands.append((off, cmd, size))
            off += size

    def find(self, cmd):
        for off, c, size in self.commands:
            if c == cmd:
                return off, size
        return None, None

    def segment(self, wanted):
        """Find a segment by name. The first LC_SEGMENT_64 is usually __PAGEZERO,
        which is not what execSegBase should point at."""
        fallback = None
        for off, c, size in self.commands:
            if c != LC_SEGMENT_64:
                continue
            name = bytes(self.data[off + 8:off + 24]).rstrip(b'\0')
            fileoff, filesize = struct.unpack_from('<QQ', self.data, off + 40)
            if name == wanted:
                return off, fileoff, filesize
            if fallback is None and filesize:
                fallback = (off, fileoff, filesize)
        if fallback is None:
            raise SystemExit(f'no {wanted} segment')
        return fallback

    def linkedit(self):
        return self.segment(b'__LINKEDIT')[0]

    def first_content_offset(self):
        """Lowest file offset that any section actually uses."""
        low = len(self.data)
        for off, c, size in self.commands:
            if c != LC_SEGMENT_64:
                continue
            fileoff, = struct.unpack_from('<Q', self.data, off + 40)
            nsects, = struct.unpack_from('<I', self.data, off + 64)
            sect = off + 72
            for _ in range(nsects):
                ssize, = struct.unpack_from('<Q', self.data, sect + 40)
                offset, = struct.unpack_from('<I', self.data, sect + 48)
                if ssize:
                    low = min(low, fileoff + offset)
                sect += 80
        return low

    def existing_signature(self):
        off, _ = self.find(LC_CODE_SIGNATURE)
        if off is None:
            return None
        dataoff, datasize = struct.unpack_from('<II', self.data, off + 8)
        if not dataoff or not datasize:
            return None
        return bytes(self.data[dataoff:dataoff + datasize])

    def normalize_static(self):
        """Make the image a static one, whatever the linker thought of -static.

        ld64.lld answers "-static" with "Option `-static' is not yet implemented",
        so the image comes out of the linker as an ordinary executable: it carries
        LC_LOAD_DYLINKER and the MH_DYLDLINK / MH_PIE flags, and the kernel would
        hand it to a dyld that does not exist in this root. Drop those commands,
        compact the load-command area, and clear the flags. Nothing here is
        referenced by the program: it has no LC_LOAD_DYLIB and no symbols to fix
        up, so removing the marker is honest rather than a workaround.
        """
        kept, removed = [], []
        for off, cmd, size in self.commands:
            if cmd in DYNAMIC_COMMANDS:
                removed.append((cmd, bytes(self.data[off:off + size])))
            else:
                kept.append((off, cmd, size))
        if not removed:
            return []

        blob = bytearray()
        rebuilt = []
        for off, cmd, size in kept:
            rebuilt.append((32 + len(blob), cmd, size))
            blob += self.data[off:off + size]
        old_end = 32 + self.sizeofcmds
        self.data[32:old_end] = b'\0' * (old_end - 32)
        self.data[32:32 + len(blob)] = blob
        self.commands = rebuilt
        self.ncmds = len(rebuilt)
        self.sizeofcmds = len(blob)
        struct.pack_into('<I', self.data, 16, self.ncmds)
        struct.pack_into('<I', self.data, 20, self.sizeofcmds)

        self.flags = (self.flags | MH_NOUNDEFS) & ~(MH_DYLDLINK | MH_PIE)
        struct.pack_into('<I', self.data, 24, self.flags)
        return removed

    def add_code_signature_command(self):
        """Put LC_CODE_SIGNATURE in the header padding. Returns its offset."""
        off, _ = self.find(LC_CODE_SIGNATURE)
        if off is not None:
            return off
        lc_end = 32 + self.sizeofcmds
        if self.first_content_offset() - lc_end < 16:
            raise SystemExit('no room in the header for LC_CODE_SIGNATURE')
        struct.pack_into('<III', self.data, lc_end, LC_CODE_SIGNATURE, 16, 0)
        self.ncmds += 1
        self.sizeofcmds += 16
        struct.pack_into('<I', self.data, 16, self.ncmds)
        struct.pack_into('<I', self.data, 20, self.sizeofcmds)
        self.commands.append((lc_end, LC_CODE_SIGNATURE, 16))
        return lc_end

    def set_linkedit_size(self, filesize):
        off = self.linkedit()
        if off is None:
            raise SystemExit('no __LINKEDIT segment')
        struct.pack_into('<Q', self.data, off + 48, filesize)   # filesize
        struct.pack_into('<Q', self.data, off + 32, (filesize + 0xFFF) & ~0xFFF)  # vmsize


def build_codedirectory(ident, code_limit, cdhash_slots, exec_seg_base, exec_seg_limit):
    ident_b = ident.encode() + b'\0'
    ident_off = CD_HEADER_BYTES
    hash_off = ident_off + len(ident_b)
    length = hash_off + 32 * len(cdhash_slots)
    cd = bytearray(length)
    struct.pack_into('>9I', cd, 0,
                     CSMAGIC_CODEDIRECTORY, length, CD_VERSION, CS_ADHOC,
                     hash_off, ident_off, 0, len(cdhash_slots), code_limit)
    struct.pack_into('>4B', cd, 36, 32, HASH_TYPE_SHA256, 0, PAGE_SHIFT_4K)
    struct.pack_into('>I', cd, 40, 0)            # spare2
    struct.pack_into('>I', cd, 44, 0)            # scatterOffset
    struct.pack_into('>I', cd, 48, 0)            # teamOffset
    struct.pack_into('>I', cd, 52, 0)            # spare3
    struct.pack_into('>Q', cd, 56, 0)            # codeLimit64 (codeLimit is used)
    struct.pack_into('>Q', cd, 64, exec_seg_base)
    struct.pack_into('>Q', cd, 72, exec_seg_limit)
    struct.pack_into('>Q', cd, 80, 0)            # execSegFlags
    cd[ident_off:ident_off + len(ident_b)] = ident_b
    for i, slot in enumerate(cdhash_slots):
        cd[hash_off + i * 32:hash_off + (i + 1) * 32] = slot
    return bytes(cd)


def build_superblob(cd):
    blob = struct.pack('>3I', CSMAGIC_EMBEDDED_SIGNATURE, 20 + len(cd), 1)
    blob += struct.pack('>2I', 0, 20) + cd
    return blob


def trust_cache_bundle(cdhash):
    """Version-1 header (count, offsets) around one version-2 module."""
    module_uuid = uuid.uuid5(uuid.NAMESPACE_URL, 'vermeer-userland:' + cdhash.hex())
    module = struct.pack('<I16sI', 2, module_uuid.bytes, 1)
    module += cdhash + bytes([HASH_TYPE_SHA256, 0, 0, 0])
    return struct.pack('<2I', 1, 8) + module


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('binary')
    ap.add_argument('--identifier', default='org.vermeer.userland')
    ap.add_argument('--trust-cache', help='write the trust cache bundle here')
    ap.add_argument('--report', help='write a JSON report here')
    args = ap.parse_args()

    with open(args.binary, 'rb') as f:
        original = f.read()

    macho = MachO(original)
    sig = macho.existing_signature()
    if sig is not None:
        # Already signed (the linker supported -adhoc_codesign): just read it.
        count, = struct.unpack_from('>I', sig, 8)
        cd = None
        for i in range(count):
            btype, boff = struct.unpack_from('>2I', sig, 12 + i * 8)
            if btype == 0:
                cd = sig[boff:]
                break
        if cd is None:
            raise SystemExit('signature block has no CodeDirectory')
        cd_len, = struct.unpack_from('>I', cd, 4)
        cd = cd[:cd_len]
        cdhash = hashlib.sha256(cd).digest()[:20]
        dataoff, datasize = struct.unpack_from('<II', macho.data, macho.find(LC_CODE_SIGNATURE)[0] + 8)
        code_limit = struct.unpack_from('>I', cd, 32)[0]
        signed = original
    else:
        # Strip the dynamic-linker commands first: this also frees load-command
        # space, so the margin left by -headerpad is not the only headroom.
        removed = macho.normalize_static()
        if removed:
            print('removed load commands: ' + ', '.join(f'0x{c:08x}' for c, _ in removed))
        lc = macho.add_code_signature_command()

        # __TEXT is the executable segment; its (fileoff, filesize) is what the
        # CodeDirectory records for exec-segment hardening.
        _, text_fileoff, text_filesize = macho.segment(b'__TEXT')

        # Align the code limit to a whole page, not merely to 16 bytes. Apple's
        # verifier hashes min(page, limit - offset) for the last page, so a short
        # final page leaves two readings of the same bytes; a page-aligned limit
        # makes the last page complete and removes the question.
        code_limit = (len(macho.data) + 0xFFF) & ~0xFFF
        slots = [b'\0' * 32] * ((code_limit + 4095) // 4096)
        cd = build_codedirectory(args.identifier, code_limit, slots,
                                 text_fileoff, text_filesize)
        superblob = build_superblob(cd)
        datasize = (len(superblob) + 15) & ~15

        struct.pack_into('<II', macho.data, lc + 8, code_limit, datasize)
        macho.set_linkedit_size(code_limit + datasize)

        # Pad to the signature, then hash the finished bytes. Everything before
        # this point is final, including the two header edits above.
        macho.data += b'\0' * (code_limit - len(macho.data))
        real_slots = pages_of(macho.data[:code_limit], 4096)
        cd = build_codedirectory(args.identifier, code_limit, real_slots,
                                 text_fileoff, text_filesize)
        superblob = build_superblob(cd)
        if (len(superblob) + 15) & ~15 != datasize:
            raise SystemExit('signature size changed after hashing')
        signed = bytes(macho.data) + superblob + b'\0' * (datasize - len(superblob))
        cdhash = hashlib.sha256(cd).digest()[:20]

    with open(args.binary, 'wb') as f:
        f.write(signed)

    bundle = trust_cache_bundle(cdhash)
    if args.trust_cache:
        with open(args.trust_cache, 'wb') as f:
            f.write(bundle)

    if args.report:
        import json
        with open(args.report, 'w') as f:
            json.dump({
                'binary': args.binary,
                'binary_bytes': len(signed),
                'code_limit': code_limit,
                'pages': (code_limit + 4095) // 4096,
                'identifier': args.identifier,
                'cdhash': cdhash.hex(),
                'code_directory_bytes': len(cd),
                'removed_load_commands': [f'0x{c:08x}' for c, _ in (removed if sig is None else [])],
                'trust_cache_sha256': hashlib.sha256(bundle).hexdigest(),
            }, f, indent=2)
            f.write('\n')

    print(f'signed {args.binary}: {len(signed)} bytes, cdhash {cdhash.hex()}')


if __name__ == '__main__':
    main()
