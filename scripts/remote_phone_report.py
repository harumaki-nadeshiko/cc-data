#!/usr/bin/env python3
"""Print a compact, phone-friendly report of a remote native environment.

The normal output is exactly three ASCII-only lines.  This script deliberately
does not require Docker: it is intended to be copied to and run on the remote
machine whose native runtime will execute the project binaries.
"""

import argparse
import json
import os
import platform
import re
import sys
from pathlib import Path

import collect_runtime_fingerprint as fingerprint


HASH_LENGTH = 12


def named_path(value):
    """Parse NAME=PATH, also accepting PATH and deriving NAME from basename."""
    name, separator, path = value.partition("=")
    if not separator:
        path = value
        name = os.path.basename(os.path.normpath(path)) or "path"
    if not name or not path:
        raise argparse.ArgumentTypeError("expected NAME=PATH (or PATH)")
    return name, path


def ascii_token(value):
    """Make arbitrary host text safe to transcribe as one whitespace-free token."""
    text = str(value) if value not in (None, "") else "unknown"
    text = re.sub(r"\s+", "_", text)
    text = "".join(char if 33 <= ord(char) <= 126 else "?" for char in text)
    # These characters delimit fields and comparisons in the compact format.
    return text.translate(str.maketrans({";": ",", "[": "(", "]": ")"}))


def compiler_label(info):
    if not info:
        return "missing"
    version = info.get("version") or ""
    path = info.get("path") or ""
    lower = (path + " " + version).lower()
    family = "clang" if "clang" in lower else "gcc" if ("gcc" in lower or "g++" in lower) else "cc"
    matches = re.findall(r"(?<![0-9])([0-9]+(?:\.[0-9]+){1,3})(?![0-9])", version)
    return "%s-%s" % (family, matches[-1]) if matches else ascii_token(version.splitlines()[0] if version else path)


def baseline_compiler(baseline):
    tools = baseline.get("runtime", {}).get("tools", {})
    for name in ("cc", "gcc", "clang"):
        if tools.get(name):
            return compiler_label(tools[name])
    compiler = baseline.get("runtime", {}).get("python", {}).get("compiler")
    if compiler:
        return compiler_label({"version": compiler})
    return None


def baseline_environment(baseline):
    host = baseline.get("host", {})
    runtime = baseline.get("runtime", {})
    python = runtime.get("python", {})
    libc = host.get("libc", {})
    uname = host.get("uname", {})
    zmq = runtime.get("zeromq", {})
    libc_value = "-".join(value for value in (libc.get("name"), libc.get("version")) if value)
    return {
        "arch": host.get("machine"),
        "kernel": uname.get("release"),
        "libc": libc_value or None,
        "python": python.get("version"),
        "compiler": baseline_compiler(baseline),
        "libzmq": zmq.get("version"),
    }


def environment(libzmq):
    libc_name, libc_version = platform.libc_ver()
    compiler = fingerprint.command_version("cc")
    zmq = fingerprint.zmq_version(libzmq)
    libc_value = "-".join(value for value in (libc_name, libc_version) if value)
    return {
        "arch": platform.machine() or "unknown",
        "kernel": platform.release() or "unknown",
        "libc": libc_value or "unknown",
        "python": platform.python_version(),
        "compiler": compiler_label(compiler),
        "libzmq": zmq.get("version") or "missing",
    }, {"compiler": compiler, "zeromq": zmq}


def baseline_path_index(baseline):
    result = {}
    for kind in ("binaries", "artifacts"):
        for item in baseline.get(kind, []):
            path = item.get("path") or ""
            digest = item.get("sha256")
            if digest:
                result[(kind, path)] = digest
                result.setdefault((kind, os.path.basename(path)), digest)
    return result


def find_baseline_hash(index, kind, name, path):
    for key in (path, os.path.abspath(path), name, os.path.basename(path)):
        if (kind, key) in index:
            return index[(kind, key)]
    return None


def collect_paths(binaries, artifacts, baseline):
    index = baseline_path_index(baseline) if baseline else {}
    hashes = []
    ldd = []
    seen = set()
    for kind, values in (("binaries", binaries), ("artifacts", artifacts)):
        for name, path in values:
            if name in seen:
                raise ValueError("duplicate path name: %s" % name)
            seen.add(name)
            if kind == "binaries":
                item = fingerprint.binary_info(path)
            else:
                absolute = os.path.abspath(path)
                exists = os.path.isfile(absolute)
                item = {"path": absolute, "exists": exists,
                        "sha256": fingerprint.sha256(absolute) if exists else None}
            expected = find_baseline_hash(index, kind, name, path) if baseline else None
            digest = item.get("sha256")
            hashes.append({
                "name": name, "path": item["path"], "exists": item.get("exists", False),
                "sha256": digest, "baseline_sha256": expected,
                "baseline_status": ("same" if digest == expected else "different") if expected else None,
            })
            if kind == "binaries":
                ldd_info = item.get("ldd")
                if not item.get("exists"):
                    status, missing = "binary-missing", []
                elif not ldd_info or not ldd_info.get("available"):
                    status, missing = "unavailable", []
                elif ldd_info.get("error") and not ldd_info.get("dependencies"):
                    status, missing = "not-dynamic", []
                else:
                    status = "ok"
                    missing = ldd_info.get("missing", [])
                ldd.append({"name": name, "status": status,
                            "missing_count": len(missing), "missing": missing})
    return hashes, ldd


def compared(value, baseline_value, has_baseline):
    token = ascii_token(value)
    if not has_baseline or baseline_value is None:
        return token
    if str(value) == str(baseline_value):
        return token + "[=base]"
    return token + "[base=%s]" % ascii_token(baseline_value)


def text_lines(report):
    baseline = report.get("baseline_environment", {})
    has_baseline = report.get("baseline") is not None
    env = report["environment"]
    env_line = "PHONEENV " + " ".join(
        "%s=%s" % (key, compared(env[key], baseline.get(key), has_baseline))
        for key in ("arch", "kernel", "libc", "python", "compiler", "libzmq")
    )
    hash_fields = []
    for item in report["paths"]:
        if not item["exists"]:
            value = "MISSING"
        else:
            value = item["sha256"][:HASH_LENGTH]
            if item["baseline_status"] == "same":
                value += "[=base]"
            elif item["baseline_status"] == "different":
                value += "[base=%s]" % item["baseline_sha256"][:HASH_LENGTH]
        hash_fields.append("%s=%s" % (ascii_token(item["name"]), value))
    hash_line = "PHONEHASH " + (" ".join(hash_fields) if hash_fields else "none")
    ldd_fields = []
    for item in report["ldd"]:
        if item["status"] != "ok":
            value = item["status"].upper()
        elif item["missing"]:
            value = "%d:%s" % (item["missing_count"], ",".join(ascii_token(x) for x in item["missing"]))
        else:
            value = "0"
        ldd_fields.append("%s=%s" % (ascii_token(item["name"]), value))
    ldd_line = "PHONELDD " + (" ".join(ldd_fields) if ldd_fields else "none")
    return [env_line, hash_line, ldd_line]


def load_baseline(path):
    if not path:
        return None
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libzmq", help="explicit native libzmq shared-library path")
    parser.add_argument("--binary", action="append", default=[], type=named_path,
                        metavar="NAME=PATH", help="hash and inspect a binary (repeatable)")
    parser.add_argument("--artifact", action="append", default=[], type=named_path,
                        metavar="NAME=PATH", help="hash a file (repeatable)")
    parser.add_argument("--baseline", metavar="FILE.json",
                        help="optional collect_runtime_fingerprint.py baseline")
    parser.add_argument("--json", action="store_true", help="emit one-line JSON instead")
    args = parser.parse_args(argv)
    try:
        baseline = load_baseline(args.baseline)
        env, details = environment(args.libzmq)
        paths, ldd = collect_paths(args.binary, args.artifact, baseline)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("remote_phone_report: %s" % exc, file=sys.stderr)
        return 2
    report = {
        "environment": env,
        "environment_details": details,
        "baseline": os.path.abspath(args.baseline) if args.baseline else None,
        "baseline_environment": baseline_environment(baseline) if baseline else {},
        "paths": paths,
        "ldd": ldd,
    }
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
    else:
        for line in text_lines(report):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
