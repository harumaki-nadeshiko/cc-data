/* TC18: directory fill/replay smoke workload. */
#include "dsm_access.h"
#include "e2e_common.h"

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC18");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    const uint32_t magic = 0x18181818;
    const uint32_t x_off = 0x0;

    if (node_id == 1) {
        dsm_store(0, x_off, magic);
        for (int i = 1; i <= 32; ++i) {
            dsm_store(0, i * 64, (uint32_t)(0x18000000u + i));
        }
    }
    sync_wait(0b111);

    int fail = 0;
    if (node_id == 1 || node_id == 2) {
        uint32_t got = dsm_load(0, x_off);
        emit_read_val(node_id, 0, magic, got, got == magic);
        if (got != magic) fail++;
    }
    sync_wait(0b111);

    _exit_program(fail ? 1 : 0);
    return 0;
}
