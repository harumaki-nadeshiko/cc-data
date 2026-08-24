/* TC-T0-3: Multi-Thread Same Node Count
 *
 * Node0 starts 2 threads but only 1 calls the barrier.
 * Node1 and Node2 each have 1 thread that calls the barrier.
 * Total: 3 threads call the barrier (mask 0b111, popcount=3).
 * The 4th thread on Node0 that does NOT call the barrier
 * should not block or affect barrier convergence.
 *
 * All output lines are single write() calls.
 *
 * Compile:
 *   aarch64-linux-gnu-gcc -static -o tc_t0_3_caller tests/sync_wait/tc_t0_3.c -DCALLER=1
 *   aarch64-linux-gnu-gcc -static -o tc_t0_3_noncaller tests/sync_wait/tc_t0_3.c -DCALLER=0
 *
 * Run:
 *   ./tc_t0_3_caller <node_id>     (for nodes 0-2, caller threads)
 *   ./tc_t0_3_noncaller <node_id>  (for node 0 extra non-caller thread)
 */

#include <stdint.h>

#define SYS_WRITE      64
#define SYS_SYNC_WAIT  436
#define SYS_EXIT       93

#ifndef CALLER
#define CALLER 1
#endif

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

/* Emit: "marker node=<nid>\n" in a single write() */
static void emit_event(const char *marker, int node_id) {
    char buf[256];
    int p = 0;
    while (*marker && p < 248) buf[p++] = *marker++;
    buf[p++]=' '; buf[p++]='n'; buf[p++]='o'; buf[p++]='d'; buf[p++]='e'; buf[p++]='=';
    p = fmt_int(buf, p, node_id);
    buf[p++]='\n';
    syscall3(SYS_WRITE, 1, (long)buf, (long)p);
}

/* Emit: "marker\n" (plain string with newline) */
static void emit_str(const char *s) {
    int len = 0;
    while (s[len]) len++;
    syscall3(SYS_WRITE, 1, (long)s, (long)len);
}

static int parse_int(const char *s) {
    int v = 0; while (*s >= '0' && *s <= '9') { v = v*10 + (*s-'0'); s++; } return v;
}

int main(int argc, char **argv) {
    int node_id = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);

    if (CALLER) {
        emit_event("BEFORE_BARRIER CALLER", node_id);

        /* mask 0b111 = popcount 3, expecting 3 caller threads */
        syscall3(SYS_SYNC_WAIT, 0b111, 1, 0);

        emit_event("AFTER_BARRIER CALLER", node_id);
    } else {
        emit_event("NON_CALLER", node_id);
        /* This thread does NOT call Sync_Wait.
         * It must not block the barrier convergence. */
        emit_event("NON_CALLER_DONE", node_id);
    }

    syscall1(SYS_EXIT, 0);
    return 0;
}
