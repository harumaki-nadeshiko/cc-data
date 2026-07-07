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
  cross-node same-socket  = --cross-node-latency  (default 405000 = 405 ns)
  same-node cross-socket  = --cross-socket-latency (default  25000 =  25 ns)

  TODO(2-hop): cross-node + cross-socket links currently use a single-hop
  heterogeneous delay = cross_node + cross_socket (D4 temporary scheme).
  Physically this should be two-hop forwarding (inter-node link + inter-socket
  link). Revert to proper multi-hop routing once networksim supports it.

Node mapping: mod_id = node * num_sockets + socket
"""
import argparse, json, sys


def main():
    p = argparse.ArgumentParser(description="Generate networksim topo.json")
    p.add_argument("--type", choices=["1s", "2s"],
                   help="shorthand for --nodes/--sockets (1s=3x1, 2s=3x2)")
    p.add_argument("--nodes", type=int,
                   help="number of nodes (default 3)")
    p.add_argument("--sockets", type=int,
                   help="sockets per node (default derived from --type)")
    p.add_argument("--cross-node-latency", type=int, default=405000,
                   help="cross-node same-socket link latency in ps (default 405000)")
    p.add_argument("--cross-socket-latency", type=int, default=25000,
                   help="same-node cross-socket link latency in ps (default 25000)")
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

    cross_node = args.cross_node_latency
    cross_sock = args.cross_socket_latency
    nmod = num_nodes * num_sockets

    links = []
    # Per-class counters for summary
    cnt_node = 0
    cnt_sock = 0
    cnt_both = 0

    for a in range(nmod):
        node_a = a // num_sockets
        sock_a = a % num_sockets
        for b in range(a + 1, nmod):
            node_b = b // num_sockets
            sock_b = b % num_sockets

            # TODO(2-hop): cross-node + cross-socket currently single-hop
            # with heterogeneous delay = cross_node + cross_socket.
            # Physically should be two-hop forwarding; revert once nsim
            # supports multi-hop routing.
            if node_a != node_b and sock_a != sock_b:
                lat = cross_node + cross_sock
                cnt_both += 1
            elif node_a != node_b:
                lat = cross_node
                cnt_node += 1
            else:
                lat = cross_sock
                cnt_sock += 1
            links.append([a, 1, b, 1, lat])

    with open(args.out, "w") as f:
        json.dump({"links": links}, f, indent=2)
        f.write("\n")
    n_links = len(links)
    print(f"[gen_topo] nodes={num_nodes} sockets={num_sockets} "
          f"NMOD={nmod} links={n_links} -> {args.out}")
    print(f"[gen_topo]   cross-node           latency={cross_node:>7} ps  count={cnt_node}")
    print(f"[gen_topo]   cross-socket         latency={cross_sock:>7} ps  count={cnt_sock}")
    print(f"[gen_topo]   cross-node+socket    latency={cross_node + cross_sock:>7} ps  count={cnt_both}")

if __name__ == "__main__":
    main()
