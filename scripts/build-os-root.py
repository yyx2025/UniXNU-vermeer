#!/usr/bin/env python3
"""Make what vermeer's verified-root profile needs besides the kernel step.

Ported from UniXNU-qemu's build-os-root.py. The steps that differ on this board
are marked "vermeer:" below; the rest are the board-independent owner-root
pipeline that the QEMU board proved out.

Run in the build tree after "unixnu build package kernel payload". In order:

1. build-storage-abi.py: the original block storage ABI the block drivers bind
   to (it boots that kernel in QEMU once). vermeer: this is what supplies the
   IOBlockStorageDevice definitions NativeUfs compiles against.
2. The OS disk image and the SystemOS image of the IPSW in
   .cache/firmware/target-volumes.
3. The owner key pair and the host verifier in build/verified-root-host.
4. The OS policy: signs the hash of every 4 KiB block of the OS disk image and
   verifies them all again; plus a 32 MiB APFS Data seed.
5. The same for the SystemOS image, under the same key.
6. build-verified-root-probe.py --component os --include-systemos: both policies
   and the verifier as a kernel archive (libVerifiedRootProbe.a), which is what
   `-Wl,-force_load` pulls in for CONFIG_XNU_VERIFIED_ROOT_PROBE=y.
7. The Data seed grown to 1 GiB and packed for DarwinBoot (DBRDISK1).
8. The original static and System trust caches as one bundle (AMFI needs it or
   launchd is refused).

Each step is skipped when its output exists.

Requires a macOS host: hdiutil, diskutil, xcrun.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--ipsw', type=Path, default=os.environ.get('UNIXNU_IPSW'),
                    help='local IPSW (default: $UNIXNU_IPSW)')
parser.add_argument('--openssl-prefix', type=Path, default=Path('/opt/homebrew/opt/openssl@3'),
                    help='OpenSSL 3 of the host verifier (brew install openssl@3)')
args = parser.parse_args()

volumes = ROOT / '.cache/firmware/target-volumes'
data = ROOT / '.cache/firmware/owner-data'


def step(*command):
    print('==', ' '.join(str(part) for part in command), flush=True)
    subprocess.run([sys.executable, *[str(part) for part in command]], cwd=ROOT, check=True)


# 1. storage ABI. vermeer-specific: NativeUfs is the block driver here, not VirtIO.
if not (ROOT / 'build/storage-abi/interface-audit.json').is_file():
    step(ROOT / 'scripts/build-storage-abi.py')

# 2. the two volume images. The components come from board.toml [userdata]:
#    OS <- OS, SystemOS <- Cryptex1,SystemOS.
for component, member in (('OS', '044-76040-038.dmg'),
                          ('Cryptex1,SystemOS', '044-76034-038.dmg')):
    if not (volumes / member).is_file():
        if not args.ipsw:
            parser.error('the disk images are extracted from the IPSW: pass --ipsw or set UNIXNU_IPSW')
        step(ROOT / 'scripts/extract-target-volume.py', component, '--ipsw', args.ipsw)

# 3. owner key pair (the private key stays in the build tree).
if not (ROOT / 'build/verified-root-host/keys/owner.pem').is_file():
    step(ROOT / 'kits/VerifiedRoot/tests/check.py', '--output', ROOT / 'build/verified-root-host',
         '--openssl-prefix', args.openssl_prefix)

# 4. OS policy + Data seed. Signing needs the volume mounted read-only.
os_policy = ROOT / 'build/verified-root-os/verification.json'
if not os_policy.is_file() or not (data / 'seed.json').is_file():
    mount = volumes / 'os-mount'
    mount.mkdir(exist_ok=True)
    attached = plistlib.loads(subprocess.check_output(
        ['hdiutil', 'attach', '-readonly', '-nobrowse', '-noverify', '-noautofsck',
         '-mountpoint', str(mount), '-plist', str(volumes / '044-76040-038.dmg')]))
    whole = next(e['dev-entry'] for e in attached['system-entities']
                 if e.get('content-hint') == 'GUID_partition_scheme')
    try:
        if not os_policy.is_file():
            step(ROOT / 'scripts/build-owner-os-policy.py')
        if not (data / 'seed.json').is_file():
            step(ROOT / 'scripts/prepare-owner-data-fixture.py', '--output', data)
    finally:
        subprocess.run(['hdiutil', 'detach', whole], check=True)

# 5. SystemOS policy, same key.
if not (ROOT / 'build/verified-root-systemos/verification.json').is_file():
    step(ROOT / 'scripts/build-owner-systemos-policy.py')

# 6. the verified-root archive the kernel force-loads.
if not (ROOT / 'build/verified-root-os-xnu-bundle/libVerifiedRootProbe.a').is_file():
    # The kernel build creates EXPORT_HDRS/libsa under build/xnu/obj only; the
    # bsd compile record names the one under ios-obj, and the probe compiles
    # with -Werror, so a missing include directory stops it.
    (ROOT / 'build/xnu/ios-obj/EXPORT_HDRS/libsa').mkdir(parents=True, exist_ok=True)
    step(ROOT / 'scripts/build-verified-root-probe.py', '--component', 'os', '--include-systemos')

# 7. the Data seed grown to 1 GiB and packed for DarwinBoot.
seed = data / 'owner-data-1g.dbr'
if not seed.is_file():
    raw = data / 'owner-data-1g.raw'
    with raw.open('wb') as handle:                     # sparse: only the seed is written
        handle.truncate(1 << 30)
        handle.seek(0)
        handle.write((data / 'owner-data-32.cdr').read_bytes())
    try:
        attached = plistlib.loads(subprocess.check_output(
            ['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage',
             '-nomount', '-nobrowse', '-plist', str(raw)]))
        store = Path(attached['system-entities'][0]['dev-entry']).name
        try:
            listing = plistlib.loads(subprocess.check_output(['diskutil', 'apfs', 'list', '-plist']))
            container = next(entry['ContainerReference'] for entry in listing['Containers']
                             if any(s['DeviceIdentifier'] == store
                                    for s in entry.get('PhysicalStores', [])))
            subprocess.run(['diskutil', 'apfs', 'resizeContainer', container, '0'], check=True)
        finally:
            subprocess.run(['hdiutil', 'detach', store, '-force'], check=True)

        header = raw.open('rb').read(4096)
        blocks = int.from_bytes(header[40:48], 'little')
        if header[32:36] != b'NXSB' or not (1 << 30) - (64 << 20) <= blocks * 4096 <= 1 << 30:
            raise SystemExit('the grown Data seed is not a 1 GiB APFS container')

        # The seed's files belong to uid 501 (mobile on iPadOS), the user
        # SpringBoard and its cfprefsd run as.
        mount = data / 'mount'
        attached = plistlib.loads(subprocess.check_output(
            ['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage',
             '-nobrowse', '-owners', 'on', '-mountpoint', str(mount), '-plist', str(raw)]))
        try:
            preferences = mount / 'mobile/Library/Preferences/com.apple.springboard.plist'
            values = plistlib.loads(preferences.read_bytes()) if preferences.exists() else {}
            # Without input, iPadOS dims and sleeps half a minute after the
            # welcome screen; ArmCpu does not support sleep.
            values.update(SBDisableAutoDim=True, SBDontDimOrLockOnAC=True, SBDisableIdleSleep=True)
            preferences.write_bytes(plistlib.dumps(values, fmt=plistlib.FMT_BINARY))
            preferences.chmod(0o600)
            if preferences.stat().st_uid != 501:
                raise SystemExit(f'{preferences} does not belong to uid 501 (mobile)')
        finally:
            subprocess.run(['hdiutil', 'detach', attached['system-entities'][0]['dev-entry']],
                           check=True)
        step(ROOT / 'drivers/DarwinBoot/Test/packed_ramdisk.py', raw, seed)
    finally:
        raw.unlink()

# 8. the trust-cache bundle AMFI needs. vermeer: the static trust cache of this
#    board's IPSW comes from the run's own cache, not from a reference checkout.
trust = ROOT / 'build/original-static-system-trust/original-static-system.bundle'
if not trust.is_file():
    static = volumes / 'static-trustcache.bin'
    if not static.is_file():
        blob = (ROOT.parent.parent / 'ipsw/21H461/044-76040-038.dmg.trustcache').read_bytes()

        def der(buffer, at):
            tag, size = buffer[at], buffer[at + 1]
            at += 2
            if size & 0x80:
                size, at = int.from_bytes(buffer[at:at + (size & 0x7f)], 'big'), at + (size & 0x7f)
            return tag, buffer[at:at + size], at + size

        _, sequence, _ = der(blob, 0)
        fields, cursor = [], 0
        while cursor < len(sequence):
            _, value, cursor = der(sequence, cursor)
            fields.append(value)
        module = fields[3]
        if fields[:2] != [b'IM4P', b'trst'] or (hashlib.sha256(module).hexdigest() !=
                                                'da88460e6e513438d5307e5c526f76497a74754476a24da29f239f0c90120fc2'):
            raise SystemExit('unexpected StaticTrustCache IM4P')
        static.write_bytes(module)
    ipsw = ROOT / 'refer/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw'
    if not ipsw.exists():
        if not args.ipsw:
            parser.error('the System trust cache is read from the IPSW: pass --ipsw or set UNIXNU_IPSW')
        ipsw.symlink_to(Path(args.ipsw).resolve())
    step(ROOT / 'scripts/build-original-system-trust.py')

volume = json.loads((data / 'seed.json').read_text())['volume_uuid']
print('vermeer OS root inputs ready. Then:')
print(f'  export XNU_OWNER_DATA_VOLUME_UUID={volume}')
print('  unixnu defconfig vermeer_verified_defconfig; unixnu build kernel')
print(f'  unixnu build payload ... --trust-cache {trust} --data-ramdisk {seed}')
