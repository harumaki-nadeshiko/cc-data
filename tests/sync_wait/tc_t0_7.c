/* TC-T0-7: mask with bits beyond N=3 → syscall returns negative (error)
 *
 * The current topology has N=3 nodes (bits 0, 1, 2 valid).
 * Setting bit 3 or any higher bit must return -EINVAL without blocking.
 *
 * Compile:
 *   aarch64-linux-gnu-gcc -static -o tc_t0_7 tests/sync_wait/tc_t0_7.c
 *
 * Run:
 *   ./tc_t0_7
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

static void print_str(int fd, const char *s) {
    int len = 0; while (s[len]) len++;
    syscall3(SYS_WRITE, fd, (long)s, (long)len);
}

static void print_int(int fd, int val) {
    char buf[16]; int pos = 0;
    if (val == 0) { buf[pos++] = '0'; }
    else {
        char tmp[16]; int tp = 0;
        unsigned u = (unsigned)(val < 0 ? -val : val);
        while (u) { tmp[tp++] = '0' + (u % 10); u /= 10; }
        if (val < 0) buf[pos++] = '-';
        while (tp) buf[pos++] = tmp[--tp];
    }
    buf[pos++] = '\n';
    syscall3(SYS_WRITE, fd, (long)buf, (long)pos);
}

int main(int argc, char **argv) {
    print_str(1, "TC_T0_7_START\n");

    /* mask = 0b1000 = bit 3 set, which is beyond N=3 (bits 0-2) */
    long ret = syscall1(SYS_SYNC_WAIT, 8 /* bit 3 */);

    print_str(1, "TC_T0_7_MASK_BIT3=1\n");
    print_str(1, "TC_T0_7_RET=");
    print_int(1, (int)ret);

    if (ret < 0) {
        print_str(1, "TC_T0_7_PASS_ERROR_RETURNED\n");
    } else {
        print_str(1, "TC_T0_7_FAIL_NO_ERROR\n");
    }

    syscall1(SYS_EXIT, 0);
    return 0;
}
