/* TC31: 4 CPUs/node concurrent accesses on distinct DSM lines. */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE   0
#define BASE_OFF    0x5000
#define LINES       12

static inline uint32_t line_off(int node_id, int lane)
{
    return BASE_OFF + (uint32_t)(node_id * 4 + lane) * 64u;
}

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);

    int lane = cpu_index & 3;
    uint32_t off = line_off(node_id, lane);
    uint32_t val = 0x31000000u | ((uint32_t)node_id << 8) | (uint32_t)lane;

    for (int i = 0; i < 32; i++) {
        dsm_store(HOME_NODE, off, val);
        if ((i & 3) == 0) (void)dsm_load(HOME_NODE, off);
    }
    __asm__ volatile("dmb sy" ::: "memory");

    int primary = (cpu_index % 4 == 0);
    if (!primary) _exit_program(0);

    emit_e2e_meta(node_id, "TC31");
    int fail = 0;
    sync_wait(0b111);

    if (node_id == 0) {
        for (int idx = 0; idx < LINES; idx++) {
            int n = idx / 4;
            int l = idx % 4;
            uint32_t exp = 0x31000000u | ((uint32_t)n << 8) | (uint32_t)l;
            uint32_t got = dsm_load(HOME_NODE, BASE_OFF + (uint32_t)idx * 64u);
            emit_read_val(node_id, HOME_NODE, exp, got, got == exp);
            if (got != exp) fail++;
        }
    }
    sync_wait(0b111);

    _exit_program(fail ? 1 : 0);
    return 0;
}
