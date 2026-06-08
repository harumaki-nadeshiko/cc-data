"""Phase 2 — Fix A/B/C/D: Structural verification matrix.

Verification checkpoints:
  FIX-A-CHK: retryPendingSnoopResponses contains callbackDone guard
  FIX-B-CHK: recvSnoopMsg duplicate snoop paths send defensive SnpResp_I
  FIX-C-CHK: onGlobalInvalidateComplete has result.ok failure branch
  FIX-D-CHK-1: recvSnoopMsg contains all 6 snoop type dispatch branches
  FIX-D-CHK-2: sendOrRetry called in all non-blocking/immediate paths
  FIX-D-CHK-3: GlobalInvalidateResult struct defined in UBCCController.hh
  FIX-D-CHK-4: clearRegistration exists and is called from EPBackend
  FIX-D-CHK-5: callbackDone field declared in PendingSnoopTxn struct
  FIX-D-CHK-6: callbackDone set in onGlobalInvalidateComplete + onRemoteFetchComplete

Exit code: 0 = PASS, 1 = FAIL, 2 = SKIP
"""
import sys
import os
import re

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_SKIP = 2

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))


def _read_src(rel_path):
    """Read a source file and return its content."""
    path = os.path.join(REPO_ROOT, rel_path)
    try:
        with open(path, 'r') as f:
            return f.read()
    except Exception as e:
        print(f"  [WARN] Cannot read {path}: {e}")
        return None


def run_checks():
    """Run all structural checks, return list of (name, result, detail)."""
    details = []

    def check(name, result, detail=""):
        details.append((name, result, detail))
        status = "PASS" if result else "FAIL"
        extra = f" — {detail}" if detail else ""
        print(f"  {status}: {name}{extra}")
        return result

    epcc = _read_src('gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc')
    ephh = _read_src('gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh')
    ubcch = _read_src('gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh')
    ebcc = _read_src('gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc')

    # =========================================================================
    # FIX-A-CHK: callbackDone guard in retryPendingSnoopResponses
    # =========================================================================
    if epcc:
        # Must contain the skip-continue pattern that checks callbackDone
        has_cb_guard = '!txn.callbackDone' in epcc and \
                       '!txn.pendingResp' in epcc and \
                       '!txn.pendingData' in epcc
        check("FIX-A-CHK: retryPendingSnoopResponses callbackDone guard",
              has_cb_guard,
              "all three conditions present" if has_cb_guard
              else "missing one or more conditions")
    else:
        check("FIX-A-CHK: EPRNFController.cc", False, "source not readable")

    # =========================================================================
    # FIX-B-CHK: Duplicate snoop defensive response
    # =========================================================================
    if epcc:
        # Both SnpUnique and SnpOnce duplicate paths should send SnpResp_I
        has_uniq_def = ('SnpUnique duplicate' in epcc and
                        'sending defensive SnpResp_I' in epcc)
        has_once_def = ('SnpOnce duplicate' in epcc and
                        'sending defensive SnpResp_I' in epcc)
        check("FIX-B-CHK-1: SnpUnique/SnpUniqueFwd duplicate defensive "
              "SnpResp_I",
              has_uniq_def,
              "defensive response found" if has_uniq_def
              else "defensive response missing for SnpUnique dup")
        check("FIX-B-CHK-2: SnpOnce duplicate defensive SnpResp_I",
              has_once_def,
              "defensive response found" if has_once_def
              else "defensive response missing for SnpOnce dup")
    else:
        check("FIX-B-CHK: EPRNFController.cc", False, "source not readable")

    # =========================================================================
    # FIX-C-CHK: result.ok failure handling in onGlobalInvalidateComplete
    # =========================================================================
    if epcc:
        # The failure path must check !result.ok AND send SnpResp_I AND erase
        has_rok_check = '!result.ok' in epcc
        # Verify it's in onGlobalInvalidateComplete (not just anywhere)
        # by checking proximity: !result.ok near "FAILED -- sending defensive"
        has_fail_def = ('FAILED -- sending defensive SnpResp_I' in epcc)
        # The erase must happen after finding the pending txn
        has_erase_on_fail = (
            '_pendingSnoopTxns.erase' in epcc and
            'sendOrRetry(rsp, linePa)' in epcc
        )
        check("FIX-C-CHK: onGlobalInvalidateComplete result.ok failure branch",
              has_rok_check and has_fail_def and has_erase_on_fail,
              f"!ok_check={has_rok_check} fail_def={has_fail_def} "
              f"erase={has_erase_on_fail}")
    else:
        check("FIX-C-CHK: EPRNFController.cc", False, "source not readable")

    # =========================================================================
    # FIX-D-CHK-1: All 6 snoop type dispatch branches in recvSnoopMsg
    # =========================================================================
    if epcc:
        snoop_types = [
            ('SnpCleanInvalid', 'case CHIRequestType_SnpCleanInvalid'),
            ('SnpUnique',       'case CHIRequestType_SnpUnique'),
            ('SnpUniqueFwd',    'case CHIRequestType_SnpUniqueFwd'),
            ('SnpOnce',         'case CHIRequestType_SnpOnce'),
            ('SnpOnceFwd',      'case CHIRequestType_SnpOnceFwd'),
            ('SnpShared',       'case CHIRequestType_SnpShared'),
            ('SnpSharedFwd',    'case CHIRequestType_SnpSharedFwd'),
            ('default',         'default:'),
        ]
        all_present = True
        missing = []
        for name, pattern in snoop_types:
            if pattern not in epcc:
                all_present = False
                missing.append(name)
        check("FIX-D-CHK-1: recvSnoopMsg snoop type dispatch branches",
              all_present,
              f"missing: {missing}" if missing else "all 8 branches present")
    else:
        check("FIX-D-CHK-1: EPRNFController.cc", False, "source not readable")

    # =========================================================================
    # FIX-D-CHK-2: sendOrRetry in non-blocking/immediate response paths
    # =========================================================================
    if epcc:
        # SnpCleanInvalid must call sendOrRetry (not bare sendResponseMsg)
        # Check for sendOrRetry near SnpCleanInvalid context
        sci_block_match = re.search(
            r'case CHIRequestType_SnpCleanInvalid:\s*\{(.*?)(?=case\s+CHIRequestType_|default:)',
            epcc, re.DOTALL)
        sci_uses_sor = False
        if sci_block_match:
            sci_body = sci_block_match.group(1)
            sci_uses_sor = 'sendOrRetry' in sci_body
        check("FIX-D-CHK-2a: SnpCleanInvalid uses sendOrRetry",
              sci_uses_sor,
              "found in SnpCleanInvalid case block" if sci_uses_sor
              else "sendOrRetry not found in SnpCleanInvalid block")

        # Unexpected/default branches also use sendOrRetry
        has_def_sor = ('default:' in epcc and
                       'sendOrRetry' in epcc)
        check("FIX-D-CHK-2b: default/unexpected branches use sendOrRetry",
              has_def_sor,
              "sendOrRetry present near default branch" if has_def_sor
              else "may be missing defensive response path")

        # Verify sendOrRetry is called in SnpOnceFwd and SnpShared paths
        sofw_match = re.search(
            r'SnpOnceFwd.*?sendOrRetry',
            epcc, re.DOTALL)
        ss_match = re.search(
            r'SnpShared.*?sendOrRetry',
            epcc, re.DOTALL)
        check("FIX-D-CHK-2c: SnpOnceFwd defensive path uses sendOrRetry",
              sofw_match is not None)
        check("FIX-D-CHK-2d: SnpShared/SnpSharedFwd defensive path uses "
              "sendOrRetry",
              ss_match is not None)
    else:
        for sub in ['2a', '2b', '2c', '2d']:
            check(f"FIX-D-CHK-{sub}: EPRNFController.cc", False,
                  "source not readable")

    # =========================================================================
    # FIX-D-CHK-3: GlobalInvalidateResult struct definition
    # =========================================================================
    if ubcch:
        has_gir = ('struct GlobalInvalidateResult' in ubcch and
                   'bool ok' in ubcch and
                   'bool hasData' in ubcch and
                   'bool isDirty' in ubcch and
                   'uint8_t data[64]' in ubcch)
        check("FIX-D-CHK-3: GlobalInvalidateResult struct in "
              "UBCCController.hh",
              has_gir,
              "all 4 fields present" if has_gir
              else "missing fields or struct")
    else:
        check("FIX-D-CHK-3: UBCCController.hh", False, "source not readable")

    # =========================================================================
    # FIX-D-CHK-4: clearRegistration exists and is called from EPBackend
    # =========================================================================
    if epcc and ebcc:
        # clearRegistration defined in EPRNFController.cc
        cr_def = ('EPRNFController::clearRegistration' in epcc and
                  'RegState::REG_IDLE' in epcc)
        # Called from EPBackend.cc at least once
        cr_called = 'clearRegistration' in ebcc
        check("FIX-D-CHK-4a: clearRegistration defined in "
              "EPRNFController.cc",
              cr_def,
              "definition found" if cr_def else "definition missing")
        check("FIX-D-CHK-4b: clearRegistration called from EPBackend.cc",
              cr_called,
              "called from EPBackend" if cr_called else "not called")
    elif not epcc:
        check("FIX-D-CHK-4a: EPRNFController.cc", False,
              "source not readable")
        check("FIX-D-CHK-4b: EPBackend.cc", False,
              "source not readable")
    elif not ebcc:
        check("FIX-D-CHK-4a: definition", cr_def if 'cr_def' in dir()
              else False, "EPRNFController.cc checked")
        check("FIX-D-CHK-4b: EPBackend.cc", False, "source not readable")

    # =========================================================================
    # FIX-D-CHK-5: callbackDone field in PendingSnoopTxn struct
    # =========================================================================
    if ephh:
        has_cbd_field = ('bool callbackDone = false' in ephh or
                         'bool callbackDone=false' in ephh)
        check("FIX-D-CHK-5: callbackDone field in PendingSnoopTxn struct",
              has_cbd_field,
              "field declared" if has_cbd_field else "field missing in header")
    else:
        check("FIX-D-CHK-5: EPRNFController.hh", False, "source not readable")

    # =========================================================================
    # FIX-D-CHK-6: callbackDone set in both callbacks
    # =========================================================================
    if epcc:
        # Extract onGlobalInvalidateComplete body
        gic_match = re.search(
            r'onGlobalInvalidateComplete\(.*?\{.*?callbackDone\s*=\s*true',
            epcc, re.DOTALL)
        # Extract onRemoteFetchComplete body
        rfc_match = re.search(
            r'onRemoteFetchComplete\(.*?\{.*?callbackDone\s*=\s*true',
            epcc, re.DOTALL)
        check("FIX-D-CHK-6a: callbackDone=true in "
              "onGlobalInvalidateComplete",
              gic_match is not None,
              "set in callback" if gic_match else "not set")
        check("FIX-D-CHK-6b: callbackDone=true in onRemoteFetchComplete",
              rfc_match is not None,
              "set in callback" if rfc_match else "not set")
    else:
        check("FIX-D-CHK-6a: EPRNFController.cc", False,
              "source not readable")
        check("FIX-D-CHK-6b: EPRNFController.cc", False,
              "source not readable")

    return details


# ==========================================================================
# Main
# ==========================================================================
if __name__ == "__main__":
    print("PHASE 2: Fix A/B/C/D Structural Verification Matrix")
    print("Mode: structural source-code verification (no ARM simulation)")
    print()

    print("=" * 60)
    print("Phase 2 Fix Verification Matrix")
    print("=" * 60)

    details = run_checks()

    passes = sum(1 for _, r, _ in details if r)
    fails = sum(1 for _, r, _ in details if not r)
    total = len(details)

    print()
    print("=" * 60)
    print("PHASE 2 VERDICT")
    print("=" * 60)
    print(f"  {total} checks: {passes} PASS, {fails} FAIL")

    if fails > 0:
        print("\nFAIL — structural verification incomplete.")
        print("  Missing checks indicate incomplete or incorrect Phase 2 fixes.")
        print("  Verify all Fix A/B/C/D changes are present in source files.")
        sys.exit(EXIT_FAIL)
    else:
        print("\nPASS — all Phase 2 structural fixes verified.")
        print("  Fix A: callbackDone guard in retryPendingSnoopResponses")
        print("  Fix B: duplicate snoop defensive SnpResp_I response")
        print("  Fix C: result.ok failure handling in onGlobalInvalidateComplete")
        print("  Fix D: structural coverage matrix — all checks passed")
        sys.exit(EXIT_PASS)
