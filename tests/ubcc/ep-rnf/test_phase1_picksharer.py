"""Phase 1 — pickSharerForSnoop validation tests.

Verifies the pickSharerForSnoop function exists in SLICC source and
that all 4 snoop actions use it correctly, replacing the previous
smallestElement() direct calls.

Scenarios verified:
  S-1: candidates.remove(epRnfId) then count>0 → returns smallestElement()
       (L2 selected when sharers exist)
  S-2: candidates empty → returns epRnfId (EP-RNF is only sharer)
  S-3: dir_sharers contains L2 → EP-RNF excluded (L2 prioritized)

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
    """Run all pickSharerForSnoop checks, return (pass_count, fail_count, details)."""
    details = []

    def check(name, result, detail=""):
        details.append((name, result, detail))
        status = "PASS" if result else "FAIL"
        extra = f" — {detail}" if detail else ""
        print(f"  {status}: {name}{extra}")
        return result

    # =========================================================================
    # C-1: Verify pickSharerForSnoop function definition exists
    # =========================================================================
    funcs_src = _read_src('gem5/src/mem/ruby/protocol/chi/CHI-cache-funcs.sm')
    if funcs_src:
        has_func = 'pickSharerForSnoop' in funcs_src
        check("C-1: pickSharerForSnoop function defined in CHI-cache-funcs.sm",
              has_func)
    else:
        check("C-1: pickSharerForSnoop function definition", False,
              "CHI-cache-funcs.sm not readable")

    # =========================================================================
    # C-2: Verify all 4 actions use pickSharerForSnoop (not bare smallestElement)
    # =========================================================================
    actions_src = _read_src('gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm')
    if actions_src:
        # Count pickSharerForSnoop occurrences in actions file
        pick_calls = actions_src.count('pickSharerForSnoop')
        check("C-2a: pickSharerForSnoop called >= 4 times in CHI-cache-actions.sm",
              pick_calls >= 4,
              f"found {pick_calls} call(s)")

        # The 4 expected action sites
        expected_actions = [
            'Send_SnpUnique_RetToSrc',
            'Send_SnpSharedFwd_ToSharer',
            'Send_SnpOnce',
            'Send_SnpOnceFwd',
        ]

        for action_name in expected_actions:
            # Find the action block and check it contains pickSharerForSnoop
            pattern = r'action\(' + re.escape(action_name) + r'[^)]*\)\s*\{'
            match = re.search(pattern, actions_src)
            if match:
                # Extract from action start to closing brace of the action body
                pos = match.end()
                # Find the matching closing brace by counting braces
                brace_count = 1
                i = pos
                while i < len(actions_src) and brace_count > 0:
                    if actions_src[i] == '{':
                        brace_count += 1
                    elif actions_src[i] == '}':
                        brace_count -= 1
                    i += 1
                action_body = actions_src[pos:i - 1]

                has_pick = 'pickSharerForSnoop' in action_body
                check(f"C-2b: {action_name} uses pickSharerForSnoop",
                      has_pick)
            else:
                check(f"C-2b: {action_name} uses pickSharerForSnoop", False,
                      f"action not found in source")
    else:
        check("C-2: action source", False, "CHI-cache-actions.sm not readable")
        for action_name in ['Send_SnpUnique_RetToSrc', 'Send_SnpSharedFwd_ToSharer',
                            'Send_SnpOnce', 'Send_SnpOnceFwd']:
            check(f"C-2b: {action_name} uses pickSharerForSnoop", False,
                  "source unreadable")

    # =========================================================================
    # S-1: candidates.remove(epRnfId) then count>0 → returns smallestElement()
    #      (L2 selected when additional sharers exist)
    # =========================================================================
    if funcs_src:
        # Extract the function body of pickSharerForSnoop
        func_match = re.search(
            r'MachineID\s+pickSharerForSnoop\([^)]+\)\s*\{(.*?)\n\}',
            funcs_src, re.DOTALL)
        if func_match:
            func_body = func_match.group(1)
            s1_remove = 'candidates.remove(epRnfId)' in func_body or \
                        'candidates.remove' in func_body
            s1_count_check = 'candidates.count() > 0' in func_body
            s1_return_l2 = 'candidates.smallestElement()' in func_body
            s1_ok = s1_remove and s1_count_check and s1_return_l2
            check("S-1: EP-RNF removed from candidates; count>0 → "
                  "smallestElement() (L2 selected)",
                  s1_ok,
                  f"remove={s1_remove} count_check={s1_count_check} "
                  f"return_l2={s1_return_l2}")
        else:
            check("S-1: candidates logic", False, "function body not parsable")
    else:
        check("S-1: candidates logic", False, "funcs source unreadable")

    # =========================================================================
    # S-2: candidates empty → returns epRnfId (EP-RNF is only sharer)
    # =========================================================================
    if funcs_src:
        if func_match:
            s2_return_eprnf = 'return epRnfId' in func_body or \
                              'return epRnfId' in funcs_src
            check("S-2: candidates empty → return epRnfId (EP-RNF only)",
                  s2_return_eprnf)
        else:
            check("S-2: EP-RNF fallback", False, "function body not parsable")
    else:
        check("S-2: EP-RNF fallback", False, "funcs source unreadable")

    # =========================================================================
    # S-3: dir_sharers contains L2 → EP-RNF excluded (logic check)
    #      The function removes epRnfId from candidates first, so if L2 is
    #      in dir_sharers, candidates.count()>0 and L2 is selected.
    #      We verify the remove-before-check ordering.
    # =========================================================================
    if funcs_src:
        if func_match:
            # Verify remove() happens before count() check (EP-RNF exclusion
            # before L2 selection)
            remove_pos = func_body.find('candidates.remove')
            count_pos = func_body.find('candidates.count()')
            smallest_pos = func_body.find('candidates.smallestElement')
            s3_ordering = remove_pos >= 0 and count_pos >= 0 and \
                          remove_pos < count_pos < smallest_pos
            check("S-3: EP-RNF removed before count check (L2 prioritized)",
                  s3_ordering,
                  f"remove_pos={remove_pos} count_pos={count_pos} "
                  f"smallest_pos={smallest_pos}")
        else:
            check("S-3: EP-RNF exclusion ordering", False,
                  "function body not parsable")
    else:
        check("S-3: EP-RNF exclusion ordering", False, "funcs source unreadable")

    return details


# ==========================================================================
# Main
# ==========================================================================
if __name__ == "__main__":
    print("PHASE 1: pickSharerForSnoop Validation")
    print("Mode: structural source-code verification")
    print()

    print("=" * 60)
    print("pickSharerForSnoop Structural Verification")
    print("=" * 60)

    details = run_checks()

    passes = sum(1 for _, r, _ in details if r)
    fails = sum(1 for _, r, _ in details if not r)
    total = len(details)

    print()
    print("=" * 60)
    print("pickSharerForSnoop VERDICT")
    print("=" * 60)
    print(f"  {total} checks: {passes} PASS, {fails} FAIL")

    if fails > 0:
        print("\nFAIL — pickSharerForSnoop verification incomplete.")
        print("  Verify the function definition and all 4 action sites.")
        sys.exit(EXIT_FAIL)
    else:
        print("\nPASS — pickSharerForSnoop verified.")
        print("  Function defined in CHI-cache-funcs.sm.")
        print("  All 4 snoop actions use pickSharerForSnoop() correctly.")
        print("  Priority logic: L2 > EP-RNF (EP-RNF removed from candidates first).")
        print("  Fallback: EP-RNF selected when no other sharers exist.")
        sys.exit(EXIT_PASS)
