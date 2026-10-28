#!/usr/bin/env bash
# Build the vermeer userland image on any Linux or macOS host with clang + lld.
#
# Nothing here needs an Apple SDK: the program includes no system header, so the
# compile is a plain freestanding cross-compile and the link is ld64.lld with no
# syslibroot. The result is one static Mach-O, ad-hoc signed by adhoc.py, wrapped
# into the N4KROOT container that mockfs reads.
#
#   userland/build/launchd.macho              the executable (also the CD source)
#   userland/build/kernel.macho.trustcache    names its cdhash for AMFI
#   userland/build/userland.img               what goes into the payload as userland.img
#   userland/build/userland.json              sizes, hashes, cdhash

set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="${1:-$here/build}"
mkdir -p "$out"

CC="${CC:-clang}"
LD="${LD:-}"

find_ld() {
    if [ -n "$LD" ] && command -v "$LD" >/dev/null 2>&1; then return; fi
    for candidate in ld64.lld ld64.lld-20 ld64.lld-19 ld64.lld-18 ld64.lld-17 ld64.lld-16; do
        if command -v "$candidate" >/dev/null 2>&1; then LD="$(command -v "$candidate")"; return; fi
    done
    # Versioned LLVM installs keep it next to clang.
    local dir
    dir="$(dirname "$(command -v "$CC")")"
    for candidate in "$dir"/ld64.lld* /usr/lib/llvm-*/bin/ld64.lld; do
        if [ -x "$candidate" ]; then LD="$candidate"; return; fi
    done
    echo "no ld64.lld found; install lld" >&2
    exit 1
}

find_ld
echo "compiler: $("$CC" --version | head -n 1)"
echo "linker:   $LD"

# -ffreestanding -nostdlibinc: no system headers exist for this target anyway.
# No unwind tables and no stack protector: there is no runtime to service them.
CFLAGS=(
    -target aarch64-apple-ios17.0
    -std=c11 -O2
    -ffreestanding -fno-builtin -nostdlibinc
    -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables
    -fno-common -Wall -Wextra
    -I"$here"
)

"$CC" "${CFLAGS[@]}" -c "$here/start.S" -o "$out/start.o"
"$CC" "${CFLAGS[@]}" -c "$here/libc.c"  -o "$out/libc.o"
"$CC" "${CFLAGS[@]}" -c "$here/init.c"  -o "$out/init.o"

link() {
    "$LD" -arch arm64 -platform_version ios 17.0 17.0 -static -e _start \
        -o "$out/launchd.macho" "$out/start.o" "$out/libc.o" "$out/init.o" "$@" 2>>"$out/link.log"
}

# Two independent things the linker has to get right, and neither is portable
# across lld versions:
#
#   padding    adhoc.py has to insert a 16-byte LC_CODE_SIGNATURE where codesign
#              would, which needs free space in the load-command area. -headerpad
#              asks for it directly; -headerpad_max_install_names is the older
#              spelling some builds only accept.
#   non-PIE    a static image has nothing to slide, so it should not be a PIE.
#
# Try the richest form first and fall back one flag at a time, then verify the
# result rather than trusting the flag that was accepted.
: >"$out/link.log"
linked=0
for attempts in "-headerpad 0x800 -no_pie" "-headerpad 0x800" \
                "-headerpad_max_install_names -no_pie" "-headerpad_max_install_names" ""; do
    # shellcheck disable=SC2086
    if link $attempts; then
        echo "linked with: $attempts"
        linked=1
        break
    fi
done
if [ "$linked" -ne 1 ]; then
    echo "link failed; see $out/link.log" >&2
    exit 1
fi

# The signing step needs room in the header. Check it here so a missing pad is a
# build error with an explanation, not a signature the kernel will refuse.
python3 - "$out/launchd.macho" <<'PY'
import struct, sys
data = open(sys.argv[1], 'rb').read()
ncmds, sizeofcmds = struct.unpack_from('<II', data, 16)
low, off = len(data), 32
for _ in range(ncmds):
    cmd, size = struct.unpack_from('<II', data, off)
    if cmd == 0x19:
        fileoff, = struct.unpack_from('<Q', data, off + 40)
        nsects, = struct.unpack_from('<I', data, off + 64)
        sect = off + 72
        for _ in range(nsects):
            ssize, = struct.unpack_from('<Q', data, sect + 40)
            soff, = struct.unpack_from('<I', data, sect + 48)
            if ssize:
                low = min(low, fileoff + soff)
            sect += 80
    off += size
gap = low - (32 + sizeofcmds)
print(f'header gap: {gap} bytes')
sys.exit(0 if gap >= 16 else 1)
PY

python3 "$here/adhoc.py" "$out/launchd.macho" \
    --identifier org.vermeer.userland \
    --trust-cache "$out/kernel.macho.trustcache" \
    --report "$out/userland.json"

python3 "$here/mkn4kroot.py" \
    --launch "$out/launchd.macho" \
    --test "$here/motd.txt" \
    --output "$out/userland.img" \
    --report "$out/n4kroot.json"

ls -l "$out"
