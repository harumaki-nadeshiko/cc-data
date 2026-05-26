/* TC-T0-4: Reusable Barrier
 *
 * All 3 threads use the same barrier (mask 0b111, popcount=3)
 * for two consecutive rounds.
 *
 * Round 1: all arrive -> release
 * Round 2: all arrive -> release (no stale state from round 1)
 *
 * All output lines are single write() calls for shared-output
 * global ordering assertions.
 *
 * Compile:
 *   aarch64-linux-gnu-gcc -static -o tc_t0_4 tests/sync_wait/tc_t0_4.c
 *
 * Run:
 *   ./tc_t0_4 <node_id>
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

static int fmt_int(char *buf, int p, int val) {
    if (val == 0) { buf[p++] = '0'; return p; }
    char tmp[16]; int tp = 0;
    unsigned u = (unsigned)(val < 0 ? -val : val);
    if (val < 0) buf[p++] = '-';
    while (u) { tmp[tp++] = '0' + (u % 10); u /= 10; }
    while (tp) buf[p++] = tmp[--tp];
    return p;
}

/* Emit "marker node=<nid>\n" in a single write() */
static void emit_event(const char *marker, int node_id) {
    char buf[256];
    int p = 0;
    while (*marker && p < 248) buf[p++] = *marker++;
    buf[p++]=' '; buf[p++]='n'; buf[p++]='o'; buf[p++]='d'; buf[p++]='e'; buf[p++]='=';
    p = fmt_int(buf, p, node_id);
    buf[p++]='\n';
    syscall3(SYS_WRITE, 1, (long)buf, (long)p);
}

static int parse_int(const char *s) {
    int v = 0; while (*s >= '0' && *s <= '9') { v = v*10 + (*s-'0'); s++; } return v;
}

int main(int argc, char **argv) {
    int node_id = 0;
    int mask = 0b111;
    if (argc >= 2) node_id = parse_int(argv[1]);

    /* Round 1 */
    emit_event("BEFORE_BARRIER_R1", node_id);
    syscall1(SYS_SYNC_WAIT, mask);
    emit_event("AFTER_BARRIER_R1", node_id);

    /* Round 2 - same barrier, must not be affected by round 1 state */
    emit_event("BEFORE_BARRIER_R2", node_id);
    syscall1(SYS_SYNC_WAIT, mask);
    emit_event("AFTER_BARRIER_R2", node_id);

    syscall1(SYS_EXIT, 0);
    return 0;
}
