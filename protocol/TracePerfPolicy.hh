/**
 * TracePerfPolicy — deterministic, bounded TRACE-PERF emission policy.
 *
 * Semantics:
 *  - Full:   emit every event. EP_TRACE_PERF=full
 *  - Sample: emit firstN always, then every Kth (0=stop), capped at maxRecords.
 *            Default: firstN=500, everyK=0, maxRecords=2000.
 *            EP_TRACE_PERF=sample  (or unset = default=sample)
 *  - Off:    emit nothing.  EP_TRACE_PERF=off
 *
 * Deterministic only: first-N counting plus positional K (no random).
 * Hard per-process cap (maxRecords) applies in Sample mode. Full mode is
 * intentionally unbounded unless EP_TRACE_PERF_MAX is explicitly supplied.
 *
 * Every process (gem5/UBAdapter, ubio, networksim) includes this header,
 * initialises at start and prints [TRACE-PERF-SUMMARY] at termination.
 *
 * Env vars (read once at first init call):
 *   EP_TRACE_PERF=full|sample|off         (default sample)
 *   EP_TRACE_PERF_MAX=<N>                 (default 2000)
 *   EP_TRACE_PERF_EVERY=<K>               (default 0 = after-firstN disabled)
 *   EP_TRACE_PERF_FIRST_N=<N>             (default 500)
 */

#ifndef PROTOCOL_TRACE_PERF_POLICY_HH
#define PROTOCOL_TRACE_PERF_POLICY_HH

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>

class TracePerfPolicy {
public:
    enum class Mode { Full, Sample, Off };

    static TracePerfPolicy& get() {
        static TracePerfPolicy inst;
        return inst;
    }

    /** Call once at process startup for each component. Reads env vars. */
    void init() {
        bool expected = false;
        if (!_initialised.compare_exchange_strong(expected, true))
            return;  // already done (gem5 may have many UBAdapters)

        const char* modeStr = std::getenv("EP_TRACE_PERF");
        if (modeStr) {
            if (std::strcmp(modeStr, "full") == 0)
                _mode = Mode::Full;
            else if (std::strcmp(modeStr, "sample") == 0)
                _mode = Mode::Sample;
            else if (std::strcmp(modeStr, "off") == 0)
                _mode = Mode::Off;
            // unknown values stay at default (Sample)
        }

        const char* maxStr = std::getenv("EP_TRACE_PERF_MAX");
        if (maxStr) {
            _maxRecords = static_cast<uint64_t>(std::atol(maxStr));
            _maxExplicit = true;
        }

        const char* everyStr = std::getenv("EP_TRACE_PERF_EVERY");
        if (everyStr) _everyK = static_cast<uint64_t>(std::atol(everyStr));

        const char* firstStr = std::getenv("EP_TRACE_PERF_FIRST_N");
        if (firstStr) _firstN = static_cast<uint64_t>(std::atol(firstStr));

        // Register atexit handler once per process – prints summary at exit.
        // guarded by the CAS above so only the first caller wires it.
        std::atexit(+[]() {
            TracePerfPolicy::get().printSummary();
        });
    }

    /**
     * @return true if caller should emit a TRACE-PERF line now.
     * @param component  short name, e.g. "gem5", "ubio", "nsim".
     *                   Must be stable string literal or long-lived c-string.
     */
    bool shouldEmit(const char* component) {
        init();
        if (_mode == Mode::Off) {
            _recordSuppressed(component);
            return false;
        }

        if (_mode == Mode::Full) {
            // Preserve historical full tracing unless the caller explicitly
            // requests a finite cap for a controlled diagnostic run.
            uint64_t emitted = _totalEmitted.load(std::memory_order_relaxed);
            if (_maxExplicit && emitted >= _maxRecords) {
                _recordSuppressed(component);
                return false;
            }
            _recordEmitted(component);
            return true;
        }

        // ── Sample mode ────────────────────────────────────────────
        uint64_t seq = _totalConsidered.fetch_add(1, std::memory_order_relaxed);
        uint64_t emitted = _totalEmitted.load(std::memory_order_relaxed);

        // Hard cap – if we already hit max, suppress everything else.
        if (emitted >= _maxRecords) {
            _recordSuppressed(component);
            return false;
        }

        bool emit = false;
        if (seq < _firstN) {
            // First N events always go through.
            emit = true;
        } else if (_everyK > 0 && ((seq - _firstN) % _everyK) == 0) {
            // After first N, emit every Kth.
            emit = true;
        }
        // else (_everyK == 0): stop after first N.

        if (emit) {
            _recordEmitted(component);
        } else {
            _recordSuppressed(component);
        }
        return emit;
    }

    void printSummary() const {
        bool expected = false;
        if (!_summaryPrinted.compare_exchange_strong(expected, true))
            return;

        uint64_t totalEmitted = 0, totalSuppressed = 0;
        for (const auto& kv : _components) {
            totalEmitted   += kv.second.emitted.load(std::memory_order_relaxed);
            totalSuppressed += kv.second.suppressed.load(std::memory_order_relaxed);
        }

        const char* modeName =
            _mode == Mode::Full   ? "full" :
            _mode == Mode::Off    ? "off"  : "sample";

        std::fprintf(stderr,
            "[TRACE-PERF-SUMMARY] policy=mode=%s firstN=%lu everyK=%lu max=%lu"
            " total_emitted=%lu total_suppressed=%lu\n",
            modeName, _firstN, _everyK, _maxRecords,
            totalEmitted, totalSuppressed);

        for (const auto& kv : _components) {
            uint64_t e = kv.second.emitted.load(std::memory_order_relaxed);
            uint64_t s = kv.second.suppressed.load(std::memory_order_relaxed);
            if (e > 0 || s > 0) {
                std::fprintf(stderr,
                    "[TRACE-PERF-SUMMARY] component=%s emitted=%lu suppressed=%lu\n",
                    kv.first.c_str(), e, s);
            }
        }
    }

    // Read-only queries for manifest / supervisor reporting.
    Mode mode()             const { return _mode; }
    uint64_t maxRecords()   const { return _maxRecords; }
    uint64_t everyK()       const { return _everyK; }
    uint64_t firstN()       const { return _firstN; }
    uint64_t totalEmitted() const { return _totalEmitted.load(std::memory_order_relaxed); }
    uint64_t totalSuppressed() const {
        uint64_t s = 0;
        for (const auto& kv : _components)
            s += kv.second.suppressed.load(std::memory_order_relaxed);
        return s;
    }

private:
    TracePerfPolicy() = default;

    void _recordEmitted(const char* component) {
        _totalEmitted.fetch_add(1, std::memory_order_relaxed);
        _components[component].emitted.fetch_add(1, std::memory_order_relaxed);
    }

    void _recordSuppressed(const char* component) {
        _components[component].suppressed.fetch_add(1, std::memory_order_relaxed);
    }

    std::atomic<bool> _initialised{false};
    mutable std::atomic<bool> _summaryPrinted{false};

    Mode     _mode        = Mode::Sample;
    uint64_t _maxRecords  = 2000;
    bool     _maxExplicit = false;
    uint64_t _everyK      = 0;
    uint64_t _firstN      = 500;

    std::atomic<uint64_t> _totalEmitted{0};
    std::atomic<uint64_t> _totalConsidered{0};

    struct Counter {
        std::atomic<uint64_t> emitted{0};
        std::atomic<uint64_t> suppressed{0};
    };
    mutable std::map<std::string, Counter> _components;
};

#endif // PROTOCOL_TRACE_PERF_POLICY_HH
