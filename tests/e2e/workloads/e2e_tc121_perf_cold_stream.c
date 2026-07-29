/* TC121 / P1: cold streaming overflow.
 * Gives naive eviction its best case: low reuse after directory pressure.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#ifndef TC121_LINES
#define TC121_LINES 64
#endif
#define BASE  0x12100000u
#define CONFLICT_STRIDE 0x10000u

static int fmt_u64_dec(char *buf, int p, uint64_t val)
{
    if (val == 0) { buf[p++] = '0'; return p; }
    char tmp[24]; int tp = 0;
    while (val) { tmp[tp++] = (char)('0' + (val % 10)); val /= 10; }
    while (tp) buf[p++] = tmp[--tp];
    return p;
}

static void emit_latency(int node_id, const char *phase_name, int iter,
                         uint64_t cycles)
{
    char buf[192]; int p = 0;
    char *s = (char *)"[LATENCY] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" phase="; while (*s) buf[p++] = *s++;
    while (*phase_name) buf[p++] = *phase_name++;
    s = (char *)" iter="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, iter);
    s = (char *)" cycles="; while (*s) buf[p++] = *s++;
    p = fmt_u64_dec(buf, p, cycles);
    buf[p++] = '\n';
    _raw_write(buf, p);
}

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    if ((cpu_index % 4) != 0) { _exit_program(0); return 0; }
    emit_e2e_meta(node_id, "TC121");

    if (node_id == 0) {
        for (int i = 0; i < TC121_LINES; i++) {
            emit_progress(0, "cold_stream_before", i);
            dsm_store(0, (uint32_t)i * CONFLICT_STRIDE, BASE | (uint32_t)i);
            emit_progress(0, "cold_stream_after", i);
        }
        emit_phase_done(0, "cold_stream_write");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        uint64_t t_phase = read_cntvct_el0();
        for (int i = 0; i < TC121_LINES; i += 4) {
            uint32_t exp = BASE | (uint32_t)i;
            uint64_t t0 = read_cntvct_el0();
            uint32_t got = dsm_load(0, (uint32_t)i * CONFLICT_STRIDE);
            uint64_t t1 = read_cntvct_el0();
            emit_latency(1, "cold_stream_sample", i, t1 - t0);
            emit_read_val(1, 0, exp, got, got == exp);
        }
        emit_guest_timer(1, "cold_stream_sample", TC121_LINES / 4,
                         read_cntvct_el0() - t_phase);
        emit_phase_done(1, "cold_stream_sample");
    }
    sync_wait(0b111);

    _exit_program(0);
    return 0;
}
