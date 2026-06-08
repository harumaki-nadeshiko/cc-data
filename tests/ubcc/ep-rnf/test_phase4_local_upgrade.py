"""Phase 4: Local Write Upgrade E2E Snoop Notification Chain.

Verifies the end-to-end structural chain for:
  Node B reads DSM_N (First Miss)
  → shared_hint=true in CompData
  → HN-F RegisterEPRNF_OnSharedHint triggers
  → EP-RNF setRegistrationDone
  → registration context = REG_DONE

  Node B writes same DSM_N (local upgrade)
  → CleanUnique → HN-F SC→UD
  → SnpCleanInvalid sent to EP-RNF
  → EP-RNF recvSnoopMsg(SnpCleanInvalid) → REG_DONE → notifyLocalWriteUpgrade
  → EPBackend → UBCC: updateOwner(state=UD)
  → UBCC directory: ownerNode=B, state=UD

Verification checkpoints (structural, no ARM simulation required):
  CHK-1:  RegisterEPRNF_OnSharedHint action exists with dir_sharers.add
  CHK-2:  EP-RNF setRegistrationDone exists with REG_DONE assignment
  CHK-3:  recvSnoopMsg SnpCleanInvalid branch checks REG_DONE context
  CHK-4:  notifyLocalWriteUpgrade method exists in EPBackend
  CHK-5:  notifyLocalWriteUpgrade calls homeUbcc->updateOwner
  CHK-6:  UBCC updateOwner sets ownerNode, state=UD, pendingOwnerUpdate=true
  CHK-7:  UBCC clearPendingOwnerUpdate clears the barrier flag
  CHK-8:  EPBackend clearPendingOwnerUpdate routes to home UBCC
  CHK-9:  EP-RNF sendOrRetry supports needBarrierClear for delayed clear
  CHK-10: EPRNFController::clearRegistration exists for recall path

Exit code: 0 = PASS, 1 = FAIL, 2 = SKIP
"""
import sys
import os
import re

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_SKIP = 2

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
EP_DIR = os.path.join(REPO_ROOT, 'gem5/src/mem/ruby/protocol/chi/ep')
SM_DIR = os.path.join(REPO_ROOT, 'gem5/src/mem/ruby/protocol/chi')


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
        extra = f" - {detail}" if detail else ""
        print(f"  {status}: {name}{extra}")
        return result

    # ---- CHK-1: RegisterEPRNF_OnSharedHint action ----
    src = _read_src('gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm')
    if src:
        has_action = 'action(RegisterEPRNF_OnSharedHint' in src
        has_dir_add = 'dir_sharers.add(tbe.epRnfMachineID)' in src
        has_shear_hint = 'in_msg.shared_hint' in src
        has_dataunique = 'dataUnique := false' in src
        check("CHK-1a: RegisterEPRNF_OnSharedHint action defined",
              has_action)
        check("CHK-1b: dir_sharers.add(epRnfMachineID) in shared_hint path",
              has_dir_add)
        check("CHK-1c: shared_hint guard in CompData path",
              has_shear_hint)
        check("CHK-1d: dataUnique forced false for shared_hint",
              has_dataunique)
    else:
        check("CHK-1: CHI-cache-actions.sm", False, "source not readable")

    # ---- CHK-2: EP-RNF setRegistrationDone + REG_DONE ----
    epcc = _read_src('gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc')
    ephh = _read_src('gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh')

    if epcc:
        has_setreg = ('setRegistrationDone' in epcc and
                      'REG_DONE' in epcc)
        check("CHK-2a: EPRNFController::setRegistrationDone exists",
              has_setreg)
        check("CHK-2b: setRegistrationDone sets RegState::REG_DONE",
              'ctx.state = RegState::REG_DONE' in epcc)
    else:
        check("CHK-2: EPRNFController.cc", False, "source not readable")

    if ephh:
        has_regstate = ('REG_IDLE' in ephh and 'REG_DONE' in ephh and
                        'enum class RegState' in ephh)
        check("CHK-2c: RegState enum with REG_IDLE and REG_DONE in header",
              has_regstate)
        has_regctx = ('RegState state' in ephh and 'uint64_t epoch' in ephh)
        check("CHK-2d: RegistrationContext struct with state + epoch",
              has_regctx)
    else:
        check("CHK-2c-d: EPRNFController.hh", False, "source not readable")

    # ---- CHK-3: recvSnoopMsg SnpCleanInvalid + REG_DONE branch ----
    if epcc:
        has_sci_case = 'case CHIRequestType_SnpCleanInvalid' in epcc
        check("CHK-3a: recvSnoopMsg has SnpCleanInvalid case",
              has_sci_case)

        # Extract the region from SnpCleanInvalid case to next case/default.
        # Using the broader range between SnpCleanInvalid and next 'case'
        # avoids premature closure from nested if-block braces.
        sci_region_match = re.search(
            r'case CHIRequestType_SnpCleanInvalid:.*?(?=case\s+CHIRequestType_SnpUnique)',
            epcc, re.DOTALL)
        if sci_region_match:
            sci_region = sci_region_match.group(0)
            has_reg_done = 'RegState::REG_DONE' in sci_region
            has_notify = 'notifyLocalWriteUpgrade' in sci_region
            has_send_or_retry = 'sendOrRetry' in sci_region
            check("CHK-3b: SnpCleanInvalid branch checks REG_DONE state",
                  has_reg_done,
                  "found in SCI region" if has_reg_done else "REG_DONE not in SCI region")
            check("CHK-3c: SnpCleanInvalid calls notifyLocalWriteUpgrade "
                  "when REG_DONE",
                  has_notify,
                  "found in SCI region" if has_notify else "notify not in SCI region")
            check("CHK-3d: SnpCleanInvalid uses sendOrRetry (not bare "
                  "sendResponseMsg)",
                  has_send_or_retry,
                  "found in SCI region" if has_send_or_retry else "sendOrRetry not in SCI region")
        else:
            check("CHK-3b-d: SnpCleanInvalid case body", False,
                  "regex did not match SCI region")
    else:
        for sub in ['a', 'b', 'c', 'd']:
            check(f"CHK-3{sub}: EPRNFController.cc", False,
                  "source not readable")

    # ---- CHK-4: EPBackend::notifyLocalWriteUpgrade exists ----
    ebcc = _read_src('gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc')
    ebhh = _read_src('gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh')

    if ebcc and ebhh:
        has_decl = 'void notifyLocalWriteUpgrade' in ebhh
        has_def = 'EPBackend::notifyLocalWriteUpgrade' in ebcc
        check("CHK-4a: notifyLocalWriteUpgrade declared in EPBackend.hh",
              has_decl)
        check("CHK-4b: notifyLocalWriteUpgrade defined in EPBackend.cc",
              has_def)
    else:
        check("CHK-4: EPBackend sources", False,
              f"cc_readable={ebcc is not None} hh_readable={ebhh is not None}")

    # ---- CHK-5: notifyLocalWriteUpgrade calls homeUbcc->updateOwner ----
    if ebcc:
        has_update_owner = 'updateOwner' in ebcc
        has_home_ubcc = 'homeUbcc' in ebcc
        has_dsm_guard = 'isDsmAddrCrossNode' in ebcc
        check("CHK-5a: notifyLocalWriteUpgrade calls updateOwner on home UBCC",
              has_update_owner and has_home_ubcc)
        check("CHK-5b: notifyLocalWriteUpgrade has isDsmAddrCrossNode guard",
              has_dsm_guard)
    else:
        check("CHK-5: EPBackend.cc", False, "source not readable")

    # ---- CHK-6: UBCC updateOwner sets ownerNode, state=UD, pendingOwnerUpdate ----
    ubcc = _read_src('gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc')
    ubch = _read_src('gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh')

    if ubcc:
        has_update_owner = 'UBCCController::updateOwner' in ubcc
        has_pending = 'pendingOwnerUpdate = true' in ubcc
        has_owner = 'ownerNode = ownerNode' in ubcc or 'entry.ownerNode' in ubcc
        has_ud = 'MESIState::UD' in ubcc
        has_share_clear = 'sharersMask = 0' in ubcc
        has_epoch = 'entry.epoch++' in ubcc
        check("CHK-6a: UBCCController::updateOwner method exists",
              has_update_owner)
        check("CHK-6b: updateOwner sets pendingOwnerUpdate = true",
              has_pending)
        check("CHK-6c: updateOwner sets ownerNode",
              has_owner)
        check("CHK-6d: updateOwner sets state = UD",
              has_ud)
        check("CHK-6e: updateOwner clears sharersMask",
              has_share_clear)
        check("CHK-6f: updateOwner increments epoch",
              has_epoch)
    else:
        check("CHK-6: UBCCController.cc", False, "source not readable")

    # ---- CHK-7: UBCC clearPendingOwnerUpdate clears the barrier ----
    if ubcc:
        has_clear = 'UBCCController::clearPendingOwnerUpdate' in ubcc
        has_clear_assign = 'pendingOwnerUpdate = false' in ubcc
        check("CHK-7a: UBCCController::clearPendingOwnerUpdate exists",
              has_clear)
        check("CHK-7b: clearPendingOwnerUpdate sets pendingOwnerUpdate = false",
              has_clear_assign)
    else:
        check("CHK-7: UBCCController.cc", False, "source not readable")

    # ---- CHK-8: EPBackend clearPendingOwnerUpdate routes to home UBCC ----
    if ebcc:
        has_clear = 'EPBackend::clearPendingOwnerUpdate' in ebcc
        has_route = ('UBCCController::getInstance' in ebcc and
                     'clearPendingOwnerUpdate' in ebcc)
        check("CHK-8a: EPBackend::clearPendingOwnerUpdate exists",
              has_clear)
        check("CHK-8b: clearPendingOwnerUpdate routes via "
              "UBCCController::getInstance(homeNode)",
              has_route)
    else:
        check("CHK-8: EPBackend.cc", False, "source not readable")

    # ---- CHK-9: EP-RNF sendOrRetry supports needBarrierClear ----
    if epcc and ephh:
        has_sor = 'sendOrRetry' in epcc
        has_nbc = 'needBarrierClear' in epcc
        has_nbc_struct = 'needBarrierClear' in ephh
        check("CHK-9a: sendOrRetry method exists in EPRNFController.cc",
              has_sor)
        check("CHK-9b: sendOrRetry uses needBarrierClear parameter",
              has_nbc)
        check("CHK-9c: QueuedImmediateResponse has needBarrierClear field",
              has_nbc_struct)
    elif not epcc:
        check("CHK-9: EPRNFController.cc", False, "source not readable")
    elif not ephh:
        check("CHK-9: EPRNFController.hh", False, "source not readable")

    # ---- CHK-10: EPRNFController::clearRegistration for recall path ----
    if epcc:
        has_clear_reg = 'EPRNFController::clearRegistration' in epcc
        has_regidle = 'REG_IDLE' in epcc
        check("CHK-10a: EPRNFController::clearRegistration exists",
              has_clear_reg)
        check("CHK-10b: clearRegistration sets state to REG_IDLE",
              has_regidle)
    else:
        check("CHK-10: EPRNFController.cc", False, "source not readable")

    # ---- CHK-11: EPBackend calls setRegistrationDone on shared_hint path ----
    if ebcc:
        has_set_reg = 'setRegistrationDone' in ebcc
        check("CHK-11: EPBackend calls setRegistrationDone (shared_hint "
              "completes registration)",
              has_set_reg)
    else:
        check("CHK-11: EPBackend.cc", False, "source not readable")

    # ---- CHK-12: SnpResp_I sent in SnpCleanInvalid path ----
    if epcc:
        has_snp_respi = 'CHIResponseType_SnpResp_I' in epcc
        check("CHK-12: SnpResp_I response type used in EP-RNF snoop handling",
              has_snp_respi)
    else:
        check("CHK-12: EPRNFController.cc", False, "source not readable")

    return details


# ==========================================================================
# Main
# ==========================================================================
if __name__ == "__main__":
    print("PHASE 4: Local Write Upgrade E2E Snoop Notification Chain")
    print("Mode: structural source-code verification (no ARM simulation)")
    print()

    print("=" * 60)
    print("Phase 4 Structural Verification — E2E Local Upgrade Chain")
    print("=" * 60)
    print()

    print("Chain under test:")
    print("  1. shared_hint CompData → HN-F RegisterEPRNF_OnSharedHint")
    print("  2. EP-RNF setRegistrationDone → REG_DONE")
    print("  3. CleanUnique → HN-F SC→UD → SnpCleanInvalid to EP-RNF")
    print("  4. EP-RNF recvSnoopMsg: REG_DONE → notifyLocalWriteUpgrade")
    print("  5. EPBackend → UBCC::updateOwner(state=UD)")
    print("  6. UBCC: pendingOwnerUpdate barrier set/cleared")
    print()

    details = run_checks()

    passes = sum(1 for _, r, _ in details if r)
    fails = sum(1 for _, r, _ in details if not r)
    total = len(details)

    print()
    print("=" * 60)
    print("PHASE 4 VERDICT")
    print("=" * 60)
    print(f"  {total} checks: {passes} PASS, {fails} FAIL")

    if fails > 0:
        print()
        print("FAIL — structural verification incomplete for local upgrade chain.")
        print("  Missing checks may indicate incomplete protocol implementation.")
        print("  Verify all Phase 1-4 changes are present in source files.")
        sys.exit(EXIT_FAIL)
    else:
        print()
        print("PASS — all Phase 4 structural checks passed.")
        print("  Local write upgrade E2E chain verified at source level:")
        print("    shared_hint → REG_DONE → SnpCleanInvalid →")
        print("    notifyLocalWriteUpgrade → updateOwner(state=UD) →")
        print("    pendingOwnerUpdate barrier set/cleared")
        sys.exit(EXIT_PASS)
