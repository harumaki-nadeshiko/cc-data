/* TC131: replicated catalog with >150% of the pure-ResidentDir capacity.
 * node0 owns a 4K-line catalog, node1/node2 cache it, then node0 streams 96K
 * unrelated lines.  The 102,656 distinct lines exceed the 65,536-entry pure
 * directory by 56.64%, so spill must retain at least 98,304 unique records. */
#include "dsm_access.h"
#include "e2e_common.h"
#define HOT 4096
#define PRESSURE 98304
#define CATALOG_BASE 0x000000u
#define SCAN_BASE 0x1000000u
#define UPGRADE_BASE 0x700000u
#define UPGRADE_SEM_BASE 0x710000u
#define UPGRADE_SAMPLES 256
#define VALUE 0x13100000u
static inline uint32_t hot_off(int i) { return CATALOG_BASE + (uint32_t)i * 64u; }
static inline uint32_t scan_off(int i) { return SCAN_BASE + (uint32_t)i * 64u; }
static inline uint32_t upgrade_off(int i) { return UPGRADE_BASE + (uint32_t)i * 64u; }
static inline uint32_t upgrade_sem_off(int i) { return UPGRADE_SEM_BASE + (uint32_t)i * 64u; }
static inline uint64_t read_cntvct(void) { uint64_t v; __asm__ volatile("mrs %0, cntvct_el0" : "=r"(v)); return v; }
static int fmt_u64_dec(char *buf, int p, uint64_t value) {
 if (!value) { buf[p++] = '0'; return p; }
 char digits[24]; int n = 0;
 while (value) { digits[n++] = (char)('0' + value % 10); value /= 10; }
 while (n) buf[p++] = digits[--n];
 return p;
}
static void emit_latency(int node, const char *phase, int sample, uint64_t cycles) {
 char buf[128]; int p = 0; char *s = (char *)"[LATENCY] node=";
 while (*s) buf[p++] = *s++; p = fmt_int(buf, p, node);
 s = (char *)" phase="; while (*s) buf[p++] = *s++;
 while (*phase) buf[p++] = *phase++;
 s = (char *)" iter="; while (*s) buf[p++] = *s++;
 p = fmt_int(buf, p, sample); s = (char *)" cycles="; while (*s) buf[p++] = *s++;
 p = fmt_u64_dec(buf, p, cycles); buf[p++] = '\n'; _raw_write(buf, p);
}
int main(int argc, char **argv) {
 int n = 0, c = 0;
 if (argc >= 2) n = parse_int(argv[1]);
 if (argc >= 3) c = parse_int(argv[2]);
 /* Only nodes 0, 1, and 2 participate in sync_wait(0x7). CPU2 on node1
  * is the second L2 cluster for the silent-upgrade samples. */
 if (n > 2 || (c % 4 && !(n == 1 && c % 4 == 2))) {
  _exit_program(0); return 0;
 }
 if (c % 4 == 0) emit_e2e_meta(n, "TC131");
 if (n == 0) {
  for (int i = 0; i < HOT; i++) dsm_store(0, hot_off(i), VALUE | (uint32_t)i);
  emit_phase_done(0, "catalog_seed");
 }
 if (c % 4 == 0) sync_wait(7);
 if ((n == 1 || n == 2) && c % 4 == 0) {
  for (int i = 0; i < HOT; i++) (void)dsm_load(0, hot_off(i));
  emit_phase_done(n, "catalog_share");
 }
 if (c % 4 == 0) sync_wait(7);
 if (n == 0) {
  for (int i = 0; i < PRESSURE; i++) {
   dsm_store(0, scan_off(i), 0x13180000u | (uint32_t)i);
   if ((i + 1) % 256 == 0) emit_progress(0, "full_scan", i + 1);
  }
  emit_phase_done(0, "full_scan");
 }
 if (c % 4 == 0) sync_wait(7);
 if ((n == 1 || n == 2) && c % 4 == 0) {
  for (int pass = 0; pass < 2; pass++) for (int i = 0; i < HOT; i++) {
   uint64_t t0 = 0;
   if (pass == 0 && i % 64 == 0) t0 = read_cntvct();
   uint32_t v = dsm_load(0, hot_off(i));
   if (t0) emit_latency(n, "catalog_reuse", i, read_cntvct() - t0);
   if (pass == 0 && i % 512 == 0)
    emit_read_val(n, 0, VALUE | (uint32_t)i, v, v == (VALUE | (uint32_t)i));
  }
  emit_phase_done(n, "catalog_reuse");
 }
 if (c % 4 == 0) sync_wait(7);
 /* CPU0 first obtains R_M. CPU2 is in a different L2 cluster, so its store
  * enters EP-RNF with the node-level R_M record.  Silent upgrade completes
  * locally; baseline issues OuterUpgradeReq. */
 if (n == 1) {
  if (c % 4 == 0) {
   for (int i = 0; i < UPGRADE_SAMPLES; i++) {
    local_dram_store(upgrade_sem_off(i), 0);
    dsm_store(0, upgrade_off(i), 0x131e0000u | (uint32_t)i);
    coherence_settle();
    local_dram_store(upgrade_sem_off(i), 1);
    while (local_dram_load(upgrade_sem_off(i)) != 2) { }
   }
   emit_phase_done(1, "exclusive_upgrade");
  } else if (c % 4 == 2) {
   for (int i = 0; i < UPGRADE_SAMPLES; i++) {
    while (local_dram_load(upgrade_sem_off(i)) != 1) { }
    uint64_t t0 = read_cntvct();
    dsm_store(0, upgrade_off(i), 0x131f0000u | (uint32_t)i);
    uint64_t t1 = read_cntvct();
    emit_latency(1, "exclusive_upgrade", i, t1 - t0);
    local_dram_store(upgrade_sem_off(i), 2);
   }
  }
  }
 if (c % 4 == 0) sync_wait(7);
 _exit_program(0);
 return 0;
}
