# Textual Two-Column Demo

`textual_two_column_demo.py` is a small live TUI intended as an editable
starting point:

- The left column is a wrapping, auto-scrolling `RichLog`.
- The right column is a selectable `ListView` whose rows are replaced in real
  time.
- Press `c` to clear the log and `q` to quit.

Run with an installed Textual package:

```bash
python3 tools/textual_two_column_demo.py
```

For an automated smoke run:

```bash
python3 tools/textual_two_column_demo.py --demo-seconds 2
```

The example feed is deliberately kept at the top of the Python file. Replace
`SAMPLE_LOG_MESSAGES`, `SAMPLE_ROWS`, or `_update_demo()` with queue, socket,
file-tail, or application callback data. External code may also call
`apply_update(log_message, rows)` from Textual's message loop.

## Offline Wheel

The adjacent `textual-8.2.8-py3-none-any.whl` was resolved with pip's
`manylinux2014_aarch64` target. Textual is pure Python, so PyPI publishes a
platform-independent `py3-none-any` wheel rather than a separately tagged
AArch64 wheel. The file is usable on AArch64 Linux with a compatible Python.

```text
SHA256 267375fd402dc8d981457212efa71f0e3365fd17bba144ba9bb3ed7563cb374a
```

Install it with:

```bash
python3 -m pip install tools/textual-8.2.8-py3-none-any.whl
```

Textual has runtime dependencies. If the target is fully offline, mirror its
dependency wheels as well or install them from the target system's package
set.
