#!/usr/bin/env python3
"""Collect and compare deterministic runtime/environment fingerprints.

The JSON intentionally contains no timestamp.  A fingerprint made twice in an
unchanged checkout and environment can therefore be compared byte-for-byte.
"""

import argparse
import ctypes
import ctypes.util
import fnmatch
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path


SCHEMA_VERSION = 1
ENV_NAMES = (
    "CC", "CXX", "CFLAGS", "CPPFLAGS", "CXXFLAGS", "LDFLAGS",
    "LD_LIBRARY_PATH", "LIBRARY_PATH", "CPATH", "C_INCLUDE_PATH",
    "CPLUS_INCLUDE_PATH", "PKG_CONFIG_PATH", "PYTHONPATH", "VIRTUAL_ENV",
    "CONDA_PREFIX", "ZMQ_PREFIX", "LIBZMQ_PATH",
)
ENV_PREFIXES = (
    "UBCC_", "UBIO_", "GEM5_", "EP_", "E2E_", "WORKLOAD_",
    "HA_", "OURCC_", "NSIM_",
)
DEFAULT_HOST_ONLY = (
    "label", "host.machine", "host.uname", "host.cpu", "host.os_release",
    "environment", "git", "runtime.python.executable",
    "runtime.tools.*.path", "runtime.zeromq.library",
    "binaries.*.path", "binaries.*.ldd.dependencies",
    "artifacts.*.path",
)


def run(argv, cwd=None):
    """Return (rc, normalized stdout); never raise for missing commands."""
    try:
        proc = subprocess.run(
            argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", check=False,
        )
        return proc.returncode, proc.stdout.replace("\r\n", "\n").strip()
    except (OSError, ValueError) as exc:
        return 127, "%s: %s" % (type(exc).__name__, exc)


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_key_values(text):
    result = {}
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key.strip()] = value
    return dict(sorted(result.items()))


def cpu_info():
    text = read_text("/proc/cpuinfo")
    model = None
    for key in ("model name", "Hardware", "Processor", "cpu model"):
        match = re.search(r"^%s\s*:\s*(.+)$" % re.escape(key), text,
                          flags=re.MULTILINE | re.IGNORECASE)
        if match:
            model = match.group(1).strip()
            break
    return {"count": os.cpu_count(), "model": model}


def command_version(name):
    path = shutil.which(name)
    if not path:
        return None
    # GNU ld accepts --version; compiler drivers and clang do too.
    rc, output = run([path, "--version"])
    first = output.splitlines()[0] if output else None
    return {"path": os.path.realpath(path), "version": first, "exit_code": rc}


def zmq_version(libzmq=None):
    candidate = libzmq or ctypes.util.find_library("zmq")
    result = {"library": candidate, "version": None}
    if not candidate:
        result["error"] = "libzmq not found"
        return result
    try:
        library = ctypes.CDLL(candidate)
        function = library.zmq_version
        function.argtypes = [ctypes.POINTER(ctypes.c_int)] * 3
        function.restype = None
        major, minor, patch = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        function(ctypes.byref(major), ctypes.byref(minor), ctypes.byref(patch))
        result["version"] = "%d.%d.%d" % (major.value, minor.value, patch.value)
        result["library"] = os.path.realpath(candidate) if os.path.exists(candidate) else candidate
    except (OSError, AttributeError) as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
    return result


def git_info(repo):
    repo = os.path.abspath(repo)
    rc, top = run(["git", "rev-parse", "--show-toplevel"], cwd=repo)
    if rc:
        return {"available": False, "error": top or "not a git repository"}
    rc, head = run(["git", "rev-parse", "HEAD"], cwd=top)
    status_rc, status = run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"], cwd=top)
    sub_rc, sub_text = run(["git", "submodule", "status", "--recursive"], cwd=top)
    if sub_rc != 0:
        sub_rc, sub_text = run(["git", "submodule", "status"], cwd=top)
    submodules = []
    if sub_rc == 0:
        for line in sub_text.splitlines():
            match = re.match(r"^(.)([0-9a-fA-F]+)\s+(\S+)(?:\s+.*)?$", line)
            if match:
                state, commit, path = match.groups()
                submodules.append({"path": path, "head": commit.lower(), "state": state})
    return {
        "available": rc == 0,
        "head": head if rc == 0 else None,
        "dirty": bool(status) if status_rc == 0 else None,
        "submodules": sorted(submodules, key=lambda item: item["path"]),
    }


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_ldd(output):
    dependencies = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "=>" in line:
            name, target = (part.strip() for part in line.split("=>", 1))
            target = target.split(" (", 1)[0].strip()
            dependencies.append({"name": name,
                                 "resolved": None if target == "not found" else target,
                                 "missing": target == "not found"})
        else:
            token = line.split(" (", 1)[0].strip()
            # Loader and static/non-dynamic diagnostics have no => form.
            dependencies.append({"name": token,
                                 "resolved": token if token.startswith("/") else None,
                                 "missing": False})
    return sorted(dependencies, key=lambda item: (item["name"], item["resolved"] or ""))


def binary_info(path):
    absolute = os.path.abspath(path)
    item = {"path": absolute, "exists": os.path.isfile(absolute)}
    if not item["exists"]:
        item["error"] = "not a regular file"
        return item
    item["sha256"] = sha256(absolute)
    ldd = shutil.which("ldd")
    if not ldd:
        item["ldd"] = {"available": False, "dependencies": []}
    else:
        rc, output = run([ldd, absolute])
        item["ldd"] = {
            "available": True, "exit_code": rc,
            "dependencies": parse_ldd(output),
        }
        item["ldd"]["missing"] = sorted(
            dependency["name"] for dependency in item["ldd"]["dependencies"]
            if dependency["missing"]
        )
        if rc and not item["ldd"]["dependencies"]:
            item["ldd"]["error"] = output
    return item


def collect(args):
    uname = platform.uname()
    libc_name, libc_version = platform.libc_ver()
    tools = {}
    for name in ("cc", "c++", "gcc", "g++", "clang", "clang++", "ld"):
        value = command_version(name)
        if value is not None:
            tools[name] = value
    environment = {
        key: value for key, value in os.environ.items()
        if key in ENV_NAMES or key.startswith(ENV_PREFIXES)
    }
    implementation = platform.python_implementation()
    git = git_info(args.repo)
    if args.git_head:
        git = {
            "available": True,
            "head": args.git_head,
            "dirty": args.git_dirty == "true" if args.git_dirty else None,
            "submodules": [],
        }
        for item in args.submodule:
            path, separator, head = item.partition("=")
            if not separator or not path or not head:
                raise ValueError("--submodule must be PATH=HEAD")
            git["submodules"].append({"path": path, "head": head, "state": " "})
        git["submodules"].sort(key=lambda item: item["path"])
    fingerprint = {
        "schema_version": SCHEMA_VERSION,
        "label": args.label,
        "host": {
            "machine": platform.machine(),
            "uname": {
                "system": uname.system, "node": uname.node, "release": uname.release,
                "version": uname.version, "machine": uname.machine,
            },
            "os_release": parse_key_values(read_text("/etc/os-release")),
            "cpu": cpu_info(),
            "libc": {"name": libc_name or None, "version": libc_version or None},
        },
        "runtime": {
            "container_image_id": args.container_image_id,
            "python": {
                "implementation": implementation,
                "version": platform.python_version(),
                "compiler": platform.python_compiler(),
                "executable": os.path.realpath(sys.executable),
            },
            "tools": dict(sorted(tools.items())),
            "zeromq": zmq_version(args.libzmq),
        },
        "environment": dict(sorted(environment.items())),
        "git": git,
        "binaries": sorted((binary_info(path) for path in args.binary), key=lambda item: item["path"]),
        "artifacts": sorted(
            ({"path": os.path.abspath(path), "exists": os.path.isfile(path),
              "sha256": sha256(path) if os.path.isfile(path) else None}
             for path in args.artifact),
            key=lambda item: item["path"]),
    }
    return fingerprint


def flatten(value, prefix=""):
    result = {}
    if isinstance(value, dict):
        for key in sorted(value):
            child = "%s.%s" % (prefix, key) if prefix else key
            result.update(flatten(value[key], child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            # Collectors sort every list before comparison. Dot-separated
            # indexes also make patterns such as binaries.*.path intuitive.
            result.update(flatten(item, "%s.%d" % (prefix, index)))
        if not value:
            result[prefix] = []
    else:
        result[prefix] = value
    return result


def matches(path, patterns):
    return any(path == pattern or path.startswith(pattern + ".") or
               path.startswith(pattern + "[") or fnmatch.fnmatchcase(path, pattern)
               for pattern in patterns)


def compare(baseline, current, ignored=(), strict_host=False):
    old, new = flatten(baseline), flatten(current)
    patterns = tuple(ignored) + (() if strict_host else DEFAULT_HOST_ONLY)
    differences = []
    for path in sorted(set(old) | set(new)):
        if old.get(path) == new.get(path) and path in old and path in new:
            continue
        classification = "ignored" if matches(path, patterns) else "required"
        differences.append({
            "field": path, "baseline": old.get(path, "<missing>"),
            "current": new.get(path, "<missing>"), "classification": classification,
        })
    return differences


def compact(value):
    text = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return text if len(text) <= 100 else text[:97] + "..."


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", help="human-readable source label")
    parser.add_argument("--libzmq", help="explicit libzmq shared-library path")
    parser.add_argument("--container-image-id",
                        help="container image ID/digest supplied by the orchestrator")
    parser.add_argument("--binary", action="append", default=[], metavar="PATH",
                        help="binary to hash and inspect with ldd (repeatable)")
    parser.add_argument("--artifact", action="append", default=[], metavar="PATH",
                        help="file to hash without ldd inspection (repeatable)")
    parser.add_argument("--repo", default=".", help="git checkout to inspect (default: cwd)")
    parser.add_argument("--git-head", help="orchestrator-supplied Git HEAD")
    parser.add_argument("--git-dirty", choices=("true", "false"),
                        help="tracked dirty state used with --git-head")
    parser.add_argument("--submodule", action="append", default=[], metavar="PATH=HEAD",
                        help="orchestrator-supplied submodule revision")
    parser.add_argument("--compare", metavar="BASELINE.json")
    parser.add_argument("--input", metavar="CURRENT.json",
                        help="compare an already collected fingerprint instead of recollecting")
    parser.add_argument("--ignore-field", action="append", default=[], metavar="GLOB",
                        help="ignore dotted field/glob during comparison (repeatable)")
    parser.add_argument("--strict-host", action="store_true",
                        help="make normally informational host fields required")
    args = parser.parse_args(argv)
    if args.input:
        try:
            with open(args.input, encoding="utf-8") as stream:
                current = json.load(stream)
        except (OSError, ValueError) as exc:
            print("cannot read current fingerprint: %s" % exc, file=sys.stderr)
            return 2
    else:
        current = collect(args)
    if not args.compare:
        json.dump(current, sys.stdout, sort_keys=True, indent=2, ensure_ascii=True)
        sys.stdout.write("\n")
        return 0
    try:
        with open(args.compare, encoding="utf-8") as stream:
            baseline = json.load(stream)
    except (OSError, ValueError) as exc:
        print("cannot read baseline: %s" % exc, file=sys.stderr)
        return 2
    differences = compare(baseline, current, args.ignore_field, args.strict_host)
    for difference in differences:
        print("%s %-8s %s: %s -> %s" % (
            "!" if difference["classification"] == "required" else "~",
            difference["classification"], difference["field"],
            compact(difference["baseline"]), compact(difference["current"]),
        ))
    required = sum(item["classification"] == "required" for item in differences)
    ignored = len(differences) - required
    print("summary: %d required mismatch(es), %d informational/ignored" % (required, ignored))
    return 1 if required else 0


if __name__ == "__main__":
    sys.exit(main())
