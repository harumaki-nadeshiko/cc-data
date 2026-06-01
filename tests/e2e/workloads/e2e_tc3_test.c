#include "dsm_access.h"
#include "e2e_common.h"

int main(int argc, char **argv)
{
    int node_id = 0;
    int cpu_index = 0;
    if (argc >= 2) node_id = parse_int(argv[1]);
    if (argc >= 3) cpu_index = parse_int(argv[2]);
    int primary = (cpu_index % 4 == 0);
    
    // Quick test: dc civac on a local variable
    volatile uint32_t local_val = 0xDEAD;
    __asm__ volatile("dc civac, %0\n" "dsb osh\n" : : "r"(&local_val) : "memory");
    
    if (primary) {
        emit_e2e_meta(node_id, "TC3-TEST");
        emit_phase_done(node_id, "done");
    }
    _exit_program(0);
    return 0;
}
