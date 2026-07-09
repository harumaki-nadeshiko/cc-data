#include "e2e_common.h"
#define NUM_NODES 3
#define SEG_SIZE 0x8000000ULL
#define DSM_VA_BASE 0xffff00000000ULL
static inline volatile uint32_t *dsm2(int h,int s,uint32_t o){
    return (volatile uint32_t*)(DSM_VA_BASE+((uint64_t)h*2+s)*SEG_SIZE+o);
}
int main(int argc,char**argv){
    int nid=0; if(argc>=2)nid=parse_int(argv[1]);
    if(nid==0){__asm__("str %w0,[%1]"::"r"(0x810000AAu),"r"(dsm2(0,0,0x6100)));__asm__("str %w0,[%1]"::"r"(0x810000BBu),"r"(dsm2(0,1,0x6100)));}
    sync_wait(0b111);
    uint32_t g0,g1;
    if(nid==0){__asm__("ldr %w0,[%1]":"=r"(g0):"r"(dsm2(0,0,0x6100)));__asm__("ldr %w0,[%1]":"=r"(g1):"r"(dsm2(0,1,0x6100)));emit_read_val(nid,0,0x810000AAu,g0,g0==0x810000AAu&&g1==0x810000BBu);}
    sync_wait(0b111);_exit_program(0);return 0;
}
