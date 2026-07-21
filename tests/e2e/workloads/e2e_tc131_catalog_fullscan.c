/* TC131: replicated catalog under a real 57K-entry ResidentDir full scan.
 * node0 owns a 4K-line catalog, node1/node2 cache it, then node0 streams 64K
 * unrelated lines. Reuse exposes naive invalidation versus metadata spill. */
#include "dsm_access.h"
#include "e2e_common.h"
#define HOT 4096
#define PRESSURE 65536
#define CATALOG_BASE 0x000000u
#define SCAN_BASE 0x1000000u
#define VALUE 0x13100000u
static inline uint32_t hot_off(int i) { return CATALOG_BASE + (uint32_t)i * 64u; }
static inline uint32_t scan_off(int i) { return SCAN_BASE + (uint32_t)i * 64u; }
int main(int argc, char **argv) {
 int n = 0, c = 0;
 if (argc >= 2) n = parse_int(argv[1]);
 if (argc >= 3) c = parse_int(argv[2]);
 if (c % 4) { _exit_program(0); return 0; }
 emit_e2e_meta(n, "TC131");
 if (n == 0) {
  for (int i = 0; i < HOT; i++) dsm_store(0, hot_off(i), VALUE | (uint32_t)i);
  emit_phase_done(0, "catalog_seed");
 }
 sync_wait(7);
 if (n == 1 || n == 2) {
  for (int i = 0; i < HOT; i++) (void)dsm_load(0, hot_off(i));
  emit_phase_done(n, "catalog_share");
 }
 sync_wait(7);
 if (n == 0) {
  for (int i = 0; i < PRESSURE; i++) {
   dsm_store(0, scan_off(i), 0x13180000u | (uint32_t)i);
   if ((i + 1) % 256 == 0) emit_progress(0, "full_scan", i + 1);
  }
  emit_phase_done(0, "full_scan");
 }
 sync_wait(7);
 if (n == 1 || n == 2) {
  for (int pass = 0; pass < 2; pass++) for (int i = 0; i < HOT; i++) {
   uint32_t v = dsm_load(0, hot_off(i));
   if (pass == 0 && i % 512 == 0)
    emit_read_val(n, 0, VALUE | (uint32_t)i, v, v == (VALUE | (uint32_t)i));
  }
  emit_phase_done(n, "catalog_reuse");
 }
 sync_wait(7);
 _exit_program(0);
 return 0;
}
