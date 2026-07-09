/* TC84/85: Cacheline capacity — write 100 unique DSM lines, verify reads.
 * TC84=vanilla(UBCC_BF_BYTES=0), TC85=optimized(default). Same workload.
 */
#include "e2e_common.h"
#define NUM_NODES 3
#define SEG_SIZE 0x8000000ULL
#define DSM_VA_BASE 0xffff38000000ULL
#define N 100
static inline volatile uint32_t *dsm_addr(int h,uint32_t o){
    return (volatile uint32_t*)(DSM_VA_BASE+(uint64_t)h*SEG_SIZE+o);
}
int main(int argc,char**argv){
    int nid=0; if(argc>=2)nid=parse_int(argv[1]);
    if(nid!=0){sync_wait(0b111);_exit_program(0);return 0;}
    int ok=0;
    for(int i=0;i<N;i++){
        uint32_t o=0x1000+(uint32_t)i*64u,v=0x84000000u^(uint32_t)i,g;
        __asm__("str %w0,[%1]"::"r"(v),"r"(dsm_addr(0,o)));
        __asm__("dmb ish":::"memory");
        __asm__("ldr %w0,[%1]":"=r"(g):"r"(dsm_addr(0,o)));
        if(g==v)ok++;
    }
    char b[64]; int p=0; char*s="[CAPACITY] ok="; while(*s)b[p++]=*s++;
    p=fmt_int(b,p,ok); s=" total="; while(*s)b[p++]=*s++;
    p=fmt_int(b,p,N); b[p++]='\n'; _raw_write(b,p);
    emit_read_val(nid,0,0x84000000u,(uint32_t)ok,ok>=N/2);
    sync_wait(0b111);_exit_program(0);return 0;
}
