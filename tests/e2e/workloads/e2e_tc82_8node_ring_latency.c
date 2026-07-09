/* TC82: 8-node ring read — node i reads node (i+1)%8's DSM.
 * Each node writes to its own DSM, barrier, then reads neighbor's value.
 */
#include "e2e_common.h"
#define NUM_NODES 8
#define SEG_SIZE 0x8000000ULL
#define DSM_VA_BASE 0xFFFFB8000000ULL
static inline volatile uint32_t *dsm_addr(int h,uint32_t o){
    return (volatile uint32_t*)(DSM_VA_BASE+(uint64_t)h*SEG_SIZE+o);
}
int main(int argc,char**argv){
    int nid=0; if(argc>=2)nid=parse_int(argv[1]);
    int dst=(nid+1)%NUM_NODES;
    uint32_t val=0x82000000u|((uint32_t)nid<<8);
    __asm__("str %w0,[%1]"::"r"(val),"r"(dsm_addr(nid,0x6200)));
    sync_wait(0xFF);
    uint32_t got;
    __asm__("ldr %w0,[%1]":"=r"(got):"r"(dsm_addr(dst,0x6200)));
    uint32_t exp=0x82000000u|((uint32_t)dst<<8);
    emit_read_val(nid,dst,exp,got,got==exp);
    sync_wait(0xFF);_exit_program(0);return 0;
}
