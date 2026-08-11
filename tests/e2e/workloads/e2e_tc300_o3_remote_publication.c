#include "dsm_access.h"
#include "e2e_common.h"

#define DATA_OFF 0u
#define FLAG_OFF 64u
#define VALUE 0x3000c0deu

static inline void store_release32(volatile uint32_t *addr, uint32_t value)
{
    __asm__ volatile("stlr %w1, [%0]" : : "r"(addr), "r"(value) : "memory");
}

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
    emit_e2e_meta(node, "TC300");

    if (node == 0) {
        dsm_store(2, DATA_OFF, VALUE);
        store_release32(dsm_addr(2, FLAG_OFF), 1);
    }
    arch_sync_wait(0b111);

    if (node == 1) {
        uint32_t flag = load_acquire32(dsm_addr(2, FLAG_OFF));
        uint32_t data = dsm_load(2, DATA_OFF);
        emit_read_val(node, 2, 1, flag, flag == 1);
        emit_read_val(node, 2, VALUE, data, data == VALUE);
    }
    arch_sync_wait(0b111);
    _exit_program(0);
    return 0;
}
