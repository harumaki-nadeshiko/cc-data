/* TC80: Cross-node read latency — single PA, 8 repeated reads.
 * Node 1 writes a sentinel on node 0, then reads it 8 times with cntvct_el0 timing.
 */
#include "e2e_common.h"
#define NUM_NODES 3
#define SEG_SIZE 0x8000000ULL
#define DSM_VA_BASE 0xffff38000000ULL
static inline volatile uint32_t *dsm_addr(int h, uint32_t o) {
    return (volatile uint32_t *)(DSM_VA_BASE + (uint64_t)h * SEG_SIZE + o);
}
int main(int argc, char **argv) {
    int nid = 0;
    if (argc >= 2) nid = parse_int(argv[1]);
    uint32_t val = 0x800000AAu;
    if (nid == 0) __asm__ volatile("str %w0,[%1]"::"r"(val),"r"(dsm_addr(0,0x6000)));
    sync_wait(0b111);
    if (nid == 1) {
        uint32_t got; char b[64]; int p; char *s;
        for (int i = 0; i < 8; i++) {
            uint64_t t0, t1;
            __asm__ volatile("mrs %0,cntvct_el0":"=r"(t0));
            __asm__ volatile("ldr %w0,[%1]":"=r"(got):"r"(dsm_addr(0,0x6000)));
            __asm__ volatile("mrs %0,cntvct_el0":"=r"(t1));
            if(got!=val){p=0;s="[LATENCY] FAIL";while(*s)b[p++]=*s++;b[p++]='\n';_raw_write(b,p);}
        }
        p=0;s="[LATENCY] node=1 count=8 done";while(*s)b[p++]=*s++;b[p++]='\n';_raw_write(b,p);
        emit_read_val(nid,0,val,got,got==val);
    }
    sync_wait(0b111);
    _exit_program(0); return 0;
}
