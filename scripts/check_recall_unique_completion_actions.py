#!/usr/bin/env python3
"""Check that every HN-F ReadUnique completion path emits RecallUnique Comp_UC."""

import pathlib
import re


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm"
ACTION_RE = re.compile(
    r"action\((Initiate_ReadUnique_(?:Hit|HitUpstream|Hit_InvUpstream)),.*?\n}"
    r"(?=\n\naction\(|\Z)",
    re.DOTALL,
)
REQUIRED = (
    "Initiate_ReadUnique_Hit",
    "Initiate_ReadUnique_HitUpstream",
    "Initiate_ReadUnique_Hit_InvUpstream",
)


text = SOURCE.read_text()
actions = {match.group(1): match.group(0) for match in ACTION_RE.finditer(text)}
errors = []
for name in REQUIRED:
    body = actions.get(name)
    if body is None:
        errors.append(f"missing action: {name}")
        continue
    if "tbe.epProxyOp == EpProxyOp:RecallUnique" not in body:
        errors.append(f"{name}: missing RecallUnique gate")
    if "SendCompUCResp" not in body:
        errors.append(f"{name}: missing SendCompUCResp")
    if "SendCompData" not in body:
        errors.append(f"{name}: missing SendCompData")

if errors:
    raise SystemExit("\n".join(errors))

print("RecallUnique ReadUnique completion actions: PASS")
