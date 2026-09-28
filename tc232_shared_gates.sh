#!/bin/bash
# Execute inside Docker --network none only. Fresh evidence on both profiles.
set -euo pipefail
export CHI_GATE_PREFIX=tc232-shared-v1 CHI_RUN_PREFIX=t232sv1
for arm in ubcc ha-vi; do
  for pressure in 100 0 50; do
    bash ha_invalidatefix_gate.sh 8n1s "$pressure" "$arm" 232
  done
  for topo in 2n1s 4n1s 8n2s 16n1s; do
    bash ha_invalidatefix_gate.sh "$topo" 100 "$arm" 232
  done
  bash ha_finalfix_gates.sh "$arm"
done
