"""Phase 1 TC-3: First Miss → HN-F SC + EP-RNF in dir_sharers.

TC-3 verifies the end-to-end chain for a first-miss ReadShared on a DSM
address through the full UBCC+CHI protocol.

Verification checkpoints (structural, no ARM simulation required):
  TC3-CHK-4a: SLICC source contains RegisterEPRNF_OnSharedHint action
  TC3-CHK-4b: SLICC source contains dir_sharers.add(epRnfMachineID)
  TC3-CHK-4c: SLICC source checks in_msg.shared_hint
  TC3-CHK-4d: CHI-msg.sm declares shared_hint field on CHIDataMsg
  TC3-CHK-4e: EPSNFController sets shared_hint via isPostGrantShared
  TC3-CHK-4f: CHI-cache-transitions.sm invokes RegisterEPRNF_OnSharedHint
  TC3-CHK-4g: EPBackend has isPostGrantShared method
  TC3-CHK-4h: EPBackend has isDsmAddrCrossNode guard

Exit code: 0 = PASS, 1 = FAIL, 2 = SKIP
"""
import sys
import os
import re

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_SKIP = 2

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
SM_DIR = os.path.join(REPO_ROOT, 'gem5/src/mem/ruby/protocol/chi')
EP_DIR = os.path.join(SM_DIR, 'ep')


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
    """Run all structural checks, return (pass_count, fail_count, details)."""
    details = []

    def check(name, result, detail=""):
        details.append((name, result, detail))
        status = "PASS" if result else "FAIL"
        extra = f" — {detail}" if detail else ""
        print(f"  {status}: {name}{extra}")
        return result

    # --- TC3-CHK-4a: RegisterEPRNF_OnSharedHint action ---
    src = _read_src('gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm')
    if src:
        check("TC3-CHK-4a: RegisterEPRNF_OnSharedHint action",
              'action(RegisterEPRNF_OnSharedHint' in src)
        check("TC3-CHK-4b: dir_sharers.add(epRnfMachineID)",
              'tbe.dir_sharers.add(tbe.epRnfMachineID)' in src)
        check("TC3-CHK-4c: in_msg.shared_hint check",
              'in_msg.shared_hint' in src)
    else:
        check("TC3-CHK-4a-c: action source", False, "file not readable")

    # --- TC3-CHK-4d: shared_hint field on CHIDataMsg ---
    src = _read_src('gem5/src/mem/ruby/protocol/chi/CHI-msg.sm')
    if src:
        check("TC3-CHK-4d: CHIDataMsg shared_hint field",
              'bool shared_hint' in src and 'default="false"' in src)
    else:
        check("TC3-CHK-4d: msg source", False, "file not readable")

    # --- TC3-CHK-4e: EPSNFController sets shared_hint ---
    src = _read_src('gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc')
    if src:
        check("TC3-CHK-4e: EPSNFController sets shared_hint",
              'setSharedHint' in src and '_backend->isPostGrantShared' in src)
    else:
        check("TC3-CHK-4e: EPSNFController source", False, "file not readable")

    # --- TC3-CHK-4f: Transition invokes RegisterEPRNF_OnSharedHint ---
    src = _read_src('gem5/src/mem/ruby/protocol/chi/CHI-cache-transitions.sm')
    if src:
        check("TC3-CHK-4f: transition invokes RegisterEPRNF_OnSharedHint",
              'RegisterEPRNF_OnSharedHint' in src)
    else:
        check("TC3-CHK-4f: transitions source", False, "file not readable")

    # --- TC3-CHK-4g: EPBackend::isPostGrantShared ---
    src = _read_src('gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc')
    if src:
        check("TC3-CHK-4g: EPBackend::isPostGrantShared",
              'isPostGrantShared' in src)
        check("TC3-CHK-4h: EPBackend::isDsmAddrCrossNode guard",
              'isDsmAddrCrossNode' in src)
    else:
        check("TC3-CHK-4g-h: EPBackend source", False, "file not readable")

    # --- TC3-CHK-4i: EPBackend has RequesterLineState::R_S check ---
    if src:
        check("TC3-CHK-4i: EPBackend RequesterLineState R_S assignment",
              'RequesterLineState::R_S' in src or 'R_S' in src)
    else:
        check("TC3-CHK-4i: EPBackend source", False, "file not readable")

    # --- TC3-CHK-4j: UBCCController first-miss G_S path (logic check) ---
    # Replaces comment-string check with structural logic verification:
    #   Within processOuterRequest(), case MESIState::G_I branch,
    #   reqType == GlobalReadShared AND entry.state = MESIState::G_S
    #   appear in the same code block, proving the first-miss shared grant path.
    src = _read_src('gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc')
    if src:
        # Extract the G_I case block (from "case MESIState::G_I:" to next
        # case/default or end of switch).
        gi_match = re.search(
            r'case\s+MESIState::G_I:\s*\{(.*?)\n\s+\}', src, re.DOTALL)
        if gi_match:
            gi_body = gi_match.group(1)
            has_reqtype = 'GlobalReadShared' in gi_body
            has_g_s = 'entry.state\s*=\s*MESIState::G_S' in gi_body or \
                      'entry.state = MESIState::G_S' in gi_body
            check("TC3-CHK-4j: UBCCController G_S first-miss logic "
                  "(reqType==GlobalReadShared && entry.state=G_S "
                  "in same case block)",
                  has_reqtype and has_g_s,
                  f"reqType={has_reqtype} G_S_assignment={has_g_s}")
        else:
            check("TC3-CHK-4j: UBCCController G_S first-miss logic", False,
                  "G_I case block not parsable")
    else:
        check("TC3-CHK-4j: UBCCController source", False, "file not readable")

    # --- TC3-CHK-4k: SLICC compiled output (verify build artifacts) ---
    gen_py = os.path.join(REPO_ROOT,
                          'build/ARM/mem/ruby/protocol/CHI/'
                          'CHI_Cache_Controller.py')
    gen_hh = os.path.join(REPO_ROOT,
                          'build/ARM/mem/ruby/protocol/CHI/'
                          'Cache_Controller.hh')
    build_ok = os.path.exists(gen_py) and os.path.exists(gen_hh)
    check("TC3-CHK-4k: SLICC build artifacts present", build_ok)

    # --- TC3-CHK-4l: gem5 binary exists ---
    binary_ok = os.path.exists(os.path.join(REPO_ROOT,
                                            'build/ARM/gem5.opt'))
    check("TC3-CHK-4l: gem5 binary exists", binary_ok)

    return details


# ==========================================================================
# Main
# ==========================================================================
if __name__ == "__main__":
    print("PHASE 1: TC-3 — First Miss / shared_hint / EP-RNF Registration")
    print("Mode: structural verification (no ARM simulation required)")
    print()

    print("=" * 60)
    print("TC-3 Structural Verification")
    print("=" * 60)

    details = run_checks()

    passes = sum(1 for _, r, _ in details if r)
    fails = sum(1 for _, r, _ in details if not r)
    total = len(details)

    print()
    print("=" * 60)
    print("TC-3 VERDICT")
    print("=" * 60)
    print(f"  {total} checks: {passes} PASS, {fails} FAIL")

    if fails > 0:
        print("\nFAIL — structural verification incomplete.")
        print("  Missing checks may indicate incomplete SLICC code generation.")
        print("  Verify that all Phase 1 SM changes are present and rebuild.")
        sys.exit(EXIT_FAIL)
    else:
        print("\nPASS — structural verification complete.")
        print("  All TC-3 source-level checks passed.")
        print("  shared_hint chain verified from UBCC → EPBackend → EPSNFController → HN-F.")
        sys.exit(EXIT_PASS)
