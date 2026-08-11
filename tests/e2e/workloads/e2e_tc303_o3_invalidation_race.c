#include "dsm_access.h"
#include "e2e_common.h"

#define ROUNDS 16
#define BASE 0x30300000u

static inline uint32_t load_acquire32(volatile uint32_t *addr)
{
    uint32_t value;
    __asm__ volatile("ldar %w0, [%1]" : "=r"(value) : "r"(addr) : "memory");
    return value;
}

int main(int argc, char **argv)
{
    int node = argc >= 2 ? parse_int(argv[1]) : 0;
    int cpu = argc >= 3 ? parse_int(argv[2]) : 0;
    if ((cpu % 4) != 0) { _exit_program(0); return 0; }
    emit_e2e_meta(node, "TC303");

    if (node == 0) {
        dsm_store(2, 0, BASE);
        __asm__ volatile("dsb sy" ::: "memory");
    }
    arch_sync_wait(0b111);

    for (int round = 1; round <= ROUNDS; ++round) {
        if (node == 1) {
            volatile uint32_t old = dsm_load(2, 0);
            (void)old;
        }
        arch_sync_wait(0b111);

        if (node == 0) {
            dsm_store(2, 0, BASE | (uint32_t)round);
            __asm__ volatile("dsb sy" ::: "memory");
        } else if (node == 1) {
            volatile uint32_t sink = 0;
            for (int i = 0; i < 128; ++i)
                sink ^= dsm_load(2, 0);
            (void)sink;
        }
        arch_sync_wait(0b111);

        if (node == 1) {
            uint32_t expected = BASE | (uint32_t)round;
            uint32_t got = load_acquire32(dsm_addr(2, 0));
            emit_read_val(node, 2, expected, got, got == expected);
        }
        arch_sync_wait(0b111);
    }

    _exit_program(0);
    return 0;
}
