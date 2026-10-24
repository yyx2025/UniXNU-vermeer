#!/usr/bin/env python3
"""Helpers shared by the vermeer storage-ABI and OS-root scripts.

Ported from UniXNU-qemu's scripts/qemu_esp.py. The QEMU board builds an ESP for
QEMU's own EDK II firmware; vermeer builds a payload volume for the Mu-Silicium
firmware on the device, so only the staging conventions are shared here:

  ROOT   the device repository (UniXNU-vermeer)
  WORK   the build tree of this board (out/vermeer)
  stage()  a directory holding UniXNU, DarwinBoot and a kernel, in the layout the
           board's firmware expects

The layout difference matters and is the reason this is not a straight copy:
vermeer's firmware reads \\EFI\\UniXNU and the payload volume from the boot
image's ramdisk, while QEMU's reads an ESP. stage() therefore only produces a
directory that mirrors that volume; the caller decides where to place it.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parent / 'out' / 'vermeer'

# Where the board's firmware looks for each file (FAT volume layout).
EFI_BOOT = Path('EFI/BOOT/BOOTAA64.EFI')
EFI_PAYLOADS = Path('EFI/UniXNU/Payloads')
EFI_CONFIG = Path('EFI/UniXNU/unixnu.toml')


def need(path, step):
    if not Path(path).is_file():
        raise SystemExit(f'{path} missing ({step})')
    return Path(path)


def stage(directory, kernel, arguments=''):
    """Mirror the firmware's volume into `directory` with the given kernel."""
    payload = need(WORK / 'payload/payload.img', 'unixnu build payload')
    unixnu = need(WORK / 'package/Build/Out/AARCH64-DEBUG/UniXNU.efi', 'unixnu build package')
    darwinboot = ROOT / 'build/darwinboot-efi/DarwinBoot.efi'
    if not darwinboot.is_file():
        subprocess.run([sys.executable, str(ROOT / 'scripts/build-darwinboot-efi.py')], check=True)

    directory = Path(directory)
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)

    # mcopy reads the FAT payload volume; MTOOLS_SKIP_CHECK tolerates the
    # small-cluster layout mkfs built for the firmware.
    subprocess.run(['mcopy', '-s', '-n', '-i', str(payload), '::/EFI', str(directory)],
                   check=True, env=dict(os.environ, MTOOLS_SKIP_CHECK='1'))
    (directory / EFI_BOOT).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(unixnu, directory / EFI_BOOT)
    (directory / EFI_PAYLOADS).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(darwinboot, directory / EFI_PAYLOADS / 'DarwinBoot.efi')
    shutil.copyfile(need(kernel, 'unixnu build kernel'), directory / EFI_PAYLOADS / 'kernel.macho')

    if arguments:
        config = directory / EFI_CONFIG
        if not config.is_file():
            raise SystemExit(f'{config} missing: the payload volume has no UniXNU config')
        config.write_text(config.read_text().replace('args = "', f'args = "{arguments} ', 1))
    return directory


def build_dir(*parts):
    """A directory under the board's build tree, created on demand."""
    path = Path(WORK, *parts)
    path.mkdir(parents=True, exist_ok=True)
    return path
