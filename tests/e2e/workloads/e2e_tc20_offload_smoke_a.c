#include "dsm_access.h"
#include "e2e_common.h"

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (!primary) {
        _exit_program(0);
        return 0;
    }

    const uint32_t magic = 0x20202020;
    if (node_id == 0) {
        dsm_store(1, 0, magic);
    }
    sync_wait(0b111);
    if (node_id == 1 || node_id == 2) {
        uint32_t got = dsm_load(1, 0);
        emit_read_val(node_id, 1, magic, got, got == magic);
    }
    sync_wait(0b111);
    _exit_program(0);
    return 0;
}
