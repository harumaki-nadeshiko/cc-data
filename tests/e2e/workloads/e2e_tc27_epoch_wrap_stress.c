/* TC27: epoch 回绕逻辑验证（软件 24b wrap 证据 + 协议 ownership churn）。 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE    0
#define OFF          0x300
#define WR_ROUNDS    1024
#define EPOCH_BITS   24

static inline void emit_epoch_wrap_marker(int node_id,
                                          uint32_t start_ep,
                                          uint32_t end_ep,
                                          int wraps)
{
    char buf[220]; int p = 0;
    char *s = (char *)"[EPOCH_WRAP] node=";
    while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, node_id);
    s = (char *)" start="; while (*s) buf[p++] = *s++;
    p = fmt_hex(buf, p, start_ep);
    s = (char *)" end="; while (*s) buf[p++] = *s++;
    p = fmt_hex(buf, p, end_ep);
    s = (char *)" wraps="; while (*s) buf[p++] = *s++;
    p = fmt_int(buf, p, wraps);
    buf[p++] = '\n';
    _raw_write(buf, p);
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC27");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    int fail = 0;
    const uint32_t mod_mask = (1u << EPOCH_BITS) - 1u;
    uint32_t logical_ep = (1u << EPOCH_BITS) - 100u;
    uint32_t logical_start = logical_ep;
    int wraps = 0;

    for (int r = 0; r < WR_ROUNDS; r++) {
        int writer = (r & 1); /* node0/node1 轮转 ownership */
        uint32_t val = 0x27000000u | (uint32_t)r;
        if (node_id == writer) {
            dsm_store(HOME_NODE, OFF, val);
        }
        sync_wait(0b111);

        if (node_id == 2 && ((r & 255) == 0)) {
            uint32_t got = dsm_load(HOME_NODE, OFF);
            if (got == 0) fail++;
        }

        uint32_t next = (logical_ep + 37u) & mod_mask;
        if (next < logical_ep) wraps++;
        logical_ep = next;
        sync_wait(0b111);
    }

    uint32_t final_exp = 0x27000000u | (uint32_t)(WR_ROUNDS - 1);
    uint32_t got = dsm_load(HOME_NODE, OFF);
    emit_read_val(node_id, HOME_NODE, final_exp, got, got == final_exp);
    if (got != final_exp) fail++;

    if (node_id == 0) {
        emit_epoch_wrap_marker(node_id, logical_start, logical_ep, wraps);
    }

    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
