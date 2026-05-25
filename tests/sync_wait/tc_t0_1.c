/* TC-T0-1: Barrier Basic Release
 *
 * Three threads each call Sync_Wait(0b111) which has popcount=3.
 * All threads must arrive before any are released.
 *
 * Compile:
 *   aarch64-linux-gnu-gcc -static -o tc_t0_1 tests/sync_wait/tc_t0_1.c
 *
 * Run:
 *   ./tc_t0_1 <node_id>
 *   where node_id is 0, 1, or 2
 */

#include <stdint.h>

/* syscall numbers for ARM64 */
#define SYS_WRITE      64
#define SYS_SYNC_WAIT  436
#define SYS_EXIT       93

/* inline asm helper: syscall with 3 args */
static long syscall3(long num, long arg0, long arg1, long arg2) {
    register long x8 __asm__("x8") = num;
    register long x0 __asm__("x0") = arg0;
    register long x1 __asm__("x1") = arg1;
    register long x2 __asm__("x2") = arg2;
    __asm__ volatile("svc #0"
                     : "+r"(x0)
                     : "r"(x8), "r"(x1), "r"(x2)
                     : "memory");
    return x0;
}

/* inline asm helper: syscall with 1 arg */
static long syscall1(long num, long arg0) {
    register long x8 __asm__("x8") = num;
    register long x0 __asm__("x0") = arg0;
    __asm__ volatile("svc #0"
                     : "+r"(x0)
                     : "r"(x8)
                     : "memory");
    return x0;
}

static void write_stdout(const char *buf, int len) {
    syscall3(SYS_WRITE, 1 /* stdout */, (long)buf, (long)len);
}

static void print_int(int fd, int val) {
    char buf[16];
    int pos = 0;
    if (val == 0) {
        buf[pos++] = '0';
    } else {
        char tmp[16];
        int tmp_pos = 0;
        unsigned int u = (unsigned int)(val < 0 ? -val : val);
        while (u > 0) { tmp[tmp_pos++] = '0' + (u % 10); u /= 10; }
        if (val < 0) buf[pos++] = '-';
        while (tmp_pos > 0) buf[pos++] = tmp[--tmp_pos];
    }
    buf[pos++] = '\n';
    syscall3(SYS_WRITE, fd, (long)buf, (long)pos);
}

static void print_str(int fd, const char *s) {
    int len = 0;
    while (s[len]) len++;
    syscall3(SYS_WRITE, fd, (long)s, (long)len);
}

static void exit_program(int code) {
    syscall1(SYS_EXIT, code);
}

/* parse simple integer from string; returns 0 on failure */
static int parse_int(const char *s) {
    int val = 0;
    while (*s >= '0' && *s <= '9') {
        val = val * 10 + (*s - '0');
        s++;
    }
    return val;
}

/* Use int main for CRT-based startup.
 * gcc -static links against glibc which provides _start.
 */
int main(int argc, char **argv) {
    int node_id = 0;
    int mask = 0b111;  /* all 3 nodes participate */

    if (argc >= 2) {
        node_id = parse_int(argv[1]);
    }

    print_str(1, "BEFORE_BARRIER node=");
    print_int(1, node_id);

    /* Call Sync_Wait(mask) */
    syscall1(SYS_SYNC_WAIT, mask);

    print_str(1, "AFTER_BARRIER node=");
    print_int(1, node_id);

    exit_program(0);
    return 0;
}
