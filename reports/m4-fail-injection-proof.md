# M4 FAIL Injection Proof

**Phase:** M4 — Sentinel Registration (failure-mode verification)
**Date:** 2026-05-26
**Purpose:** Prove that a genuine M4_CHECK failure correctly propagates through C++ → captured output → Python harness → `exit(1)`.

> **实跑证据:** 完整注入-还原双次运行日志见 [m4-fail-injection-run.txt](m4-fail-injection-run.txt)

---

## Real-Run Verification (2026-05-26)

The FAIL injection scenario described below was executed as a real run with the following results:

| Scenario | C++ Output | Python Parsing | Exit Code |
|----------|-----------|----------------|-----------|
| FAIL injection active | `8/24 PASS, 1 FAIL, 15 SKIP` + `M4_SELF_TEST_FAILED=1` | `M4_PYTHON: explicit FAIL marker found, treating as fail` | **1** |
| Injection reverted (clean) | `8/23 PASS, 0 FAIL, 15 SKIP` + `M4_SELF_TEST_PASSED=1` | `M4_PYTHON_TEST_HARNESS: DONE — all executed checks passed` | **0** |

Key evidence from C++ captured output:
```
// FAIL run:
  M4 FAIL-INJECT: deliberate failure injection for CI gate verification: FAIL (This is an intentional infrastructure-level FAIL test)
=== M4 Self-Test Results: 8/24 PASS, 1 FAIL, 15 SKIP ===
M4_SELF_TEST_FAILED=1

// PASS run (reverted):
=== M4 Self-Test Results: 8/23 PASS, 0 FAIL, 15 SKIP ===
M4_SELF_TEST_PASSED=1
```

The full raw output (233 lines) is archived in `reports/m4-fail-injection-run.txt`.

---

## Injection Description

A single artificial FAIL check is injected into the C++ self-test (`M4SelfTest.cc`) using the `M4_CHECK` macro with condition `false` and a detail string that does **not** start with `"SKIP:"`:

```cpp
// In M4SelfTest::runSelfTest(), after the M4-ADDR-4 check:
M4_CHECK("FAIL-INJECT: deliberate failure injection for CI gate verification",
         false,
         "This is an intentional infrastructure-level FAIL test");
```

This exercises the FAIL path in the `M4_CHECK` macro:

```cpp
// M4_CHECK macro FAIL branch (lines 79-86 of M4SelfTest.cc):
_failed++;
_any_failure = true;
printf("  M4 %s: FAIL", _name);
if (!_d.empty())
    printf(" (%s)", _d.c_str());
printf("\n");
```

---

## Expected C++ Output

With the injection active, the self-test outputs:

```
=== M4 Sentinel Registration Self-Test (node_id=0) ===
  M4 M4-ADDR-1: DSM address recognized: PASS
  M4 M4-ADDR-2: DSM home node correct: PASS
  M4 M4-ADDR-3: LocalPrivate NOT DSM: PASS
  M4 M4-ADDR-4: UbccExclusive NOT DSM: PASS
  M4 FAIL-INJECT: deliberate failure injection for CI gate verification: FAIL (This is an intentional infrastructure-level FAIL test)
  ... (remaining checks as PASS/SKIP) ...
=== M4 Self-Test Results: 8/24 PASS, 1 FAIL, 15 SKIP ===
M4_SELF_TEST_FAILED=1
```

Key markers:
- `M4 FAIL-INJECT: ... : FAIL` — parsed by Python regex as a FAIL match
- `M4_SELF_TEST_FAILED=1` — explicit failure marker
- `_failed > 0` triggers `M4_SELF_TEST_FAILED=1` (line 391-392 of M4SelfTest.cc)

---

## Python Harness Behavior

The Python harness (`test_sentinel_registration.py`) parses the captured output:

1. **FAIL count detection** (lines 146, 150):
   ```python
   fail_matches = re.findall(r'^\s*M4\s+.*:\s*FAIL', captured, re.MULTILINE)
   fail_count = len(fail_matches)  # → 1
   ```

2. **Explicit marker check** (lines 154, 177-186):
   ```python
   explicit_fail = "M4_SELF_TEST_FAILED=1" in captured  # → True
   
   if not explicit_fail and not explicit_pass:
       print("M4_PYTHON: FATAL — neither marker found")
       sys.exit(1)  # guard: no marker at all
   ```

3. **FAILED=1 path** (lines 194-196):
   ```python
   if explicit_fail:
       print("M4_PYTHON: explicit FAIL marker found, treating as fail")
       sys.exit(1)
   ```

Result: `sys.exit(1)` is called — the CI gate correctly blocks the build.

Note: If both `M4_SELF_TEST_PASSED=1` and `M4_SELF_TEST_FAILED=1` appear simultaneously (corrupt output), lines 177-179 also catch this and call `exit(1)`.

---

## Reversion and Regression PASS

After removing the injection line (or commenting it out), the self-test returns to its clean state:

```
=== M4 Sentinel Registration Self-Test (node_id=0) ===
  M4 M4-ADDR-1: DSM address recognized: PASS
  M4 M4-ADDR-2: DSM home node correct: PASS
  M4 M4-ADDR-3: LocalPrivate NOT DSM: PASS
  M4 M4-ADDR-4: UbccExclusive NOT DSM: PASS
  ... (all remaining checks as PASS/SKIP) ...
=== M4 Self-Test Results: 8/23 PASS, 0 FAIL, 15 SKIP ===
M4_SELF_TEST_PASSED=1
```

Python harness output:
```
M4 Self-Test: 8/23 PASS, 0 FAIL, 15 SKIP
M4_PYTHON: marker-based confirmation — PASSED=1 with 0 failures
M4_PYTHON_TEST_HARNESS: DONE — all executed checks passed
```

Exit code: `0` — regression PASS confirmed.

---

## Gate Decision Logic Summary

```
                    C++ self-test completes
                            │
                    fflush(stdout)
                            │
               Python captures fd output
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        FAILED=1 in     PASSED=1 in    neither marker
         captured?       captured?      in captured?
              │             │             │
         exit(1)        fail_count>0   exit(1)
         ◄──────────────      │        ─────────────►
                        ┌─────┴─────┐
                        ▼           ▼
                     exit(1)     exit(0)
                                 (regression PASS)
```

All FAIL paths (explicit marker, parsed FAIL count, missing marker) converge to `exit(1)`. Only the clean path (PASSED=1 + fail_count==0) yields `exit(0)`.

---

## Verification Summary

| Scenario | C++ Output | Python Parsing | Exit Code |
|----------|-----------|----------------|-----------|
| Clean run (no injection) | `M4_SELF_TEST_PASSED=1`, 0 FAIL | `fail_count=0`, explicit_pass=True | **0** (PASS) |
| FAIL injection active | `M4_SELF_TEST_FAILED=1`, 1 FAIL | `fail_count=1`, explicit_fail=True | **1** (FAIL) |
| Corrupt output (both markers) | `PASSED=1` AND `FAILED=1` | both explicit flags true | **1** (FATAL) |
| No marker at all | (output truncated/crashed) | neither marker found | **1** (FATAL) |

The FAIL injection decisively proves that:
1. C++ `M4_CHECK(..., false, ...)` correctly emits a FAIL line.
2. The FAIL triggers `M4_SELF_TEST_FAILED=1` via `_failed > 0` guard.
3. Python harness unconditionally converts `FAILED=1` → `exit(1)`.
4. Removing the injection restores clean PASS.

**Conclusion:** The M4 CI gate (zero-tolerance fail policy) is correctly implemented and verified via injection proof.
