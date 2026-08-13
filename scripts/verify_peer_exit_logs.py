#!/usr/bin/env python3
"""Verify that every UBIO plane completed the PeerExit handshake."""
import argparse
import pathlib
import re
import sys


START_RE = re.compile(
    r"\[PEER-EXIT-START\] local=(\d+):(\d+) exitId=(\d+).*"
    r"required=(\d+) seenNotify=(\d+)")
QUIESCE_RE = re.compile(
    r"\[PEER-EXIT-QUIESCE\] local=(\d+):(\d+) exitId=(\d+) acked=(\d+)/(\d+)")
CLOSE_RE = re.compile(
    r"\[PEER-EXIT-CLOSE\] local=(\d+):(\d+) exitId=(\d+)")
NOTIFY_RECV_RE = re.compile(
    r"\[PEER-EXIT-NOTIFY-RECV\] local=(\d+):(\d+) peer=(\d+):(\d+) "
    r"exitId=(\d+)")
ACK_RECV_RE = re.compile(
    r"\[PEER-EXIT-ACK-RECV\] local=(\d+):(\d+) peer=(\d+):(\d+) "
    r"exitId=(\d+)")
PATH_RE = re.compile(r"ubio_tc\d+_n(\d+)_s(\d+)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("log_dir")
    parser.add_argument("--tc", type=int, required=True)
    parser.add_argument("--num-nodes", type=int, required=True)
    parser.add_argument("--num-sockets", type=int, required=True)
    args = parser.parse_args()

    if args.num_nodes <= 0 or args.num_sockets <= 0:
        parser.error("topology dimensions must be positive")
    expected_planes = {
        (str(node), str(socket))
        for node in range(args.num_nodes)
        for socket in range(args.num_sockets)
    }
    plane_count = len(expected_planes)

    root = pathlib.Path(args.log_dir)
    logs = sorted(root.glob(f"ubio_tc{args.tc}_n*_s*/stdout.log"))
    if len(logs) != plane_count:
        print(f"FAIL: expected {plane_count} UBIO logs, found {len(logs)}")
        return 1

    failures = []
    lifecycle = []
    observed_planes = set()
    for path in logs:
        text = path.read_text(errors="replace")
        starts = START_RE.findall(text)
        quiesces = QUIESCE_RE.findall(text)
        closes = CLOSE_RE.findall(text)
        ack_receives = ACK_RECV_RE.findall(text)
        path_match = PATH_RE.fullmatch(path.parent.name)
        path_plane = path_match.groups() if path_match else None
        if path_plane is None or path_plane not in expected_planes:
            failures.append(f"{path}: path plane is outside expected topology")
        if len(starts) != 1:
            failures.append(f"{path}: START count={len(starts)}")
            continue
        start_local = starts[0][:2]
        if path_plane != start_local:
            failures.append(
                f"{path}: path plane {path_plane} != START local {start_local}")
        if start_local in observed_planes:
            failures.append(f"{path}: duplicate START local plane {start_local}")
        observed_planes.add(start_local)
        if len(quiesces) != 1:
            failures.append(f"{path}: QUIESCE count={len(quiesces)}")
        elif quiesces[0][3] != quiesces[0][4]:
            failures.append(
                f"{path}: acked={quiesces[0][3]}/{quiesces[0][4]}")
        if len(closes) != 1:
            failures.append(f"{path}: CLOSE count={len(closes)}")
        if len(quiesces) == 1 and len(closes) == 1:
            start_id = starts[0][2]
            start_required = int(starts[0][3])
            start_seen = int(starts[0][4])
            start_position = text.find("[PEER-EXIT-START]")
            notify_receives = NOTIFY_RECV_RE.findall(text[:start_position])
            if start_required + start_seen != plane_count - 1:
                failures.append(
                    f"{path}: required+seenNotify="
                    f"{start_required}+{start_seen}!={plane_count - 1}")
            if quiesces[0][:2] != start_local or closes[0][:2] != start_local:
                failures.append(f"{path}: local plane changed across lifecycle")
            if quiesces[0][2] != start_id or closes[0][2] != start_id:
                failures.append(f"{path}: exitId changed across lifecycle")
            if int(quiesces[0][4]) != start_required:
                failures.append(
                    f"{path}: required changed {start_required}->{quiesces[0][4]}")
            matching_ack_peers = {
                (ack[2], ack[3]) for ack in ack_receives
                if ack[:2] == start_local and ack[4] == start_id
            }
            invalid_ack_peers = matching_ack_peers - expected_planes
            if invalid_ack_peers or start_local in matching_ack_peers:
                failures.append(f"{path}: invalid ACK peers={invalid_ack_peers}")
            if len(matching_ack_peers) != start_required:
                failures.append(
                    f"{path}: unique matching ACK peers="
                    f"{len(matching_ack_peers)}/{start_required}")
            seen_notify_peers = {
                (notify[2], notify[3]) for notify in notify_receives
                if notify[:2] == start_local
            }
            invalid_notify_peers = seen_notify_peers - expected_planes
            if invalid_notify_peers or start_local in seen_notify_peers:
                failures.append(
                    f"{path}: invalid Notify peers={invalid_notify_peers}")
            if len(seen_notify_peers) != start_seen:
                failures.append(
                    f"{path}: pre-START unique Notify peers="
                    f"{len(seen_notify_peers)}/{start_seen}")
            expected_required = expected_planes - {start_local} - seen_notify_peers
            if matching_ack_peers != expected_required:
                failures.append(
                    f"{path}: ACK set mismatch expected={sorted(expected_required)} "
                    f"actual={sorted(matching_ack_peers)}")
            lifecycle.append((path, start_local, start_id, text))
        if "[PEER-EXIT-WARN]" in text:
            failures.append(f"{path}: contains PEER-EXIT-WARN")

    if observed_planes != expected_planes:
        failures.append(
            f"local plane set mismatch expected={sorted(expected_planes)} "
            f"actual={sorted(observed_planes)}")

    exit_ids = [item[2] for item in lifecycle]
    if len(exit_ids) != len(set(exit_ids)):
        failures.append("PeerExit nonce collision across UBIO planes")
    for path, _, _, text in lifecycle:
        positions = [
            text.find("[PEER-EXIT-START]"),
            text.find("[PEER-EXIT-QUIESCE]"),
            text.find("[PEER-EXIT-CLOSE]"),
        ]
        if positions != sorted(positions):
            failures.append(f"{path}: lifecycle order is not START/QUIESCE/CLOSE")

    if failures:
        print("FAIL: PeerExit log contract")
        for failure in failures:
            print(failure)
        return 1
    print(f"PASS: TC{args.tc} PeerExit closed {len(logs)}/{plane_count} planes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
