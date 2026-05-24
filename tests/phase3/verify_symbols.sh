#!/bin/bash
# Verify EP controller symbols and message paths
set -euo pipefail

GEM5="/workspace/gem5/build/ARM/gem5.opt"
echo "=== Symbol Verification for EP Controllers ==="

tests=0; passed=0

check_sym() {
    local name="$1"; local sym="$2"
    tests=$((tests + 1))
    if nm "$GEM5" 2>/dev/null | grep -q "$sym"; then
        echo "  $name: PASS"
        passed=$((passed + 1))
    else
        echo "  $name: FAIL"
    fi
}

check_str() {
    local name="$1"; local str="$2"
    tests=$((tests + 1))
    if strings "$GEM5" 2>/dev/null | grep -q "$str"; then
        echo "  $name: PASS"
        passed=$((passed + 1))
    else
        echo "  $name: FAIL"
    fi
}

echo "TC-EP-3: EPRNF snoop path symbols"
check_sym "recvSnoopMsg" "recvSnoopMsg"
check_sym "SnpResp_I response" "SnpResp_I"
check_sym "selfTest in EPRNF" "EPRNFController.*selfTest"
check_sym "checkAddr called from recvSnoop" "checkAddr"

echo "TC-EP-4: EPSNF ReadNoSnp path symbols"
check_sym "recvRequestMsg" "recvRequestMsg"
check_sym "RespSepData response" "CHIResponseType_RespSepData"
check_sym "CompData_I data" "CHIDataType_CompData_I"

echo "TC-ISO-4: cross-node checker"
check_str "checkAddr fatal node_id" "forbidden non-DSM access"
check_str "checkAddr cross-node fatal" "cross-node DSM access"

echo "TC-EP-5: unwired endpoint check"
check_str "init fatal no backend" "no backend attached"

echo "TC-G-4: trace with node_id"
check_str "EP_RNF node_id in trace" "EP_RNF node_id="
check_str "EP_SNF node_id in trace" "EP_SNF node_id="
check_str "EPBackend node_id in fatal" "EPBackend node_id="

echo "TC-EP-1: EPBackend + UBCC symbols"
check_sym "UBCCController" "UBCCController"
check_sym "EPBackend" "EPBackend"
check_sym "NodeAddressMap" "NodeAddressMap"

echo ""
echo "=== TOTAL: $passed/$tests tests passed ==="
[ "$passed" -eq "$tests" ] && exit 0 || exit 1
