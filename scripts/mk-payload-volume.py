"""Build the UniXNU payload volume (FAT32) without mtools.

The CI writes this volume with mformat/mcopy. This is the same layout for machines that do
not have mtools: 512-byte sectors, one sector per cluster, 32 reserved sectors, two FATs,
long file names for everything that does not fit 8.3. Files are allocated contiguously, in
the order given, so the image is reproducible.

Usage: python tools/mkpayloadfat.py STAGE_DIR OUT_IMAGE SIZE_MIB [LABEL]
"""

import os
import struct
import sys
from pathlib import Path

SECTOR = 512
RESERVED = 32
NFATS = 2
LFN_ATTR = 0x0F
DIR_ATTR = 0x10
ARCHIVE = 0x20
LABEL_ATTR = 0x08
DATE_1980 = 0x21          # 1980-01-01
TIME_0 = 0x0000


def short_checksum(name11):
    s = 0
    for b in name11:
        s = (((s & 1) << 7) | (s >> 1)) + b
        s &= 0xFF
    return s


def lfn_units(longname):
    raw = longname.encode('utf-16-le')
    chars = [raw[i:i + 2] for i in range(0, len(raw), 2)]
    count = (len(chars) + 12) // 13
    units = []
    for index in range(count):
        part = chars[index * 13:(index + 1) * 13]
        if len(part) < 13:
            part = part + [b'\x00\x00'] + [b'\xff\xff'] * (12 - len(part))
        units.append(part)
    return units


def lfn_entries(longname, name11, max_entries):
    check = short_checksum(name11)
    units = lfn_units(longname)
    if len(units) > max_entries:
        raise SystemExit(f'{longname}: needs {len(units)} LFN entries, only {max_entries} fit the short name')
    out = []
    for index in range(len(units) - 1, -1, -1):
        part = units[index]
        seq = (index + 1) | (0x40 if index == len(units) - 1 else 0)
        entry = bytearray(32)
        entry[0] = seq
        entry[1:11] = b''.join(part[0:5])
        entry[11] = LFN_ATTR
        entry[12] = 0
        entry[13] = check
        entry[14:26] = b''.join(part[5:11])
        entry[26:28] = b'\x00\x00'
        entry[28:32] = b''.join(part[11:13])
        out.append(bytes(entry))
    return out


def sfn_entry(name11, attr, cluster, size):
    entry = bytearray(32)
    entry[0:11] = name11
    entry[11] = attr
    entry[12] = 0
    entry[13] = 0
    entry[14:16] = struct.pack('<H', TIME_0)
    entry[16:18] = struct.pack('<H', DATE_1980)
    entry[18:20] = struct.pack('<H', DATE_1980)
    entry[20:22] = struct.pack('<H', (cluster >> 16) & 0xFFFF)
    entry[22:24] = struct.pack('<H', TIME_0)
    entry[24:26] = struct.pack('<H', DATE_1980)
    entry[26:28] = struct.pack('<H', cluster & 0xFFFF)
    entry[28:32] = struct.pack('<I', size)
    return bytes(entry)


def dirents(longname, name11, attr, cluster, size):
    """The entries for one name: LFN chain first, then the 8.3 entry."""
    if longname.upper() == name11.decode('latin1').rstrip().upper():
        prefix = []
    else:
        prefix = lfn_entries(longname, name11, 20)
    return prefix + [sfn_entry(name11, attr, cluster, size)]


def layout(total_sectors):
    """Solve fatsz and cluster count the way mformat does: clamp at the fixed point."""
    fatsz = max(1, (total_sectors * 4) // (SECTOR * 2) + 1)
    for _ in range(8):
        clusters = total_sectors - RESERVED - NFATS * fatsz
        if clusters <= 0:
            raise SystemExit('volume too small')
        need = ((clusters + 2) * 4 + SECTOR - 1) // SECTOR
        if need == fatsz:
            break
        fatsz = need
    clusters = total_sectors - RESERVED - NFATS * fatsz
    return fatsz, clusters


class Volume:
    def __init__(self, path, size_mib, label):
        self.path = Path(path)
        self.total_sectors = size_mib * 1024 * 1024 // SECTOR
        self.label = label[:11].ljust(11).encode('ascii')
        self.fatsz, self.clusters = layout(self.total_sectors)
        self.data_sector = RESERVED + NFATS * self.fatsz
        self.data_start = self.data_sector * SECTOR
        self.next_cluster = 2
        self.fat = bytearray((self.clusters + 2) * 4)
        self.fat[0:8] = struct.pack('<II', 0x0FFFFFF8, 0x0FFFFFFF)
        self.pending = []          # (cluster, source path or bytes)
        self.file = open(self.path, 'wb')

    # -- allocation ------------------------------------------------------
    def take(self, count):
        first = self.next_cluster
        last = first + count - 1
        if last > self.clusters + 1:
            raise SystemExit(f'volume full: needs clusters {first}..{last}, has {self.clusters}')
        self.next_cluster = last + 1
        for cluster in range(first, last + 1):
            value = 0x0FFFFFFF if cluster == last else cluster + 1
            struct.pack_into('<I', self.fat, cluster * 4, value)
        return first

    def reserve_file(self, path):
        size = path.stat().st_size
        clusters = max(1, (size + SECTOR - 1) // SECTOR)
        first = self.take(clusters)
        self.pending.append((first, path))
        return first, size

    def reserve_dir(self,):
        return self.take(1)

    def write_dir(self, cluster, entries):
        blob = b''.join(entries)
        if len(blob) > SECTOR:
            raise SystemExit(f'directory needs {len(blob)} bytes, a cluster is {SECTOR}')
        self.file.seek(self.data_start + (cluster - 2) * SECTOR)
        self.file.write(blob)

    # -- output ----------------------------------------------------------
    def write_boot(self):
        bs = bytearray(512)
        bs[0:3] = b'\xeb\x58\x90'
        bs[3:11] = b'mkfs.fat'
        struct.pack_into('<H', bs, 11, SECTOR)
        bs[13] = 1                                   # sectors per cluster
        struct.pack_into('<H', bs, 14, RESERVED)
        bs[16] = NFATS
        struct.pack_into('<H', bs, 17, 0)            # root entries (0 on FAT32)
        struct.pack_into('<H', bs, 19, 0)            # total sectors 16
        bs[21] = 0xF8
        struct.pack_into('<H', bs, 22, 0)            # FAT size 16
        struct.pack_into('<H', bs, 24, 32)           # sectors per track
        struct.pack_into('<H', bs, 26, 64)           # heads
        struct.pack_into('<I', bs, 28, 0)            # hidden
        struct.pack_into('<I', bs, 32, self.total_sectors)
        struct.pack_into('<I', bs, 36, self.fatsz)
        struct.pack_into('<H', bs, 40, 0)            # ext flags
        struct.pack_into('<H', bs, 42, 0)            # fs version
        struct.pack_into('<I', bs, 44, 2)            # root cluster
        struct.pack_into('<H', bs, 48, 1)            # FSInfo sector
        struct.pack_into('<H', bs, 50, 6)            # backup boot sector
        bs[64] = 0x80
        bs[66] = 0x29
        struct.pack_into('<I', bs, 67, 0x1234ABCD)
        bs[71:82] = self.label
        bs[82:90] = b'FAT32   '
        bs[510:512] = b'\x55\xaa'
        self.file.seek(0)
        self.file.write(bs)
        self.file.seek(6 * SECTOR)
        self.file.write(bs)

        info = bytearray(512)
        struct.pack_into('<I', info, 0, 0x41615252)
        struct.pack_into('<I', info, 484, 0x61417272)
        free = self.clusters - (self.next_cluster - 2)
        struct.pack_into('<I', info, 488, free)
        struct.pack_into('<I', info, 492, self.next_cluster)
        struct.pack_into('<I', info, 508, 0xAA550000)
        self.file.seek(1 * SECTOR)
        self.file.write(info)
        self.file.seek(7 * SECTOR)
        self.file.write(info)

    def write_fats(self):
        for index in range(NFATS):
            self.file.seek((RESERVED + index * self.fatsz) * SECTOR)
            self.file.write(self.fat)

    def write_data(self):
        chunk = 1 << 20
        for first, path in self.pending:
            self.file.seek(self.data_start + (first - 2) * SECTOR)
            with open(path, 'rb') as source:
                while True:
                    block = source.read(chunk)
                    if not block:
                        break
                    self.file.write(block)

    def close(self):
        self.file.truncate(self.total_sectors * SECTOR)
        self.file.close()


def build(stage, out, size_mib, label):
    stage = Path(stage)
    volume = Volume(out, size_mib, label)

    # Directories first, so every entry can name its child cluster.
    root = volume.reserve_dir()
    efi = volume.reserve_dir()
    unixnu = volume.reserve_dir()
    payloads = volume.reserve_dir()

    entries = {}                      # cluster -> list of 32-byte entries
    entries[root] = [sfn_entry(volume.label, LABEL_ATTR, 0, 0)]
    entries[efi] = []
    entries[unixnu] = []
    entries[payloads] = []

    entries[root] += dirents('EFI', b'EFI        ', DIR_ATTR, efi, 0)
    entries[efi] += dirents('UniXNU', b'UNIXNU     ', DIR_ATTR, unixnu, 0)
    entries[unixnu] += dirents('Payloads', b'PAYLOADS   ', DIR_ATTR, payloads, 0)

    short_names = {
        'kernel.macho': b'KERNEL~1MAC',
        'kernel.macho.adt': b'KERNEL~2ADT',
        'userland.img': b'USERLA~1IMG',
        'unixnu.toml': b'UNIXNU~1TOM',
    }
    for name in sorted(p.name for p in (stage / 'EFI/UniXNU').iterdir() if p.is_file()):
        path = stage / 'EFI/UniXNU' / name
        first, size = volume.reserve_file(path)
        entries[unixnu] += dirents(name, short_names[name], ARCHIVE, first, size)
        if size:
            pass
    for name in sorted(p.name for p in (stage / 'EFI/UniXNU/Payloads').iterdir() if p.is_file()):
        path = stage / 'EFI/UniXNU/Payloads' / name
        first, size = volume.reserve_file(path)
        entries[payloads] += dirents(name, short_names[name], ARCHIVE, first, size)

    volume.write_boot()
    for cluster, items in entries.items():
        volume.write_dir(cluster, items)
    volume.write_fats()
    volume.write_data()
    volume.close()
    return volume


def main():
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    stage, out, size_mib = sys.argv[1], sys.argv[2], int(sys.argv[3])
    label = sys.argv[4] if len(sys.argv) > 4 else 'VERMEERESP'
    volume = build(stage, out, size_mib, label)
    used = sum(p.stat().st_size for p in Path(stage).rglob('*') if p.is_file())
    print(f'{out}: {os.path.getsize(out)} bytes, {volume.fatsz} sectors per FAT, '
          f'{volume.clusters} clusters, {used} bytes of content')


if __name__ == '__main__':
    main()
