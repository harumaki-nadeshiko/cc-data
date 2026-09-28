#!/bin/bash
# Docker --network none ubcc-dev:ubuntu20.04 only; no reuse of old evidence.
set -euo pipefail
export CHI_GATE_PREFIX=${CHI_GATE_PREFIX:-chi-finalfix} CHI_RUN_PREFIX=${CHI_RUN_PREFIX:-cf}
arm=${1:?arm}
for pressure in 0 50 100; do
  bash ha_invalidatefix_gate.sh 3n1s "$pressure" "$arm" 230
done
bash ha_invalidatefix_gate.sh 3n2s 50 "$arm" 230
for tc in 229 228; do
  bash ha_invalidatefix_gate.sh 3n1s 0 "$arm" "$tc"
  bash ha_invalidatefix_gate.sh 3n2s 50 "$arm" "$tc"
done
for pressure in 0 100; do
  bash ha_invalidatefix_gate.sh 2n1s "$pressure" "$arm" 228
done
bash ha_invalidatefix_gate.sh 8n1s 0 "$arm" 229
