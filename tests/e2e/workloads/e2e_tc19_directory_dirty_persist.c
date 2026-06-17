/* TC19: directory dirty persist smoke workload. */
#include "dsm_access.h"
#include "e2e_common.h"

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC19");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    const uint32_t magic = 0xABCD1234;
    const uint32_t y_off = 0x1000;

    if (node_id == 1) {
        dsm_store(0, y_off, magic);
        for (int i = 1; i <= 40; ++i) {
            dsm_load(0, 0x2000 + i * 64);
        }
    }
    sync_wait(0b111);

    int fail = 0;
    if (node_id == 2) {
        uint32_t got = dsm_load(0, y_off);
        emit_read_val(node_id, 0, magic, got, got == magic);
        if (got != magic) fail++;
    }
    sync_wait(0b111);

    _exit_program(fail ? 1 : 0);
    return 0;
}
