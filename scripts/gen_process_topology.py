#!/usr/bin/env python3
"""Generate the process-launch topology consumed by run_multi.sh."""

import argparse
import json
import pathlib


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, required=True)
    parser.add_argument("--sockets", type=int, choices=(1, 2), required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not 2 <= args.nodes <= 8:
        raise SystemExit("--nodes must be in [2,8]")

    modules = []
    links = []
    for node in range(args.nodes):
        modules.append({
            "id": f"gem5_{node}",
            "cmd": (
                "{gem5_bin} --outdir={node_outdir} {test_e2e} "
                f"--node-id={node} --num-nodes={args.nodes} "
                f"--num-sockets={args.sockets} --workload={{workload}}"),
        })
    for node in range(args.nodes):
        for socket in range(args.sockets):
            module_id = f"ubio_{node}_s{socket}"
            modules.append({
                "id": module_id,
                "cmd": (
                    f"{{ubio_bin}} --node={node} --socket={socket} "
                    f"--num-sockets={args.sockets} --num-nodes={args.nodes} "
                    "{fault_rules_args} {ubio_extra_args}"),
            })
            links.append([
                f"gem5_{node}", f"mem-{socket}", module_id, "mem-0"])
    modules.append({"id": "networksim", "cmd": "{nsim_bin} {topo_json}"})
    for node in range(args.nodes):
        for socket in range(args.sockets):
            plane = node * args.sockets + socket
            links.append([
                f"ubio_{node}_s{socket}", "mem-1", "networksim",
                f"mem-{plane}"])

    output = {
        "_comment": "Generated process topology; plane=node*sockets+socket.",
        "num_nodes": args.nodes,
        "num_sockets": args.sockets,
        "modules": modules,
        "links": links,
    }
    pathlib.Path(args.out).write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
