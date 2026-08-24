/* TC-T0-6: mask with high 32 bits set -> syscall returns negative (error)
 *
 * The current Sync_Wait implementation only supports 32-bit node_mask.
 * Passing a mask with any of the upper 32 bits set must return -EINVAL
 * without blocking.
 *
 * Outputs:
 *   TC_T0_6_START
 *   TC_T0_6_MASK_HI32=1
 *   SYNC_WAIT_RET=<val>      (machine-parseable return value)
 *   TC_T0_6_RET=<val>        (human-readable log)
 *   TC_T0_6_PASS_ERROR_RETURNED  or  TC_T0_6_FAIL_NO_ERROR
 *
 * Compile:
 *   aarch64-linux-gnu-gcc -static -o tc_t0_6 tests/sync_wait/tc_t0_6.c
 *
 * Run:
 *   ./tc_t0_6
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

static void emit_line(const char *s) {
    int len = 0; while (s[len]) len++;
    syscall3(SYS_WRITE, 1, (long)s, (long)len);
}

static void emit_tag_int(const char *prefix, int val) {
    char buf[256];
    int p = 0;
    while (*prefix && p < 240) buf[p++] = *prefix++;
    buf[p++] = '=';
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
    emit_line("TC_T0_6_START\n");

    /* mask with bit 33 set (0x1_0000_0001).
     * The high 32 bits (0x1) should trigger -EINVAL. */
    long mask = 0x100000001LL;
    long ret = syscall3(SYS_SYNC_WAIT, mask, 1, 0);

    emit_line("TC_T0_6_MASK_HI32=1\n");

    /* Machine-parseable return value (for test script) */
    emit_tag_int("SYNC_WAIT_RET", (int)ret);

    /* Human-readable log */
    emit_tag_int("TC_T0_6_RET", (int)ret);

    if (ret < 0) {
        emit_line("TC_T0_6_PASS_ERROR_RETURNED\n");
    } else {
        emit_line("TC_T0_6_FAIL_NO_ERROR\n");
    }

    syscall1(SYS_EXIT, 0);
    return 0;
}
