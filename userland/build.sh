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
    # -headerpad reserves room in the load-command area: adhoc.py has to insert
    # LC_CODE_SIGNATURE there, exactly like codesign would, and lld otherwise
    # packs the sections right behind the commands with no gap.
    "$LD" -arch arm64 -platform_version ios 17.0 17.0 -static -e _start \
        -headerpad 0x800 \
        -o "$out/launchd.macho" "$out/start.o" "$out/libc.o" "$out/init.o" "$@"
}

# lld rejects -no_pie on some versions and silently produces a PIE on others.
# Try the non-PIE form first, then fall back and record which one stuck.
if ! link -no_pie 2>"$out/link.log"; then
    echo "retrying without -no_pie (see link.log)" >&2
    link
fi

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
