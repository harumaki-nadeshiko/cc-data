/* TC46: multi-beat recall data integrity (64B, byte-level check). */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 0
#define LINE_OFF  0x4600u /* 64B aligned */

static inline uint8_t tc46_expected_byte(int idx)
{
    return (uint8_t)(idx & 0xFF);
}

static inline uint64_t tc46_build_word(int byte_base)
{
    uint64_t w = 0;
    for (int b = 0; b < 8; b++) {
        w |= ((uint64_t)tc46_expected_byte(byte_base + b)) << (8 * b);
    }
    return w;
}

static inline void emit_tc46_byte(int node_id, int idx, int exp, int act, int match)
{
    char buf[220]; int p = 0;
    char *s = (char *)"[TC46_BYTE] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" idx="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, idx);
    s = (char *)" exp="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, exp);
    s = (char *)" act="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, act);
    s = (char *)(match ? " MATCH" : " MISMATCH");
    while (*s) buf[p++] = *s++;
    buf[p++] = '\n';
    _raw_write(buf, p);
}

static inline void emit_tc46_summary(int node_id, int total, int mismatches)
{
    char buf[220]; int p = 0;
    char *s = (char *)"[TC46_SUMMARY] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" checked="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, total);
    s = (char *)" mismatches="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, mismatches);
    buf[p++] = '\n';
    _raw_write(buf, p);
}

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);

    emit_e2e_meta(node_id, "TC46");

    if (node_id == 0) {
        for (int w = 0; w < 8; w++) {
            dsm_store64(HOME_NODE, LINE_OFF + (uint32_t)(w * 8), tc46_build_word(w * 8));
        }
    }

    sync_wait(0b111);

    int mismatches = 0;
    if (node_id == 1) {
        int checked = 0;
        for (int w = 0; w < 8; w++) {
            uint64_t got = dsm_load64(HOME_NODE, LINE_OFF + (uint32_t)(w * 8));
            for (int b = 0; b < 8; b++) {
                int idx = w * 8 + b;
                int exp = (int)tc46_expected_byte(idx);
                int act = (int)((got >> (8 * b)) & 0xFFu);
                int ok = (exp == act);
                emit_tc46_byte(node_id, idx, exp, act, ok);
                if (!ok) mismatches++;
                checked++;
            }
        }
        emit_tc46_summary(node_id, checked, mismatches);
    }

    sync_wait(0b111);

    _exit_program((node_id == 1 && mismatches) ? 1 : 0);
    return 0;
}
