/* TC-T0-2: Barrier Isolation By Node Mask
 *
 * Node0/1 use Sync_Wait(0b011) - popcount=2, independent barrier
 * Node2 uses Sync_Wait(0b100) - popcount=1, separate barrier
 *
 * Verifies that different node_mask values create independent barrier
 * instances. Node2 completes immediately (1 of 1), Node0/1 wait for
 * each other (2 of 2).
 *
 * Compile:
 *   aarch64-linux-gnu-gcc -static -o tc_t0_2 tests/sync_wait/tc_t0_2.c
 *
 * Run:
 *   ./tc_t0_2 <node_id>
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
    int len = 0;
    while (s[len]) len++;
    syscall3(SYS_WRITE, fd, (long)s, (long)len);
}

static void print_int(int fd, int val) {
    char buf[16];
    int pos = 0;
    if (val == 0) { buf[pos++] = '0'; }
    else {
        char tmp[16]; int tp = 0;
        unsigned u = (unsigned)(val < 0 ? -val : val);
        while (u) { tmp[tp++] = '0'+(u%10); u/=10; }
        if (val < 0) buf[pos++]='-';
        while (tp) buf[pos++]=tmp[--tp];
    }
    buf[pos++]='\n';
    syscall3(SYS_WRITE, fd, (long)buf, (long)pos);
}

static int parse_int(const char *s) {
    int v = 0;
    while (*s >= '0' && *s <= '9') { v = v*10 + (*s-'0'); s++; }
    return v;
}

int main(int argc, char **argv) {
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);

    /* Node0 and Node1 share barrier A (mask 0b011, popcount=2).
     * Node2 uses barrier B (mask 0b100, popcount=1). */
    int mask = (node_id <= 1) ? 0b011 : 0b100;

    print_str(1, "BEFORE_BARRIER node=");
    print_int(1, node_id);
    print_str(1, " mask=");
    print_int(1, mask);

    syscall1(SYS_SYNC_WAIT, mask);

    print_str(1, "AFTER_BARRIER node=");
    print_int(1, node_id);
    print_str(1, " mask=");
    print_int(1, mask);

    syscall1(SYS_EXIT, 0);
    return 0;
}
