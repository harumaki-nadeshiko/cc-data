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

    const uint32_t magic = 0x21212121;
    if (node_id == 2) {
        dsm_store(0, 0x80, magic);
    }
    sync_wait(0b111);
    if (node_id == 0 || node_id == 1) {
        uint32_t got = dsm_load(0, 0x80);
        emit_read_val(node_id, 0, magic, got, got == magic);
    }
    sync_wait(0b111);
    _exit_program(0);
    return 0;
}
