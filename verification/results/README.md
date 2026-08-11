# Formal Verification Raw Results

This directory stores durable TLC stdout/stderr for acceptance evidence.

Each run should record:

- model and cfg names;
- `TLC_WORKERS` and timeout;
- command and return code;
- source/model/tool hashes;
- whether a violation was expected or unexpected.

Files named `*_expected_violation.log` are negative-model evidence and must not
be reported as a passing invariant check.

`formal_run_manifest_20260807.tsv` is the authoritative index for model, cfg,
log hashes, resource settings, TLC return codes, state counts, and result type.
