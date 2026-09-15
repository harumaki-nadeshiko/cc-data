"""Validate real native Park coverage, separately from the basic TC registry."""
import argparse
from pathlib import Path
import re


def verify(root, tc=2, expected_controls=1, reacquire=False, ha=False):
    stderr = (root / f"gem5_tc{tc}_node1/stderr.log").read_text()
    debug = (root / f"gem5_tc{tc}_node1/gem5_debug.log").read_text()
    markers = re.findall(r"\[PARK-MICRO\] stage=(\w+).*?control=(\d+)", stderr)
    identities = list(dict.fromkeys(identity for _, identity in markers))
    assert len(identities) == expected_controls, markers
    events = re.findall(
        r"\[EP-PARK-RX\] controller=(\S+) addr=(\S+) type=(\w+) "
        r"incarnation=(\d+) control=(\d+) phase=(\d+)", debug
    )
    for identity in identities:
        assert [stage for stage, token in markers if token == identity] == [
            "PARKED", "LOCAL_CONTROL_DONE", "RESUMED"
        ], markers
        chain = [event for event in events if event[4] == identity]
        assert len({event[1] for event in chain}) == 1, chain
        parks = [event for event in chain if event[2] == "AcquirePark"]
        resumes = [event for event in chain if event[2] == "AcquireResume"]
        assert len(parks) == 3 and len(resumes) == 3, chain
        assert [(e[0], e[3]) for e in parks] == [(e[0], e[3]) for e in resumes]
        for kind in ("AcquireParkAck", "AcquireResumeAck"):
            assert len([event for event in chain if event[2] == kind]) == 2, chain
    assert debug.count("[EP-PARK-LOCAL-FINAL]") == expected_controls
    if reacquire:
        restarted = re.findall(
            r"\[EP-PARK-REACQUIRE\].*?addr=(\S+) incarnation=(\d+) parentTxn=(\d+)",
            debug)
        assert len(restarted) == expected_controls, restarted
        for address, incarnation, _ in restarted:
            old = [e for e in events if e[1] == address and e[2] == "AcquirePark"]
            assert old and incarnation != old[0][3], (restarted, old)
            if ha:
                # The HA trace uses canonical Home PA, not requester local PA.
                # Require the old read to close before the new read is issued,
                # and the later CPU mutation to occur exactly once.
                requests = re.findall(r"\|gem5\|(\d+)\|[^|]+\|SEND\|HAPermissionReq\|", stderr)
                acknowledgements = re.findall(r"\|gem5\|(\d+)\|[^|]+\|SEND\|HAPermissionAck\|", stderr)
                assert len(requests) == 3 and acknowledgements == requests
                assert stderr.count("phase=STORE_MUTATE") == 1
                protocol = re.findall(
                    r"\|gem5\|(\d+)\|[^|]+\|SEND\|(HAPermissionReq|HAPermissionAck)\|",
                    stderr)
                assert protocol.index((requests[0], "HAPermissionAck")) < protocol.index(
                    (requests[1], "HAPermissionReq"))
            else:
                requests = re.findall(
                    r"\[PENDING-READ-SAVE\].*?localPa=" + re.escape(address) +
                    r" .*?reqId=(\d+)", debug)
            assert len(set(requests)) >= 2, requests
    if expected_controls == 2:
        sockets = re.findall(r"\[EP-PARK-TX\] node=1 socket=(\d+)", debug)
        assert sorted(sockets) == ["0", "1"], sockets
    for path in root.glob(f"gem5_tc{tc}_node*/stderr.log"):
        text = path.read_text()
        assert not re.search(r"panic:|fatal:|Assertion .* failed|Program aborted", text), path
    print("PASS native Park/Resume: HN-L2-L1 chain, exact identities, local control final")
    print(f"CPU value and exit closure must also pass the TC{tc} verifier.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("log_directory", type=Path)
    parser.add_argument("--tc", type=int, default=2)
    parser.add_argument("--controls", type=int, default=1)
    parser.add_argument("--reacquire", action="store_true")
    parser.add_argument("--ha", action="store_true")
    args = parser.parse_args()
    verify(args.log_directory, args.tc, args.controls, args.reacquire, args.ha)
