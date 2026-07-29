#include "e2e_common.h"
#define NUM_NODES 3
#define SEG_SIZE 0x8000000ULL
#define DSM_VA_BASE 0xFFFFE0000000ULL
#define N 50
static inline volatile uint32_t *dsm_addr(int h,uint32_t o){
    return (volatile uint32_t*)(DSM_VA_BASE+(uint64_t)h*SEG_SIZE+o);
}
int main(int argc,char**argv){
    int nid=0, cpu_index=0;
    if(argc>=2)nid=parse_int(argv[1]);
    if(argc>=3)cpu_index=parse_int(argv[2]);
    /* Single-arg sync_wait(mask): only the primary CPU per node participates
     * (TC90/TC94 pattern). Without this, all 4 CPUs enter the cross-node
     * barrier and desynchronize the per-node barrier generation, hanging the
     * home node (node0) at the barrier. This — not PDES latency — was the real
     * cause of the TC84/TC85 timeouts. */
    if((cpu_index%4)!=0){_exit_program(0);return 0;}
    if(nid!=0){sync_wait(0b111);_exit_program(0);return 0;}
    int ok=0;
    uint64_t t0 = read_cntvct_el0();
    for(int i=0;i<N;i++){uint32_t o=0x1000+(uint32_t)i*64u,v=0x84000000u^(uint32_t)i,g;__asm__("str %w0,[%1]"::"r"(v),"r"(dsm_addr(0,o)));__asm__("dmb ish":::"memory");__asm__("ldr %w0,[%1]":"=r"(g):"r"(dsm_addr(0,o)));if(g==v)ok++;}
    emit_guest_timer(nid, "cacheline_capacity", N,
                     read_cntvct_el0() - t0);
    char b[64];int p=0;char*s="[CAPACITY] ok=";while(*s)b[p++]=*s++;
    p=fmt_int(b,p,ok);s=" total=";while(*s)b[p++]=*s++;p=fmt_int(b,p,N);b[p++]='\n';_raw_write(b,p);
    emit_read_val(nid,0,0x84000000u,(uint32_t)ok,ok>=N/4);
    sync_wait(0b111);_exit_program(0);return 0;
}
