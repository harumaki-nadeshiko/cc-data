/* TC153-TC155: RecallResp duplicate/delay/reorder qualification. */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOME_NODE 1
#define LINE_COUNT 16
#define BASE_OFF 0x15300
#define VALUE_BASE 0x15300000u

static inline uint64_t line_off(int i) { return BASE_OFF + (uint64_t)i * 64; }

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);
    if (primary) emit_e2e_meta(node_id, "TC153-155");
    if (!primary) { _exit_program(0); return 0; }

    if (node_id == 0) {
        for (int i = 0; i < LINE_COUNT; ++i)
            dsm_store(HOME_NODE, line_off(i), VALUE_BASE | (uint32_t)i);
    }
    sync_wait(0b111);

    int fail = 0;
    if (node_id == 2) {
        for (int i = 0; i < LINE_COUNT; ++i) {
            uint32_t expected = VALUE_BASE | (uint32_t)i;
            uint32_t actual = dsm_load(HOME_NODE, line_off(i));
            emit_read_val(node_id, HOME_NODE, expected, actual, actual == expected);
            if (actual != expected) fail++;
        }
    }
    sync_wait(0b111);
    _exit_program(fail ? 1 : 0);
    return 0;
}
