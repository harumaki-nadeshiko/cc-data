/* TC-T0-6: mask with high 32 bits set → syscall returns negative (error)
 *
 * The current Sync_Wait implementation only supports 32-bit node_mask.
 * Passing a mask with any of the upper 32 bits set must return -EINVAL
 * without blocking.
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
    print_str(1, "TC_T0_6_START\n");

    /* mask with bit 33 set (0x1_0000_0001).
     * The high 32 bits (0x1) should trigger -EINVAL. */
    long mask = 0x100000001LL;
    long ret = syscall1(SYS_SYNC_WAIT, mask);

    print_str(1, "TC_T0_6_MASK_HI32=1\n");
    print_str(1, "TC_T0_6_RET=");
    print_int(1, (int)ret);

    if (ret < 0) {
        print_str(1, "TC_T0_6_PASS_ERROR_RETURNED\n");
    } else {
        print_str(1, "TC_T0_6_FAIL_NO_ERROR\n");
    }

    syscall1(SYS_EXIT, 0);
    return 0;
}
