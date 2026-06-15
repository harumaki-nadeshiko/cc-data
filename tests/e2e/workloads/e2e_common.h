/* e2e_common.h — Common helpers for E2E ARM workloads.
 *
 * Provides:
 *   - sync_wait(mask)        Barrier synchronisation (syscall 436)
 *   - emit_*() helpers       Formatted marker output for Python harness
 *   - SYS_* constants        ARM64 syscall numbers
 *   - fmt_int() / parse_int()  Integer formatting helpers
 */
#ifndef E2E_COMMON_H
#define E2E_COMMON_H

#include <stdint.h>

/* ── ARM64 syscall numbers ─────────────────────────────────────────── */
#define SYS_WRITE      64
#define SYS_SYNC_WAIT  436
#define SYS_EXIT       93

/* ── Syscall wrappers ──────────────────────────────────────────────── */

static inline long _syscall3(long num, long a0, long a1, long a2)
{
    register long x8 __asm__("x8") = num;
    register long x0 __asm__("x0") = a0;
    register long x1 __asm__("x1") = a1;
    register long x2 __asm__("x2") = a2;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}

static inline long _syscall1(long num, long a0)
{
    register long x8 __asm__("x8") = num;
    register long x0 __asm__("x0") = a0;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8) : "memory");
    return x0;
}

/* ── sync_wait barrier ─────────────────────────────────────────────── *
 * Gem5 syscall-based cross-node barrier (syscall 436).
 *
 * The syscall handler in gem5 (SyncWaitManager) suspends the calling
 * thread until popcount(node_mask) threads have called the barrier.
 * When all expected threads arrive, all are activated simultaneously.
 *
 * No shared memory required — the barrier state lives inside gem5.
 **********************************************************************/

static inline void sync_wait(unsigned int node_mask)
{
    _syscall1(SYS_SYNC_WAIT, (long)node_mask);
}

/* ── Integer formatting (no libc dependency) ───────────────────────── */

static inline int fmt_int(char *buf, int p, int val)
{
    if (val == 0) { buf[p++] = '0'; return p; }
    char tmp[16]; int tp = 0;
    unsigned int u = (unsigned int)(val < 0 ? -val : val);
    if (val < 0) buf[p++] = '-';
    while (u) { tmp[tp++] = '0' + (u % 10); u /= 10; }
    while (tp) buf[p++] = tmp[--tp];
    return p;
}

static inline int fmt_hex(char *buf, int p, unsigned int val)
{
    if (val == 0) { buf[p++] = '0'; return p; }
    int started = 0;
    for (int shift = 28; shift >= 0; shift -= 4) {
        int nib = (val >> shift) & 0xF;
        if (nib || started || shift == 0) {
            buf[p++] = nib < 10 ? '0' + nib : 'a' + nib - 10;
            started = 1;
        }
    }
    return p;
}

static inline int parse_int(const char *s)
{
    int v = 0;
    while (*s >= '0' && *s <= '9') { v = v * 10 + (*s - '0'); s++; }
    return v;
}

/* ── Single atomic write (no stdio buffering) ──────────────────────── */

static inline void _raw_write(const char *buf, int len)
{
    _syscall3(SYS_WRITE, 1, (long)buf, (long)len);
}

/* ── Marker emitters ───────────────────────────────────────────────── */

static inline void emit_before_wr(int node_id, int home, uint32_t val)
{
    char buf[200]; int p = 0;
    char *s = (char *)"[BEFORE_WR]  node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" home="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, home);
    s = (char *)" offset=0 val="; while (*s) buf[p++] = *s++;
    p = fmt_hex(buf, p, val);
    buf[p++] = '\n';
    _raw_write(buf, p);
}

static inline void emit_after_wr(int node_id, int home, uint32_t val)
{
    char buf[200]; int p = 0;
    char *s = (char *)"[AFTER_WR]   node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" home="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, home);
    s = (char *)" offset=0 val="; while (*s) buf[p++] = *s++;
    p = fmt_hex(buf, p, val);
    buf[p++] = '\n';
    _raw_write(buf, p);
}

static inline void emit_before_rd(int node_id, int home)
{
    char buf[128]; int p = 0;
    char *s = (char *)"[BEFORE_RD]  node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" home="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, home);
    s = (char *)" offset=0\n"; while (*s) buf[p++] = *s++;
    _raw_write(buf, p);
}

static inline void emit_read_val(int node_id, int home,
                                 uint32_t expected, uint32_t actual,
                                 int match)
{
    char buf[256]; int p = 0;
    char *s = (char *)"[READ_VAL]   node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" home="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, home);
    s = (char *)" offset=0 expected="; while (*s) buf[p++] = *s++;
    p = fmt_hex(buf, p, expected);
    s = (char *)" actual="; while (*s) buf[p++] = *s++;
    p = fmt_hex(buf, p, actual);
    s = (char *)" "; while (*s) buf[p++] = *s++;
    s = (char *)(match ? "MATCH" : "MISMATCH");
    while (*s) buf[p++] = *s++;
    buf[p++] = '\n';
    _raw_write(buf, p);
}

static inline void emit_phase_done(int node_id, const char *phase_name)
{
    char buf[200]; int p = 0;
    char *s = (char *)"[PHASE]      node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" phase="; while (*s) buf[p++] = *s++;
    while (*phase_name) buf[p++] = *phase_name++;
    s = (char *)" status=done\n"; while (*s) buf[p++] = *s++;
    _raw_write(buf, p);
}

static inline void emit_sync_marker(int node_id, int iter, int seg,
                                    uint32_t val)
{
    char buf[200]; int p = 0;
    char *s = (char *)"[SYNC]       node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" iter="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, iter);
    s = (char *)" seg="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, seg);
    s = (char *)" val="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, (int)val);
    buf[p++] = '\n';
    _raw_write(buf, p);
}

static inline void emit_e2e_meta(int node_id, const char *test_name)
{
    char buf[200]; int p = 0;
    char *s = (char *)"[E2E_META]   node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" test="; while (*s) buf[p++] = *s++;
    while (*test_name) buf[p++] = *test_name++;
    buf[p++] = '\n';
    _raw_write(buf, p);
}

static inline void _exit_program(int code)
{
    _syscall1(SYS_EXIT, (long)code);
    /* not reached */
    while (1) {}
}

#endif /* E2E_COMMON_H */
