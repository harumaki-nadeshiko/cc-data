/* Minimal ARM test binary for gem5 SE mode self-tests.
 * Uses Sync_Wait syscall 436 for multi-node synchronization.
 * Compile with: aarch64-linux-gnu-gcc -static -o arm_sync_test.elf arm_sync_test.c
 */

#include <stdint.h>

static inline void sync_wait(unsigned int node_mask)
{
    register long x0 asm("x0") = (long)node_mask;
    register long x8 asm("x8") = 436;
    asm volatile("svc #0"
                 : "+r"(x0)
                 : "r"(x8)
                 : "memory");
}

int main(void)
{
    /* Simple: sync with all nodes, then exit */
    sync_wait(0b111);
    return 0;
}
