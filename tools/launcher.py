#!/usr/bin/env python3
"""Launcher: reads master JSON config, generates per-module configs,
   launches all processes, monitors gem5 exit, and terminates others."""

import json, os, sys, subprocess, time, signal, tempfile

def load_config(path):
    with open(path) as f: return json.load(f)

def gen_per_module_configs(cfg):
    """Generate per-module config JSON files from master config.
    Returns dict: module_name → config_path."""
    per_mod = {}
    modules = cfg.get("modules", [])
    links = cfg.get("links", [])
    sync_window = cfg.get("sync_window", 100000)
    workdir = cfg.get("workdir", "/tmp/cc_ep_run")
    os.makedirs(workdir, exist_ok=True)

    for m in modules:
        name = m["name"]
        mod_id = m["module_id"]
        mod_type = m["type"]

        # Find ports and endpoints for this module
        ports = []
        for l in links:
            src_name, src_port = l[0], l[1]
            dst_name, dst_port = l[2], l[3]
            latency = l[4] if len(l) > 4 else 1

            if src_name == name:
                ep = f"ipc://{workdir}/{name}_p{src_port}"
                ports.append({
                    "port_id": src_port,
                    "endpoint": ep,
                    "bind": True,
                    "peer_module": dst_name,
                    "peer_port": dst_port,
                    "latency": latency
                })
            if dst_name == name:
                ep = f"ipc://{workdir}/{src_name}_p{src_port}"
                ports.append({
                    "port_id": dst_port,
                    "endpoint": ep,
                    "bind": False,
                    "peer_module": src_name,
                    "peer_port": src_port,
                    "latency": latency
                })

        mod_cfg = {
            "module_name": name,
            "module_id": mod_id,
            "module_type": mod_type,
            "sync_window": sync_window,
            "ports": ports,
            "executable": m.get("executable", ""),
            "args": m.get("args", [])
        }
        path = os.path.join(workdir, f"cfg_{name}.json")
        with open(path, "w") as f:
            json.dump(mod_cfg, f, indent=2)
        per_mod[name] = path

    # Generate topology for networksim
    topo_links = []
    for l in links:
        topo_links.append([l[0], l[1], l[2], l[3], l[4] if len(l) > 4 else 1])
    topo = {"links": topo_links, "sync_window": sync_window}
    topo_path = os.path.join(workdir, "topology.json")
    with open(topo_path, "w") as f:
        json.dump(topo, f, indent=2)

    return per_mod, topo_path, workdir

def launch_all(cfg_path):
    cfg = load_config(cfg_path)
    per_mod, topo_path, workdir = gen_per_module_configs(cfg)

    procs = {}

    # Find networksim module
    nsim_name = None
    for m in cfg.get("modules", []):
        if m["type"] == "networksim":
            nsim_name = m["name"]
            break

    # Launch networksim first
    if nsim_name and nsim_name in per_mod:
        m = next(x for x in cfg["modules"] if x["name"] == nsim_name)
        exe = m.get("executable", "./modules/networksim/networksim")
        args = [exe, topo_path] + m.get("args", [])
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        procs[nsim_name] = p
        print(f"[launcher] started {nsim_name} (pid={p.pid})")

    # Launch all other modules
    for m in cfg.get("modules", []):
        name = m["name"]
        if name in procs: continue
        if name not in per_mod: continue
        exe = m.get("executable", "")
        if not exe: continue
        cfg_path = per_mod[name]
        args = [exe, "--config", cfg_path] + m.get("args", [])
        log_path = os.path.join(workdir, f"log_{name}.txt")
        with open(log_path, "w") as log:
            p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
        procs[name] = p
        print(f"[launcher] started {name} (pid={p.pid})")
        time.sleep(0.2)

    # Monitor: wait for all gem5 processes to exit
    gem5_names = [m["name"] for m in cfg.get("modules", []) if m["type"] == "gem5"]
    remaining = set(gem5_names)
    timeout_s = cfg.get("timeout_seconds", 300)
    start = time.time()

    while remaining and (time.time() - start) < timeout_s:
        for name in list(remaining):
            if name not in procs: continue
            p = procs[name]
            if p.poll() is not None:
                print(f"[launcher] {name} exited (rc={p.returncode})")
                remaining.remove(name)
        time.sleep(0.5)

    if remaining:
        print(f"[launcher] timeout after {timeout_s}s, remaining: {remaining}")

    # Terminate all other processes
    for name, p in procs.items():
        if p.poll() is None:
            print(f"[launcher] terminating {name}")
            p.terminate()
            try: p.wait(timeout=5)
            except: p.kill()

    print("[launcher] done")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: launcher.py <master_config.json>")
        sys.exit(1)
    launch_all(sys.argv[1])
