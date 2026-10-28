// The only interface this program has: raw Darwin syscalls on arm64.
//
// Syscall numbers are copied from bsd/kern/syscalls.master in the pinned XNU
// tree, not from memory. Everything else (strings, formatting, the shell) is in
// this directory, so the image needs no SDK, no libSystem and no dyld.

#ifndef USERLAND_DARWIN_H
#define USERLAND_DARWIN_H

typedef unsigned char      u8;
typedef unsigned short     u16;
typedef unsigned int       u32;
typedef unsigned long      u64;
typedef signed   long      i64;
typedef unsigned long      usize;
typedef long               ssize;

#define NULL ((void *)0)

/* Syscall numbers (bsd/kern/syscalls.master). */
#define SYS_exit              1
#define SYS_fork              2
#define SYS_read              3
#define SYS_write             4
#define SYS_open              5
#define SYS_close             6
#define SYS_wait4             7
#define SYS_getpid           20
#define SYS_getuid           24
#define SYS_geteuid          25
#define SYS_access           33
#define SYS_kill             37
#define SYS_getppid          39
#define SYS_dup              41
#define SYS_sigaction        46
#define SYS_sigprocmask      48
#define SYS_reboot           55
#define SYS_execve           59
#define SYS_umask            60
#define SYS_munmap           73
#define SYS_mprotect         74
#define SYS_fcntl            92
#define SYS_select           93
#define SYS_gettimeofday    116
#define SYS_setsid          147
#define SYS_statfs          157
#define SYS_fstat           189
#define SYS_mmap            197
#define SYS_lseek           199
#define SYS_sysctl          202
#define SYS_semwait_signal  334
#define SYS_fstat64         339
#define SYS_getdirentries64 344
#define SYS_getentropy      500

/* open(2) flags. */
#define O_RDONLY   0x0000
#define O_WRONLY   0x0001
#define O_RDWR     0x0002
#define O_CREAT    0x0200
#define O_TRUNC    0x0400
#define O_NONBLOCK 0x0004

/* mmap(2). */
#define PROT_READ  0x01
#define PROT_WRITE 0x02
#define PROT_EXEC  0x04
#define MAP_PRIVATE 0x0002
#define MAP_ANON    0x1000
#define MAP_FAILED  ((void *)-1)

/* wait4(2). */
#define WNOHANG 0x00000001

/* sysctl(3) MIB pieces. */
#define CTL_KERN 1
#define CTL_HW   6
#define KERN_OSTYPE   1
#define KERN_VERSION  4
#define HW_MACHINE    1
#define HW_NCPU       3
#define HW_PAGESIZE   7
#define HW_CPUFREQ   15
#define HW_MEMSIZE   24

/* reboot(2). */
#define RB_AUTOBOOT 0x0100

struct timeval32 {
	i64 tv_sec;
	i64 tv_usec;
};

struct sysinfo_vals {
	i64 ncpu;
	i64 pagesize;
	i64 memsize;
	i64 cpufreq;
	char ostype[64];
	char version[192];
	char machine[64];
};

extern long _syscall_raw(long number, long a, long b, long c, long d, long e, long f);

static inline long sys_exit(int code)              { return _syscall_raw(SYS_exit, code, 0, 0, 0, 0, 0); }
static inline long sys_fork(void)                  { return _syscall_raw(SYS_fork, 0, 0, 0, 0, 0, 0); }
static inline long sys_read(int fd, void *b, usize n)  { return _syscall_raw(SYS_read, fd, (long)b, n, 0, 0, 0); }
static inline long sys_write(int fd, const void *b, usize n) { return _syscall_raw(SYS_write, fd, (long)b, n, 0, 0, 0); }
static inline long sys_open(const char *p, int f, int m) { return _syscall_raw(SYS_open, (long)p, f, m, 0, 0, 0); }
static inline long sys_close(int fd)               { return _syscall_raw(SYS_close, fd, 0, 0, 0, 0, 0); }
static inline long sys_wait4(int pid, int *st, int opt, void *ru) { return _syscall_raw(SYS_wait4, pid, (long)st, opt, (long)ru, 0, 0); }
static inline long sys_getpid(void)                { return _syscall_raw(SYS_getpid, 0, 0, 0, 0, 0, 0); }
static inline long sys_getppid(void)               { return _syscall_raw(SYS_getppid, 0, 0, 0, 0, 0, 0); }
static inline long sys_getuid(void)                { return _syscall_raw(SYS_getuid, 0, 0, 0, 0, 0, 0); }
static inline long sys_geteuid(void)               { return _syscall_raw(SYS_geteuid, 0, 0, 0, 0, 0, 0); }
static inline long sys_kill(int pid, int sig, int posix) { return _syscall_raw(SYS_kill, pid, sig, posix, 0, 0, 0); }
static inline long sys_execve(const char *f, char **a, char **e) { return _syscall_raw(SYS_execve, (long)f, (long)a, (long)e, 0, 0, 0); }
static inline long sys_access(const char *p, int m) { return _syscall_raw(SYS_access, (long)p, m, 0, 0, 0, 0); }
static inline long sys_setsid(void)                { return _syscall_raw(SYS_setsid, 0, 0, 0, 0, 0, 0); }
static inline long sys_umask(int m)                { return _syscall_raw(SYS_umask, m, 0, 0, 0, 0, 0); }
static inline long sys_lseek(int fd, i64 off, int whence) { return _syscall_raw(SYS_lseek, fd, off, whence, 0, 0, 0); }
static inline void *sys_mmap(void *a, usize l, int p, int f, int fd, i64 off) {
	return (void *)_syscall_raw(SYS_mmap, (long)a, l, p, f, fd, off);
}
static inline long sys_munmap(void *a, usize l)     { return _syscall_raw(SYS_munmap, (long)a, l, 0, 0, 0, 0); }
static inline long sys_gettimeofday(struct timeval32 *tv) { return _syscall_raw(SYS_gettimeofday, (long)tv, 0, 0, 0, 0, 0); }
static inline long sys_sysctl(int *name, u32 n, void *old, usize *oldlen, void *nw, usize newlen) {
	return _syscall_raw(SYS_sysctl, (long)name, n, (long)old, (long)oldlen, (long)nw, newlen);
}
static inline long sys_reboot(int how)              { return _syscall_raw(SYS_reboot, how, 0, 0, 0, 0, 0); }

/* nanosleep through __semwait_signal, which is how libSystem implements it too:
 * no semaphore, a relative timeout, and the thread blocks in the kernel. */
static inline long sys_nanosleep(i64 sec, i64 nsec) {
	return _syscall_raw(SYS_semwait_signal, 0, 0, 1, 1, sec, nsec);
}

#endif
