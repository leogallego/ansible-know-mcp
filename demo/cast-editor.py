#!/usr/bin/env python3
"""Edit asciinema .cast files: compress pauses, trim sections, adjust pacing.

Usage:
    python cast-editor.py input.cast output.cast [--max-pause 2.0] [--speed 1.0]
    python cast-editor.py input.cast output.cast --cut-between "START_TEXT" "END_TEXT"

The .cast format is newline-delimited JSON:
    Line 1: header {"version": 2, "width": 120, "height": 35, ...}
    Line 2+: [timestamp, "o", "text"]
"""

import argparse
import json
import sys
from pathlib import Path


def load_cast(path: Path) -> tuple[dict, list]:
    lines = path.read_text().strip().split("\n")
    header = json.loads(lines[0])
    events = [json.loads(line) for line in lines[1:]]
    return header, events


def save_cast(path: Path, header: dict, events: list) -> None:
    lines = [json.dumps(header)]
    lines.extend(json.dumps(event) for event in events)
    path.write_text("\n".join(lines) + "\n")


def compress_pauses(events: list, max_pause: float) -> list:
    """Cap gaps between events to max_pause seconds."""
    if not events:
        return events

    result = [events[0]]
    cumulative_saved = 0.0

    for i in range(1, len(events)):
        prev_ts = events[i - 1][0]
        curr_ts = events[i][0]
        gap = curr_ts - prev_ts

        if gap > max_pause:
            cumulative_saved += gap - max_pause

        result.append([curr_ts - cumulative_saved, events[i][1], events[i][2]])

    return result


def apply_speed(events: list, speed: float) -> list:
    """Scale all timestamps by 1/speed."""
    if speed == 1.0:
        return events
    return [[e[0] / speed, e[1], e[2]] for e in events]


def cut_between(events: list, start_text: str, end_text: str) -> list:
    """Remove events between two text markers (inclusive of markers)."""
    result = []
    cutting = False
    cut_start_ts = 0.0
    cumulative_cut = 0.0

    for event in events:
        text = event[2] if len(event) > 2 else ""

        if not cutting and start_text in text:
            cutting = True
            cut_start_ts = event[0]
            continue

        if cutting and end_text in text:
            cutting = False
            cumulative_cut += event[0] - cut_start_ts
            continue

        if not cutting:
            result.append([event[0] - cumulative_cut, event[1], event[2]])

    return result


def print_stats(events: list) -> None:
    if not events:
        print("  (empty)", file=sys.stderr)
        return
    duration = events[-1][0] - events[0][0]
    print(f"  Events: {len(events)}", file=sys.stderr)
    print(f"  Duration: {duration:.1f}s ({duration / 60:.1f}m)", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Edit asciinema .cast files")
    parser.add_argument("input", type=Path, help="Input .cast file")
    parser.add_argument("output", type=Path, help="Output .cast file")
    parser.add_argument(
        "--max-pause",
        type=float,
        default=2.0,
        help="Cap pauses to this many seconds (default: 2.0)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Playback speed multiplier (default: 1.0)",
    )
    parser.add_argument(
        "--cut-between",
        nargs=2,
        metavar=("START", "END"),
        action="append",
        help="Cut events between START and END text (repeatable)",
    )

    args = parser.parse_args()

    header, events = load_cast(args.input)

    print("Input:", file=sys.stderr)
    print_stats(events)

    if args.cut_between:
        for start_text, end_text in args.cut_between:
            events = cut_between(events, start_text, end_text)
            print(f"After cutting '{start_text}' .. '{end_text}':", file=sys.stderr)
            print_stats(events)

    events = compress_pauses(events, args.max_pause)
    print(f"After compress (max_pause={args.max_pause}s):", file=sys.stderr)
    print_stats(events)

    events = apply_speed(events, args.speed)
    if args.speed != 1.0:
        print(f"After speed={args.speed}x:", file=sys.stderr)
        print_stats(events)

    save_cast(args.output, header, events)
    print(f"Saved: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
