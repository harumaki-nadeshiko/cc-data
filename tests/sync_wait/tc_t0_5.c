/* TC-T0-5: mask=0 -> syscall returns negative (error), does not block
 *
 * mask=0 is invalid (popcount=0 means no threads expected).
 * The syscall must return -EINVAL immediately without suspending
 * the calling thread.
 *
 * Outputs:
 *   TC_T0_5_START
 *   TC_T0_5_RET=<val>        (raw return value)
 *   SYNC_WAIT_RET=<val>      (machine-parseable return value)
 *   TC_T0_5_PASS_ERROR_RETURNED  or  TC_T0_5_FAIL_NO_ERROR
 *
 * Compile:
 *   aarch64-linux-gnu-gcc -static -o tc_t0_5 tests/sync_wait/tc_t0_5.c
 *
 * Run:
 *   ./tc_t0_5
 */

#include <stdint.h>

#define SYS_WRITE      64
#define SYS_SYNC_WAIT  436
#define SYS_EXIT       93

static long syscall3(long num, long arg0, long arg1, long arg2) {
    register long x8 __asm__("x8") = num;
    register long x0 __asm__("x0") = arg0;
    register long x1 __asm__("x1") = arg1;
    register long x2 __asm__("x2") = arg2;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}

static long syscall1(long num, long arg0) {
    register long x8 __asm__("x8") = num;
    register long x0 __asm__("x0") = arg0;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8) : "memory");
    return x0;
}

/* Write string + newline in a single write() */
static void emit_line(const char *s) {
    int len = 0; while (s[len]) len++;
    syscall3(SYS_WRITE, 1, (long)s, (long)len);
}

/* Emit "prefix=<val>\n" in a single write() */
static void emit_tag_int(const char *prefix, int val) {
    char buf[256];
    int p = 0;
    while (*prefix && p < 240) buf[p++] = *prefix++;
    buf[p++] = '=';
    /* format val */
    if (val == 0) {
        buf[p++] = '0';
    } else {
        char tmp[16]; int tp = 0;
        unsigned u = (unsigned)(val < 0 ? -val : val);
        if (val < 0) buf[p++] = '-';
        while (u) { tmp[tp++] = '0' + (u % 10); u /= 10; }
        while (tp) buf[p++] = tmp[--tp];
    }
    buf[p++] = '\n';
    syscall3(SYS_WRITE, 1, (long)buf, (long)p);
}

int main(int argc, char **argv) {
    emit_line("TC_T0_5_START\n");

    long ret = syscall1(SYS_SYNC_WAIT, 0 /* mask=0, invalid */);

    /* Machine-parseable return value (for test script) */
    emit_tag_int("SYNC_WAIT_RET", (int)ret);

    /* Human-readable log */
    emit_tag_int("TC_T0_5_RET", (int)ret);

    if (ret < 0) {
        emit_line("TC_T0_5_PASS_ERROR_RETURNED\n");
    } else {
        emit_line("TC_T0_5_FAIL_NO_ERROR\n");
    }

    syscall1(SYS_EXIT, 0);
    return 0;
}
