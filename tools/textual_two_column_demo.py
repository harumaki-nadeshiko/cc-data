#!/usr/bin/env python3
"""Simple live-updating two-column Textual interface."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from itertools import cycle

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Label, ListItem, ListView, RichLog


# Edit these values to replace the generated demo feed with project data.
SAMPLE_LOG_MESSAGES = (
    "transport connected",
    "received coherence request req=0x1001",
    "directory lookup completed",
    "forwarding response to node 1",
    "safe timestamp advanced",
)

SAMPLE_ROWS = [
    {"name": "gem5-0", "state": "RUNNING", "detail": "tick 0"},
    {"name": "ubio-0", "state": "READY", "detail": "queue 0"},
    {"name": "network", "state": "IDLE", "detail": "forwarded 0"},
    {"name": "verifier", "state": "WAIT", "detail": "pending"},
]

STATE_SEQUENCE = ("READY", "RUNNING", "WAIT", "DONE")


@dataclass
class LiveRow:
    name: str
    state: str
    detail: str


class TwoColumnDemo(App[None]):
    """A scrolling log beside a list that is replaced in real time."""

    CSS = """
    Screen {
        background: #10151d;
    }

    #columns {
        height: 1fr;
        padding: 1;
    }

    .panel {
        border: round #4f8cff;
        background: #151d29;
        margin: 0 1;
    }

    #log-panel {
        width: 2fr;
    }

    #list-panel {
        width: 1fr;
        min-width: 32;
    }

    .title {
        height: 3;
        padding: 1 2;
        text-style: bold;
        color: #a9c7ff;
    }

    RichLog, ListView {
        height: 1fr;
        margin: 0 1 1 1;
        scrollbar-color: #4f8cff;
    }

    ListItem {
        padding: 0 1;
    }

    ListItem.--highlight {
        background: #28466f;
    }
    """

    BINDINGS = [("q", "quit", "Quit"), ("c", "clear_log", "Clear log")]

    def __init__(self, demo_seconds: float = 0) -> None:
        super().__init__()
        self.demo_seconds = demo_seconds
        self.rows = [LiveRow(**row) for row in SAMPLE_ROWS]
        self.log_messages = cycle(SAMPLE_LOG_MESSAGES)
        self.states = cycle(STATE_SEQUENCE)
        self.sequence = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="columns"):
            with Vertical(classes="panel", id="log-panel"):
                yield Label("LIVE LOG", classes="title")
                yield RichLog(id="live-log", wrap=True, markup=True)
            with Vertical(classes="panel", id="list-panel"):
                yield Label("LIVE STATUS", classes="title")
                yield ListView(id="status-list")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Textual two-column live demo"
        self.sub_title = "q quit | c clear log"
        self._refresh_rows()
        self.set_interval(0.6, self._update_demo)
        if self.demo_seconds > 0:
            self.set_timer(self.demo_seconds, self.exit)

    def _update_demo(self) -> None:
        self.sequence += 1
        log = self.query_one("#live-log", RichLog)
        level = ("green", "cyan", "yellow")[self.sequence % 3]
        log.write(
            f"[dim]event {self.sequence:04d}[/] "
            f"[{level}]{next(self.log_messages)}[/]"
        )

        row = self.rows[self.sequence % len(self.rows)]
        row.state = next(self.states)
        row.detail = f"update {self.sequence}"
        self._refresh_rows()

    def _refresh_rows(self) -> None:
        status_list = self.query_one("#status-list", ListView)
        selected = status_list.index
        status_list.clear()
        for row in self.rows:
            color = {
                "DONE": "green",
                "RUNNING": "cyan",
                "WAIT": "yellow",
                "READY": "blue",
            }.get(row.state, "white")
            status_list.append(
                ListItem(
                    Label(
                        f"[b]{row.name:<12}[/] "
                        f"[{color}]{row.state:<8}[/] {row.detail}"
                    )
                )
            )
        if selected is not None and self.rows:
            status_list.index = min(selected, len(self.rows) - 1)

    def action_clear_log(self) -> None:
        self.query_one("#live-log", RichLog).clear()

    def apply_update(self, log_message: str, rows: list[LiveRow]) -> None:
        """Public hook for replacing the demo feed with application data."""
        self.query_one("#live-log", RichLog).write(log_message)
        self.rows = rows
        self._refresh_rows()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--demo-seconds",
        type=float,
        default=0,
        help="exit automatically after N seconds (useful for smoke tests)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    TwoColumnDemo(demo_seconds=args.demo_seconds).run()
