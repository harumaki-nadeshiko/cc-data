/* TC123 / P3: shared hotset with periodic upgrades.
 * Exercises shared-state preservation, silent upgrade opportunities, and batch
 * RS behavior while directory pressure occurs between read-mostly phases.
 */
#include "dsm_access.h"
#include "e2e_common.h"

#define HOT_LINES  16
#define COLD_LINES 96
#define COLD_BASE  0x40000u
#define BASE       0x12300000u
#define NEWBASE    0x12310000u

int main(int argc, char **argv)
{
    int node_id = 0, cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    if ((cpu_index % 4) != 0) { _exit_program(0); return 0; }
    emit_e2e_meta(node_id, "TC123");

    if (node_id == 0) {
        for (int i = 0; i < HOT_LINES; i++)
            dsm_store(0, (uint32_t)i * 64u, BASE | (uint32_t)i);
        emit_phase_done(0, "init_hot");
    }
    sync_wait(0b111);

    if (node_id == 1 || node_id == 2) {
        uint64_t t0 = read_cntvct_el0();
        for (int i = 0; i < HOT_LINES; i++) {
            uint32_t exp = BASE | (uint32_t)i;
            uint32_t got = dsm_load(0, (uint32_t)i * 64u);
            if ((i % 8) == 0) emit_read_val(node_id, 0, exp, got, got == exp);
        }
        emit_guest_timer(node_id, "shared_read", HOT_LINES,
                         read_cntvct_el0() - t0);
        emit_phase_done(node_id, "shared_read");
    }
    sync_wait(0b111);

    if (node_id == 0) {
        for (int i = 0; i < COLD_LINES; i++)
            dsm_store(0, COLD_BASE + (uint32_t)i * 64u,
                      BASE | 0x8000u | (uint32_t)i);
        emit_phase_done(0, "dir_pressure");
    }
    sync_wait(0b111);

    if (node_id == 1) {
        for (int i = 0; i < HOT_LINES; i += 4)
            dsm_store(0, (uint32_t)i * 64u, NEWBASE | (uint32_t)i);
        emit_phase_done(1, "periodic_upgrade");
    }
    sync_wait(0b111);

    if (node_id == 2) {
        for (int i = 0; i < HOT_LINES; i += 4) {
            uint32_t exp = NEWBASE | (uint32_t)i;
            uint32_t got = dsm_load(0, (uint32_t)i * 64u);
            emit_read_val(2, 0, exp, got, got == exp);
        }
        emit_phase_done(2, "verify_upgrade");
    }
    sync_wait(0b111);

    _exit_program(0);
    return 0;
}
