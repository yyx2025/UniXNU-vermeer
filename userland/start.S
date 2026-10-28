// Freestanding entry point for the static arm64 Darwin userland.
//
// Nothing here depends on a dynamic linker, a C runtime or libSystem: the kernel
// execs this image and jumps to _start with the Darwin process stack, which is
//
//   [sp + 0]                    argc
//   [sp + 8 .. 8*argc]          argv[0..argc-1]
//   [sp + 8*argc + 8]           NULL
//   ...                         envp, then apple[]
//
// _entry() takes that stack pointer as its only argument so the C side never has
// to guess how the ABI delivered argv.

    .text
    .globl  _start
    .globl  _syscall_raw
    .p2align 2

// void _start(void)
_start:
    mov     x29, #0                 // terminate the frame chain for unwinders
    mov     x30, #0
    mov     x0, sp                  // &argc
    bl      _entry
1:  b       1b                      // _entry never returns

// long _syscall_raw(long number, long a, long b, long c, long d, long e, long f)
//
// Darwin puts the syscall number in x16 and the first argument in x0, then
// reports failure with the carry flag instead of a sentinel return value. The
// number arrives in x0 here because that is what the C caller can allocate, so
// the whole set is shifted down one register before the trap.
_syscall_raw:
    mov     x16, x0
    mov     x0, x1
    mov     x1, x2
    mov     x2, x3
    mov     x3, x4
    mov     x4, x5
    mov     x5, x6
    svc     #0x80
    b.cs    2f
    ret
2:  neg     x0, x0                  // carry set: x0 holds errno, return -errno
    ret
