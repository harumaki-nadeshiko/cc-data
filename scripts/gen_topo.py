#!/usr/bin/env python3
"""Generate the networksim crossbar topology JSON (topo.json) for a given
socket configuration.

usage:
    python3 scripts/gen_topo.py --type 1s --out /path/to/topo.json
    python3 scripts/gen_topo.py --type 2s --out /path/to/topo.json

Both variants build a full-mesh among all network modules (one ubio plane per
(node,socket) = NMOD modules), uniform latency 100000, and topology port 1
(routing in networksim uses module id only — see Phase 3b fix rationale).

  type 1s : 3 nodes x 1 socket    => NMOD=3  =>  3 links
  type 2s : 3 nodes x 2 sockets   => NMOD=6  => 15 links
"""
import argparse, json, sys

def main():
    p = argparse.ArgumentParser(description="Generate networksim topo.json")
    p.add_argument("--type", required=True, choices=["1s", "2s"])
    p.add_argument("--out", required=True, help="output topo.json path")
    args = p.parse_args()

    if args.type == "1s":
        nmod = 3
    else:
        nmod = 6
    latency = 100000

    links = []
    for a in range(nmod):
        for b in range(a + 1, nmod):
            links.append([a, 1, b, 1, latency])

    with open(args.out, "w") as f:
        json.dump({"links": links}, f, indent=2)
        f.write("\n")
    n_links = len(links)
    print(f"[gen_topo] type={args.type} NMOD={nmod} links={n_links} -> {args.out}")

if __name__ == "__main__":
    main()