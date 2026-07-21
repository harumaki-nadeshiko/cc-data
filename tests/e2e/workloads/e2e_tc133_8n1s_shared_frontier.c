/* TC133: 8-node graph-frontier sharing; 7 readers retain a 4K-line frontier
 * while Home streams 64K new adjacency blocks through its real directory. */
#include "dsm_access.h"
#include "e2e_common.h"
#define NODES 8
#define HOT 4096
#define PRESSURE 65536
#define FRONTIER_BASE 0x000000u
#define STREAM_BASE 0x1000000u
#define VALUE 0x13300000u
static inline uint32_t hot_off(int i) { return FRONTIER_BASE + (uint32_t)i * 64u; }
static inline uint32_t stream_off(int i) { return STREAM_BASE + (uint32_t)i * 64u; }
int main(int argc,char **argv) {
 int n=0,c=0;if(argc>=2)n=parse_int(argv[1]);if(argc>=3)c=parse_int(argv[2]);if(c%4){_exit_program(0);return 0;}emit_e2e_meta(n,"TC133");
 if(n==0){for(int i=0;i<HOT;i++)dsm_store(0,hot_off(i),VALUE|(uint32_t)i);emit_phase_done(0,"frontier_seed");} sync_wait(0xFF);
 if(n){for(int i=0;i<HOT;i++)(void)dsm_load(0,hot_off(i));emit_phase_done(n,"frontier_share");} sync_wait(0xFF);
 if(n==0){for(int i=0;i<PRESSURE;i++)dsm_store(0,stream_off(i),0x13380000u|(uint32_t)i);emit_phase_done(0,"frontier_pressure");} sync_wait(0xFF);
 if(n){for(int i=0;i<HOT;i++){uint32_t v=dsm_load(0,hot_off(i));if(i==n*512)emit_read_val(n,0,VALUE|(uint32_t)i,v,v==(VALUE|(uint32_t)i));}emit_phase_done(n,"frontier_reuse");} sync_wait(0xFF);_exit_program(0);return 0;
}
