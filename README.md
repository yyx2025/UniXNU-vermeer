<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/logo-light.svg">
    <img alt="UniXNU" src="assets/logo-light.svg" width="200">
  </picture>
</p>

<p align="center">
  <a href="https://unixnu.com"><img alt="Documentation" src="https://img.shields.io/badge/docs-unixnu.com-2A5BD7?logo=readthedocs&logoColor=white"></a>
  <a href="https://t.me/unixnu"><img alt="Telegram" src="https://img.shields.io/badge/Telegram-@unixnu-26A5E4?logo=telegram&logoColor=white"></a>
  <a href="https://x.com/unixnu"><img alt="X" src="https://img.shields.io/badge/X-@unixnu-000000?logo=x&logoColor=white"></a>
</p>

# UniXNU-vermeer

Device repository for the Redmi K70 (vermeer, SM8550). It holds the board file, kernel defconfig,
device scripts, drivers and package. Ported from [UniXNU-sheng](https://github.com/UniXNU/UniXNU-sheng):
same SM8550 SoC and the same stock UEFI memory windows, so UFS, AES, GIC, timers and the serial port
are the same blocks.

- [Documentation](https://unixnu.com)
- [Porting guide](https://unixnu.com/porting/)
- [Build](https://unixnu.com/building)

Firmware: [uefi-vermeer](https://github.com/yyx2025/uefi-vermeer).

Build with the `unixnu` driver of [UniXNU](https://github.com/UniXNU/UniXNU). Clone both repositories
with `--recurse-submodules`; `unixnu fetch` also updates the module submodules.

## Device support

The boot chain is being qualified on this board. **Nothing in this table is hardware-verified on the
K70 yet** — the sheng port supplies the SoC-level wiring, and every row below is where this board
still differs.

| Feature | State | Notes |
| --- | --- | --- |
| UFS | 🔧 | Native UFS driver ported unchanged from sheng; not yet observed on K70 hardware |
| Display | 🔧 | Mu-Silicium vermeerPkg sets the 1440x3200 panel PCD; the splash carveout is from the K70 dtbo |
| GPU | ❌ | `CONFIG_XNU_NATIVE_ADRENO=n`. The driver needs `build/sheng-gmu/ShengGmuSource.h`, which is generated from SHA-pinned Linux a6xx sources plus the sheng DTS; neither is in a clean checkout, and the K70 GMU window is `0x3d68000` against sheng's `0x3d6a000` |
| Touch | ❌ | **Not the same controller as sheng.** The K70 `dtbo.img` declares `goodix,gt9916S` on SE4 (GPIO 44-47, IRQ 25, reset 24) and a secondary `gt9916S2` on SE8 (GPIO 56-59). The linked `NativeNovatekTouch` driver targets sheng's Novatek NT36532E and is not wired into this build |
| Wi-Fi | ❌ | |
| Bluetooth | ❌ | |
| Camera | ❌ | |
| USB | ❌ | |
| Audio | ❌ | |
| Sensors | ❌ | |
| Battery driver | ❌ | |
| MiPPS | ❌ | |
| Suspend and resume | ❌ | |

🔧 ported but unverified, ❌ not available.

### Why the GPU and the legacy-display/PMGR instruments are off

Both `build-sheng-platform-power.py` and `build-native-legacy-display.py` verify their output against
a `native-owner-ufs-*-candidate.macho` produced by a live-device GDB session. A clean CI checkout
cannot reproduce that evidence, so the builders are absent from `[kernel] archives` and the matching
`CONFIG_XNU_*` flags are off. Re-adding one means first recording its evidence on hardware.

## Known gaps before a first boot

1. `[userdata]` in `board.toml` still carries sheng's placeholder GPT geometry. It needs the K70
   `userdata` entry (`fastboot oem get-partition-info userdata` or a GPT dump).
2. `[payload.adt] unique_chip_id` is `"required"` and is supplied as a workflow input or the
   `UNIXNU_UNIQUE_CHIP_ID` secret. Without it the payload and boot image steps are skipped.
3. Touch has no driver for the Goodix GT9916S in this repository.

## License

BSD-2-Clause-Patent. See [LICENSE](LICENSE).
