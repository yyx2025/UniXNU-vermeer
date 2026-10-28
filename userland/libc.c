// The parts of a C runtime this userland needs, written here because nothing is
// allowed to come from libSystem: strings, a small printf, and the console.
//
// Output goes to /dev/console. On this board the kernel has a graphics console
// (pexpert/arm/pe_init.c calls PE_initialize_console with kPEGraphicsMode once
// DarwinBoot has put the GOP frame buffer into boot_args->Video), so a write to
// /dev/console is painted on the panel by XNU itself. A user process never
// touches the frame buffer MMIO: it is not mapped for EL0, and it does not need
// to be.

#include "darwin.h"

void *memset(void *d, int c, usize n)
{
	u8 *p = (u8 *)d;
	while (n--) {
		*p++ = (u8)c;
	}
	return d;
}

void *memcpy(void *d, const void *s, usize n)
{
	u8 *p = (u8 *)d;
	const u8 *q = (const u8 *)s;
	while (n--) {
		*p++ = *q++;
	}
	return d;
}

usize strlen(const char *s)
{
	usize n = 0;
	while (s && s[n]) {
		n++;
	}
	return n;
}

int strcmp(const char *a, const char *b)
{
	while (*a && *a == *b) {
		a++;
		b++;
	}
	return (int)(u8)*a - (int)(u8)*b;
}

int strncmp(const char *a, const char *b, usize n)
{
	while (n && *a && *a == *b) {
		a++;
		b++;
		n--;
	}
	return n ? (int)(u8)*a - (int)(u8)*b : 0;
}

char *strcpy(char *d, const char *s)
{
	char *r = d;
	while ((*d++ = *s++)) {
	}
	return r;
}

int starts_with(const char *s, const char *prefix)
{
	return strncmp(s, prefix, strlen(prefix)) == 0;
}

/* ---- console ---------------------------------------------------------- */

static int  con_fd = -1;
static char con_buf[512];
static usize con_len;

static void con_flush(void)
{
	if (con_len) {
		sys_write(con_fd >= 0 ? con_fd : 1, con_buf, con_len);
		con_len = 0;
	}
}

int console_open(void)
{
	if (con_fd >= 0) {
		return con_fd;
	}
	con_fd = (int)sys_open("/dev/console", O_RDWR, 0);
	if (con_fd < 0) {
		con_fd = (int)sys_open("/dev/console0", O_RDWR, 0);
	}
	if (con_fd < 0) {
		/* No devfs node: fd 1 was inherited, so use it rather than give up. */
		con_fd = 1;
	}
	return con_fd;
}

static void con_putc(char c)
{
	if (con_len == sizeof(con_buf)) {
		con_flush();
	}
	con_buf[con_len++] = c;
	if (c == '\n') {
		con_flush();
	}
}

void con_write(const char *s, usize n)
{
	for (usize i = 0; i < n; i++) {
		con_putc(s[i]);
	}
	con_flush();
}

void con_puts(const char *s)
{
	con_write(s, strlen(s));
}

/* ---- formatting ------------------------------------------------------- */

struct out {
	void (*put)(char);
};

static void emit_str(struct out *o, const char *s)
{
	if (!s) {
		s = "(null)";
	}
	while (*s) {
		o->put(*s++);
	}
}

static void emit_uint(struct out *o, u64 v, u32 base, int upper, int pad_to, char pad)
{
	char tmp[32];
	int  n = 0;

	if (v == 0) {
		tmp[n++] = '0';
	}
	while (v) {
		u32 d = (u32)(v % base);
		tmp[n++] = (char)(d < 10 ? '0' + d : (upper ? 'A' : 'a') + d - 10);
		v /= base;
	}
	while (n < pad_to) {
		tmp[n++] = pad;
	}
	while (n) {
		o->put(tmp[--n]);
	}
}

static void emit_signed(struct out *o, i64 v, int pad_to)
{
	if (v < 0) {
		o->put('-');
		emit_uint(o, (u64)(-v), 10, 0, pad_to - 1, ' ');
	} else {
		emit_uint(o, (u64)v, 10, 0, pad_to, ' ');
	}
}

static void format(struct out *o, const char *fmt, __builtin_va_list ap)
{
	while (*fmt) {
		if (*fmt != '%') {
			o->put(*fmt++);
			continue;
		}
		fmt++;
		char pad = ' ';
		int  width = 0;

		/* Flags: '0' selects zero padding; '-' '+' and ' ' are accepted and ignored
		 * rather than printed, so a format string copied from libc behaves. */
		for (;;) {
			if (*fmt == '0') {
				pad = '0';
				fmt++;
			} else if (*fmt == '-' || *fmt == '+' || *fmt == ' ') {
				fmt++;
			} else {
				break;
			}
		}
		while (*fmt >= '0' && *fmt <= '9') {
			width = width * 10 + (*fmt++ - '0');
		}
		/* Length modifiers are accepted and ignored: everything is 64-bit here. */
		while (*fmt == 'l' || *fmt == 'z' || *fmt == 'h') {
			fmt++;
		}
		switch (*fmt) {
		case 's':
			emit_str(o, __builtin_va_arg(ap, const char *));
			break;
		case 'c':
			o->put((char)__builtin_va_arg(ap, int));
			break;
		case 'd':
		case 'i':
			emit_signed(o, __builtin_va_arg(ap, i64), width);
			break;
		case 'u':
			emit_uint(o, __builtin_va_arg(ap, u64), 10, 0, width, pad);
			break;
		case 'x':
			emit_uint(o, __builtin_va_arg(ap, u64), 16, 0, width, pad);
			break;
		case 'X':
			emit_uint(o, __builtin_va_arg(ap, u64), 16, 1, width, pad);
			break;
		case 'p':
			o->put('0');
			o->put('x');
			emit_uint(o, (u64)(usize)__builtin_va_arg(ap, void *), 16, 0, 0, ' ');
			break;
		case '%':
			o->put('%');
			break;
		case 0:
			return;
		default:
			o->put('%');
			o->put(*fmt);
			break;
		}
		fmt++;
	}
}

static void console_sink(char c)
{
	con_putc(c);
}

void printf(const char *fmt, ...)
{
	struct out o = { console_sink };
	__builtin_va_list ap;

	__builtin_va_start(ap, fmt);
	format(&o, fmt, ap);
	__builtin_va_end(ap);
}

void halt_forever(void)
{
	for (;;) {
		sys_nanosleep(3600, 0);
	}
}
