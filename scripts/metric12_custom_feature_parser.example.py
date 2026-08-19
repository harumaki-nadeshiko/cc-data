#!/usr/bin/env python3
"""Example site-specific feature parser for analyze_metric12_run_list.py."""

import re

from analyze_metric12_run_list import (
    FeatureParseError,
    Metric12RunListAnalyzer,
    RunFeature,
)


class RemoteMetric12Analyzer(Metric12RunListAnalyzer):
    def parse_feature(self, feature):
        """Replace this prototype with the remote naming convention.

        This example only demonstrates the override point. A real parser must
        recover target, round, case, topology, profile, and optionally phase.
        """
        match = re.fullmatch(
            r"PERF-(?P<tag>[A-Z]+)-(?P<case>TC\d+)-"
            r"(?P<topology>\d+n\d+s)-(?P<profile>naive|spill|optimized)",
            feature)
        if not match:
            raise FeatureParseError(f"unsupported remote feature: {feature}")
        values = match.groupdict()
        case_number = int(values["case"][2:])
        target = "target1" if case_number == 131 else "target2"
        # Placeholder only: replace this site-specific tag mapping. For
        # example, PERF-B-TC132-3n1s-spill could map B to a specific round and
        # profile policy in the remote environment. The official metric 1/2
        # analyzer will still reject TC132 because the frozen contract matrix
        # is TC131 plus TC135-TC140/TC217.
        round_by_tag = {"A": 1, "B": 2, "C": 3}
        if values["tag"] not in round_by_tag:
            raise FeatureParseError(
                f"unsupported remote round tag {values['tag']!r} in {feature!r}")
        return RunFeature(
            target=target,
            round_id=round_by_tag[values["tag"]],
            case=values["case"],
            topology=values["topology"],
            profile=values["profile"],
        )
