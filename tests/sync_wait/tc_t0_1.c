/* TC-T0-1: Barrier Basic Release
 *
 * Three threads each call Sync_Wait(0b111) which has popcount=3.
 * All threads must arrive before any are released.
 *
 * All output lines are single write() calls to ensure atomicity
 * in a shared output file for global ordering assertions.
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

/* Write an integer into buf at position p; return new position.
 * Handles negative values. Does NOT add newline. */
static int fmt_int(char *buf, int p, int val) {
    if (val == 0) {
        buf[p++] = '0';
    } else {
        char tmp[16];
        int tp = 0;
        unsigned int u = (unsigned int)(val < 0 ? -val : val);
        if (val < 0) buf[p++] = '-';
        while (u > 0) { tmp[tp++] = '0' + (u % 10); u /= 10; }
        while (tp > 0) buf[p++] = tmp[--tp];
    }
    return p;
}

/* Write a complete tagged line "marker node=<nid>\n" in a single write() */
static void emit_event(const char *marker, int node_id) {
    char buf[256];
    int p = 0;

    /* copy marker */
    while (*marker && p < 250) buf[p++] = *marker++;

    /* append " node=" */
    buf[p++] = ' '; buf[p++] = 'n'; buf[p++] = 'o'; buf[p++] = 'd';
    buf[p++] = 'e'; buf[p++] = '=';

    /* append node_id */
    p = fmt_int(buf, p, node_id);

    /* newline */
    buf[p++] = '\n';

    syscall3(SYS_WRITE, 1, (long)buf, (long)p);
}

/* Write a line with two integer fields: "marker node=<nid> mask=<mask>\n" */
static void emit_event_mask(const char *marker, int node_id, int mask) {
    char buf[256];
    int p = 0;

    while (*marker && p < 240) buf[p++] = *marker++;
    buf[p++] = ' '; buf[p++] = 'n'; buf[p++] = 'o'; buf[p++] = 'd';
    buf[p++] = 'e'; buf[p++] = '=';
    p = fmt_int(buf, p, node_id);
    buf[p++] = ' '; buf[p++] = 'm'; buf[p++] = 'a'; buf[p++] = 's';
    buf[p++] = 'k'; buf[p++] = '=';
    p = fmt_int(buf, p, mask);
    buf[p++] = '\n';

    syscall3(SYS_WRITE, 1, (long)buf, (long)p);
}

/* Write a plain string followed by newline in a single write() */
static void emit_str(const char *s) {
    int len = 0;
    while (s[len]) len++;
    syscall3(SYS_WRITE, 1, (long)s, (long)len);
    /* no newline added - caller should include \n if needed */
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

int main(int argc, char **argv) {
    int node_id = 0;
    int mask = 0b111;  /* all 3 nodes participate */

    if (argc >= 2) {
        node_id = parse_int(argv[1]);
    }

    emit_event("BEFORE_BARRIER", node_id);

    /* Call Sync_Wait(mask) */
    syscall1(SYS_SYNC_WAIT, mask);

    emit_event("AFTER_BARRIER", node_id);

    exit_program(0);
    return 0;
}
