#!/usr/bin/env python3
"""Generate the networksim crossbar topology JSON (topo.json) for a given
socket configuration.

usage:
    python3 scripts/gen_topo.py --type 1s --out /path/to/topo.json
    python3 scripts/gen_topo.py --type 2s --out /path/to/topo.json
    python3 scripts/gen_topo.py --nodes 3 --sockets 2 --out /path/to/topo.json

Builds a full-mesh among all network modules (one ubio plane per
(node,socket) = NMOD modules). Topology port is always 1
(routing in networksim uses module id only).

  type 1s : 3 nodes x 1 socket    => NMOD=3  =>  3 links
  type 2s : 3 nodes x 2 sockets   => NMOD=6  => 15 links

Latency policy (ps):
  cross-node        = 415000 ps (415 ns)
  same-node cross-socket = 225000 ps (225 ns, mid of 210-240 ns range)

Node mapping: mod_id = node * num_sockets + socket
"""
import argparse, json, sys

# Target latencies per design doc §6.1
CROSS_NODE_LATENCY = 415000
CROSS_SOCKET_LATENCY = 225000


def main():
    p = argparse.ArgumentParser(description="Generate networksim topo.json")
    p.add_argument("--type", choices=["1s", "2s"],
                   help="shorthand for --nodes/--sockets (1s=3x1, 2s=3x2)")
    p.add_argument("--nodes", type=int,
                   help="number of nodes (default 3)")
    p.add_argument("--sockets", type=int,
                   help="sockets per node (default derived from --type)")
    p.add_argument("--out", required=True, help="output topo.json path")
    args = p.parse_args()

    if args.type == "1s":
        num_nodes = args.nodes if args.nodes else 3
        num_sockets = args.sockets if args.sockets else 1
    elif args.type == "2s":
        num_nodes = args.nodes if args.nodes else 3
        num_sockets = args.sockets if args.sockets else 2
    else:
        if not args.nodes or not args.sockets:
            p.error("must specify --type or both --nodes and --sockets")
        num_nodes = args.nodes
        num_sockets = args.sockets

    nmod = num_nodes * num_sockets

    links = []
    for a in range(nmod):
        node_a = a // num_sockets
        for b in range(a + 1, nmod):
            node_b = b // num_sockets
            lat = CROSS_NODE_LATENCY if node_a != node_b else CROSS_SOCKET_LATENCY
            links.append([a, 1, b, 1, lat])

    with open(args.out, "w") as f:
        json.dump({"links": links}, f, indent=2)
        f.write("\n")
    n_links = len(links)
    print(f"[gen_topo] nodes={num_nodes} sockets={num_sockets} "
          f"NMOD={nmod} links={n_links} -> {args.out}")

if __name__ == "__main__":
    main()