#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

#define DSM_BASE_ADDR (volatile uint32_t *)0x0000007f80000000UL

int main(int argc, char **argv) {
    printf("Running memory test\n");

    volatile uint32_t *dsm_addr = DSM_BASE_ADDR;
    printf("DSM_BASE_VA: %p\n", (void*)dsm_addr);

    uint32_t val = *dsm_addr;
    dsm_addr[0] = 0xdeadbeef;
    val = dsm_addr[0];
    printf("DSM readback: 0x%x\n", val);

    void *heap_ptr = malloc(4096);
    printf("heap alloc: %p\n", heap_ptr);
    printf("heap addr: 0x%lx\n", (unsigned long)heap_ptr);

    free(heap_ptr);

    printf("Test done\n");
    return 0;
}
