/* TC22: ResidentDir 容量压力（总触达 >60K lines）+ 抽检一致性。 */
#include "dsm_access.h"
#include "e2e_common.h"

#define TOTAL_LINES   3072
#define CHUNK_LINES   256
#define BASE_OFF      0x10000

static const int PROBE_HOME[3] = {1, 2, 0};
static const uint32_t PROBE_OFF[3] = {0x200, 0x240, 0x280};
static const uint32_t PROBE_VAL[3] = {0x22A0A0A1, 0x22B0B0B2, 0x22C0C0C3};

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);

    if (primary) emit_e2e_meta(node_id, "TC22");
    if (!primary) {
        _exit_program(0);
        return 0;
    }

    int fail = 0;

    for (int base = 0; base < TOTAL_LINES; base += CHUNK_LINES) {
        int phase = base / CHUNK_LINES;
        int writer = phase % 3;
        int home = (phase + 1) % 3; /* 避免全程 home-local */

        if (node_id == writer) {
            int end = base + CHUNK_LINES;
            if (end > TOTAL_LINES) end = TOTAL_LINES;
            for (int i = base; i < end; i++) {
                uint32_t line = (uint32_t)((i * 131) % TOTAL_LINES);
                uint32_t off = BASE_OFF + line * 64u;
                uint32_t got = dsm_load(home, off);
                (void)got;
            }
        }
        sync_wait(0b111);
    }

    /* 写入 3 个 probe line（后续跨节点抽检） */
    for (int p = 0; p < 3; p++) {
        if (node_id == p) {
            dsm_store(PROBE_HOME[p], PROBE_OFF[p], PROBE_VAL[p]);
        }
    }
    sync_wait(0b111);

    for (int p = 0; p < 3; p++) {
        uint32_t got = dsm_load(PROBE_HOME[p], PROBE_OFF[p]);
        emit_read_val(node_id, PROBE_HOME[p], PROBE_VAL[p], got, got == PROBE_VAL[p]);
        if (got != PROBE_VAL[p]) fail++;
    }
    sync_wait(0b111);

    _exit_program(fail ? 1 : 0);
    return 0;
}
