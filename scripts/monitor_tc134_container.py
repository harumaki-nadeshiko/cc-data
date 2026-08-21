#!/usr/bin/env python3
"""Poll a detached TC134 Docker run and stop only on evidenced stalls."""

import argparse
import datetime
import re
import subprocess
import time


WRITER_RE = re.compile(r"p(\d+)=(\d+)/8192")
SUMMARY_RE = re.compile(
    r"SUMMARY build=(\d+) tuple=(\d+) unknown_clear=(\d+) stale_eviction=(\d+)")


def run(argv):
    result = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    return result.returncode, result.stdout.strip()


def timestamp():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def container_state(name):
    rc, text = run([
        "docker", "inspect", "--format",
        "{{.State.Running}} {{.State.Status}} {{.State.ExitCode}}", name,
    ])
    return None if rc else text


def diagnostic(name, log_root, simout_root):
    return run([
        "docker", "exec", name, "python3",
        "scripts/diagnose_tc134_timeout.py", log_root,
        "--simout-dir", simout_root, "--compact",
    ])[1]


def key_state(name, log_root):
    return run([
        "docker", "exec", name, "python3",
        "scripts/extract_ubcc_key_state.py", log_root, "--compact",
    ])[1]


def writers(text):
    line = next((line for line in text.splitlines()
                 if line.startswith("WRITERS ")), "")
    return tuple((int(plane), int(done))
                 for plane, done in WRITER_RE.findall(line))


def hard_fault(key_text, diag_text):
    match = SUMMARY_RE.search(key_text)
    if match and (int(match.group(2)) > 0 or int(match.group(3)) > 0):
        return True
    hard_tokens = (
        "CLEAR_REJECTION_LOOP", "UNKNOWN_CLEAR", "TUPLE_MISMATCH",
        "panic", "fatal", "EXHAUSTED_NO_RESPONSE",
    )
    combined = key_text + "\n" + diag_text
    return any(token in combined for token in hard_tokens)


def resident_waiter_storm(container, log_root):
    rc, text = run([
        "docker", "exec", container, "python3", "-c",
        "from pathlib import Path; import re,collections,sys; "
        "root=Path(sys.argv[1]); c=collections.Counter(); "
        "files=list(root.glob('gem5_tc134_node*/stderr.log')); "
        "[(c.update(re.findall(r'PENDING-READ-HIT.*?reqId=(\\d+).*?retry=(\\d+)', "
        "p.read_bytes()[-262144:].decode(errors='replace')))) for p in files]; "
        "mx=max((int(retry) for req,retry in c), default=0); "
        "print(mx)",
        log_root,
    ])
    if rc:
        return 0
    try:
        return int(text.splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--log-root", required=True)
    parser.add_argument("--simout-root", required=True)
    parser.add_argument("--interval", type=int, default=600)
    parser.add_argument("--hard-stall-samples", type=int, default=3)
    parser.add_argument("--asymmetric-stall-samples", type=int, default=5)
    args = parser.parse_args()

    previous = None
    unchanged = 0
    while True:
        state = container_state(args.container)
        if state is None:
            print(f"{timestamp()} MONITOR container_missing", flush=True)
            return 2
        if not state.startswith("true "):
            print(f"{timestamp()} MONITOR finished state={state}", flush=True)
            return 0

        diag = diagnostic(args.container, args.log_root, args.simout_root)
        keys = key_state(args.container, args.log_root)
        progress = writers(diag)
        unchanged = unchanged + 1 if progress == previous else 0
        previous = progress
        status = next((line for line in diag.splitlines()
                       if line.startswith("STATUS ")), "STATUS unavailable")
        summary = next((line for line in keys.splitlines()
                        if line.startswith("SUMMARY ")), "SUMMARY unavailable")
        retry_max = resident_waiter_storm(args.container, args.log_root)
        print(f"{timestamp()} {status} unchanged={unchanged} writers={progress}",
              flush=True)
        print(f"{timestamp()} {summary} max_pending_read_retry={retry_max}", flush=True)

        stop_reason = None
        if unchanged >= args.hard_stall_samples and hard_fault(keys, diag):
            stop_reason = "hard_protocol_fault_with_no_progress"
        if (unchanged >= args.asymmetric_stall_samples and
                "ONE_WRITER_BLOCKS_GLOBAL_POST_SHARE_BARRIER" in status):
            stop_reason = "asymmetric_writer_stall"
        if unchanged >= args.hard_stall_samples and retry_max >= 65536:
            stop_reason = "fixed_reqid_retry_storm"
        if stop_reason:
            print(f"{timestamp()} EARLY_EXIT reason={stop_reason}", flush=True)
            print(diag, flush=True)
            print(keys, flush=True)
            run(["docker", "stop", "--time", "30", args.container])
            return 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
