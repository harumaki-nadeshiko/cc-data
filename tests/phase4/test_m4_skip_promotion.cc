/**
 * Standalone test for M4_CHECK ternary scoring + SKIP promotion logic.
 *
 * This mirrors the exact logic from M4SelfTest.cc and verifies:
 *   - TC1: All PASS  — no promotion, correct counts
 *   - TC2: All SKIP  — promotion triggers, SKIP→FAIL converted
 *   - TC3: All FAIL  — no promotion, FAIL count correct
 */

#include <cstdio>
#include <cstdlib>
#include <string>
#include <cstring>

static int _passed = 0;
static int _failed = 0;
static int _skipped = 0;
static int _total = 0;
static bool _any_failure = false;

#define M4_CHECK(_name, _cond, _detail) \
    do { \
        _total++; \
        if (_cond) { \
            _passed++; \
            printf("  M4 %s: PASS\n", _name); \
        } else { \
            std::string _d(_detail); \
            if (_d.rfind("SKIP:", 0) == 0) { \
                _skipped++; \
                printf("  M4 %s: SKIP (%s)\n", _name, _d.c_str() + 5); \
            } else { \
                _failed++; \
                _any_failure = true; \
                printf("  M4 %s: FAIL", _name); \
                if (!_d.empty()) \
                    printf(" (%s)", _d.c_str()); \
                printf("\n"); \
            } \
        } \
    } while(0)

static void promoteRequiredSkipIfAllSkipped(
    const char *group, int skipCount, int totalInGroup)
{
    if (totalInGroup > 0 && skipCount == totalInGroup) {
        _skipped--;
        _failed++;
        _any_failure = true;
        printf("  M4 %s-REQUIRED: PROMOTED SKIP->FAIL "
               "(all %d checks in group skipped, but this is a required "
               "test category)\n",
               group, totalInGroup);
    }
}

static void resetCounters()
{
    _passed = _failed = _skipped = _total = 0;
    _any_failure = false;
}

static int run_tc(const char *tc_name,
                   bool all_pass, bool all_skip, bool all_fail,
                   int expected_pass, int expected_fail, int expected_skip)
{
    resetCounters();

    if (all_pass) {
        // TC: All PASS scenario
        int skipCount = 0, totalCount = 0;
        M4_CHECK("TC-1", true, "");
        totalCount++;
        M4_CHECK("TC-2", true, "");
        totalCount++;
        promoteRequiredSkipIfAllSkipped("TestGroup", skipCount, totalCount);
    } else if (all_skip) {
        // TC: All SKIP scenario
        int skipCount = 0, totalCount = 0;
        M4_CHECK("TC-1", false, "SKIP:precondition not met");
        totalCount++; skipCount++;
        M4_CHECK("TC-2", false, "SKIP:precondition not met");
        totalCount++; skipCount++;
        promoteRequiredSkipIfAllSkipped("TestGroup", skipCount, totalCount);
    } else if (all_fail) {
        // TC: All FAIL scenario
        int skipCount = 0, totalCount = 0;
        M4_CHECK("TC-1", false, "assertion failed");
        totalCount++;
        M4_CHECK("TC-2", false, "assertion failed");
        totalCount++;
        promoteRequiredSkipIfAllSkipped("TestGroup", skipCount, totalCount);
    }

    bool ok = true;
    if (_passed != expected_pass) {
        printf("FAIL %s: _passed=%d expected=%d\n", tc_name, _passed, expected_pass);
        ok = false;
    }
    if (_failed != expected_fail) {
        printf("FAIL %s: _failed=%d expected=%d\n", tc_name, _failed, expected_fail);
        ok = false;
    }
    if (_skipped != expected_skip) {
        printf("FAIL %s: _skipped=%d expected=%d\n", tc_name, _skipped, expected_skip);
        ok = false;
    }
    if (_total != expected_pass + expected_fail + expected_skip) {
        printf("FAIL %s: _total=%d expected=%d\n", tc_name, _total,
               expected_pass + expected_fail + expected_skip);
        ok = false;
    }
    if (ok) {
        printf("PASS %s: (%dP/%dF/%dS)\n", tc_name, _passed, _failed, _skipped);
    }
    return ok ? 0 : 1;
}

static int run_tc_mixed()
{
    resetCounters();
    int fail_count = 0;

    // TC-mixed: Pass + Fail (no skips) — no promotion
    {
        int skipCount = 0, totalCount = 0;
        M4_CHECK("TC-Mix-1", true, "");
        totalCount++;
        M4_CHECK("TC-Mix-2", false, "real failure");
        totalCount++;
        promoteRequiredSkipIfAllSkipped("TestMixed", skipCount, totalCount);

        if (_passed != 1 || _failed != 1 || _skipped != 0) {
            printf("FAIL TC-mixed: got (%dP/%dF/%dS) expected (1P/1F/0S)\n",
                   _passed, _failed, _skipped);
            fail_count++;
        } else {
            printf("PASS TC-mixed: (%dP/%dF/%dS)\n", _passed, _failed, _skipped);
        }
    }

    return fail_count;
}

static int run_tc_partial_skip()
{
    resetCounters();
    int fail_count = 0;

    // TC-partial-skip: Some PASS, some SKIP — no promotion (not all SKIP)
    {
        int skipCount = 0, totalCount = 0;
        M4_CHECK("TC-Part-1", true, "");
        totalCount++;
        M4_CHECK("TC-Part-2", false, "SKIP:not available");
        totalCount++; skipCount++;
        promoteRequiredSkipIfAllSkipped("TestPartial", skipCount, totalCount);

        if (_passed != 1 || _failed != 0 || _skipped != 1) {
            printf("FAIL TC-partial-skip: got (%dP/%dF/%dS) expected (1P/0F/1S)\n",
                   _passed, _failed, _skipped);
            fail_count++;
        } else {
            printf("PASS TC-partial-skip: (%dP/%dF/%dS)\n",
                   _passed, _failed, _skipped);
        }
    }

    return fail_count;
}

int main()
{
    printf("=== M4 Ternary Scoring + SKIP Promotion Unit Tests ===\n\n");

    int failures = 0;

    // TC1: All PASS — no promotion
    failures += run_tc("TC-1 (all-PASS)", true, false, false, 2, 0, 0);

    // TC2: All SKIP — promotion triggers: 1st SKIP→FAIL, rest remain SKIP
    failures += run_tc("TC-2 (all-SKIP)", false, true, false, 0, 1, 1);

    // TC3: All FAIL — no promotion
    failures += run_tc("TC-3 (all-FAIL)", false, false, true, 0, 2, 0);

    // TC4: Mixed PASS+FAIL — no promotion
    failures += run_tc_mixed();

    // TC5: Partial skip — no promotion
    failures += run_tc_partial_skip();

    printf("\n=== Results: %d test(s) failed ===\n", failures);
    return failures > 0 ? 1 : 0;
}
