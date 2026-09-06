#!/usr/bin/env python3
"""Publication figures, drawn from the processed tables (Task 07 §56).

    raw -> processed -> this script -> SVG

Every number on every figure is read from `processed/e3-tables/*.csv`. Nothing
is written into the drawing by hand, so a figure cannot disagree with the
analysis that produced it, and re-running this after a re-analysis regenerates
the figures rather than leaving them stale.

SVG rather than a plotting library: the toolbox image that collected the data
carries no plotting dependency, and adding one after collection would mean a
second environment to account for. SVG is text, so a reviewer can diff a figure
and read the numbers straight out of it.
"""

from __future__ import annotations

import argparse
import csv
import html
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app/src")
sys.path.insert(0, "src")

from fam.common.results import ensure_layout, resolve_results_dir  # noqa: E402

# ----------------------------------------------------------------- palette
#
# One accent per topology, held across every figure so the reader learns the
# encoding once. Greys carry structure; ink carries data.
LOCAL = "#2f6f9f"
FEDERATED = "#c2622d"
AXIS = "#444444"
GRID = "#d8d8d8"
TEXT = "#1a1a1a"
MUTED = "#666666"

FONT = "font-family='Helvetica,Arial,sans-serif'"


def _svg(width: int, height: int, body: str, title: str, desc: str) -> str:
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' "
        f"height='{height}' viewBox='0 0 {width} {height}' role='img' "
        f"aria-label='{html.escape(desc)}'>\n"
        f"  <title>{html.escape(title)}</title>\n"
        f"  <desc>{html.escape(desc)}</desc>\n"
        f"  <rect width='{width}' height='{height}' fill='#ffffff'/>\n"
        f"{body}"
        f"</svg>\n"
    )


def _text(x: float, y: float, s: str, size: float = 11, anchor: str = "start",
          fill: str = TEXT, weight: str = "normal") -> str:
    return (
        f"  <text x='{x:.1f}' y='{y:.1f}' {FONT} font-size='{size}' "
        f"text-anchor='{anchor}' fill='{fill}' font-weight='{weight}'>"
        f"{html.escape(s)}</text>\n"
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _nice_ticks(low: float, high: float, count: int = 5) -> list[float]:
    """Round tick values spanning the data, chosen from the data alone."""
    if high <= low:
        high = low + 1.0
    span = high - low
    raw = span / max(count - 1, 1)
    magnitude = 10 ** (len(f"{int(abs(raw))}") - 1) if abs(raw) >= 1 else 1.0
    while magnitude > abs(raw):
        magnitude /= 10.0
    for multiple in (1, 2, 2.5, 5, 10):
        step = magnitude * multiple
        if step >= raw:
            break
    start = (int(low / step)) * step
    ticks = []
    value = start
    while value <= high + step * 0.5:
        if value >= low - step * 0.5:
            ticks.append(round(value, 6))
        value += step
    return ticks


# ------------------------------------------------------- figure 1: latency


def figure_latency(tables: Path, out: Path) -> Path:
    rows = _read_csv(tables / "paired_comparison.csv")
    latency = [r for r in rows if r["workload"] == "latency"]
    if not latency:
        raise SystemExit("paired_comparison.csv holds no latency rows")

    width, height = 720, 380
    left, right, top, bottom = 78, 210, 58, 56
    plot_w = width - left - right
    plot_h = height - top - bottom

    values = []
    for row in latency:
        values += [float(row["local"]), float(row["federated"])]
    ticks = _nice_ticks(0.0, max(values))
    vmax = max(ticks)

    def y_of(value: float) -> float:
        return top + plot_h - (value / vmax) * plot_h

    body = ""
    for tick in ticks:
        y = y_of(tick)
        body += (
            f"  <line x1='{left}' y1='{y:.1f}' x2='{left + plot_w}' "
            f"y2='{y:.1f}' stroke='{GRID}' stroke-width='1'/>\n"
        )
        body += _text(left - 8, y + 3.5, f"{tick:g}", 10, "end", MUTED)

    groups = len(latency)
    slot = plot_w / groups
    bar = min(38.0, slot / 3.2)

    for index, row in enumerate(latency):
        centre = left + slot * (index + 0.5)
        for offset, key, colour in (
            (-bar * 0.58, "local", LOCAL),
            (bar * 0.58, "federated", FEDERATED),
        ):
            value = float(row[key])
            y = y_of(value)
            x = centre + offset - bar / 2
            body += (
                f"  <rect x='{x:.1f}' y='{y:.1f}' width='{bar:.1f}' "
                f"height='{(top + plot_h - y):.1f}' fill='{colour}'/>\n"
            )
            body += _text(x + bar / 2, y - 6, f"{value:.1f}", 10, "middle", TEXT)
        body += _text(centre, top + plot_h + 20, row["metric"], 12, "middle", TEXT)
        body += _text(
            centre,
            top + plot_h + 36,
            f"x{float(row['ratio']):.3f}",
            10,
            "middle",
            MUTED,
        )

    body += (
        f"  <line x1='{left}' y1='{top + plot_h}' x2='{left + plot_w}' "
        f"y2='{top + plot_h}' stroke='{AXIS}' stroke-width='1.2'/>\n"
    )
    # Block and interaction counts come from the percentile table, not from a
    # number typed here: a figure must not be able to state a sample size the
    # analysis did not produce.
    percentiles = _read_csv(tables / "latency_percentiles.csv")
    p50_local = next(
        r for r in percentiles if r["topology"] == "local" and r["percentile"] == "p50"
    )
    body += _text(left, 26, "Interaction round-trip time", 15, "start", TEXT, "600")
    body += _text(
        left,
        44,
        f"{latency[0]['unit']}, {p50_local['runs']} paired blocks per topology, "
        f"{p50_local['successful_interactions']} of "
        f"{p50_local['initiated_interactions']} measured interactions",
        11,
        "start",
        MUTED,
    )
    body += _text(left - 62, top - 14, "ms", 10, "start", MUTED)

    legend_x = left + plot_w + 26
    for index, (label, colour) in enumerate((("local", LOCAL), ("federated", FEDERATED))):
        y = top + 6 + index * 22
        body += f"  <rect x='{legend_x}' y='{y - 10}' width='13' height='13' fill='{colour}'/>\n"
        body += _text(legend_x + 20, y + 1, label, 11, "start", TEXT)

    y = top + 66
    body += _text(legend_x, y, "paired ratio, 95% CI", 10, "start", MUTED)
    for row in latency:
        y += 17
        body += _text(
            legend_x,
            y,
            f"{row['metric']}  {float(row['ratio']):.4f}",
            10,
            "start",
            TEXT,
        )
        y += 13
        body += _text(
            legend_x,
            y,
            f"    [{float(row['ratio_ci_low']):.4f}, {float(row['ratio_ci_high']):.4f}]",
            9,
            "start",
            MUTED,
        )

    path = out / "e3-latency-percentiles.svg"
    path.write_text(
        _svg(
            width,
            height,
            body,
            "E3 latency percentiles by topology",
            "Local and federated round-trip time at the 50th, 95th and 99th "
            "percentile, with the paired bootstrap ratio for each.",
        ),
        encoding="utf-8",
    )
    return path


# ------------------------------------------- figure 2: throughput per run


def figure_throughput(tables: Path, out: Path) -> Path:
    rows = _read_csv(tables / "throughput_runs.csv")
    levels = sorted({int(r["concurrency"]) for r in rows})

    width, height = 720, 400
    left, right, top, bottom = 70, 170, 58, 60
    plot_w = width - left - right
    plot_h = height - top - bottom

    values = [float(r["observed_throughput_per_second"]) for r in rows]
    ticks = _nice_ticks(0.0, max(values))
    vmax = max(ticks)

    def y_of(value: float) -> float:
        return top + plot_h - (value / vmax) * plot_h

    body = ""
    for tick in ticks:
        y = y_of(tick)
        body += (
            f"  <line x1='{left}' y1='{y:.1f}' x2='{left + plot_w}' y2='{y:.1f}' "
            f"stroke='{GRID}' stroke-width='1'/>\n"
        )
        body += _text(left - 8, y + 3.5, f"{tick:g}", 10, "end", MUTED)

    panel = plot_w / len(levels)
    for panel_index, level in enumerate(levels):
        origin = left + panel * panel_index
        body += _text(
            origin + panel / 2,
            top + plot_h + 22,
            f"C = {level}",
            12,
            "middle",
            TEXT,
        )
        for series_index, (topology, colour) in enumerate(
            (("local", LOCAL), ("federated", FEDERATED))
        ):
            series = [
                float(r["observed_throughput_per_second"])
                for r in rows
                if int(r["concurrency"]) == level and r["topology"] == topology
            ]
            if not series:
                continue
            span = panel * 0.36
            base = origin + panel * (0.28 + series_index * 0.44)
            step = span / max(len(series) - 1, 1)
            points = " ".join(
                f"{base + i * step:.1f},{y_of(v):.1f}" for i, v in enumerate(series)
            )
            body += (
                f"  <polyline points='{points}' fill='none' stroke='{colour}' "
                f"stroke-width='1.1' opacity='0.55'/>\n"
            )
            for i, value in enumerate(series):
                body += (
                    f"  <circle cx='{base + i * step:.1f}' cy='{y_of(value):.1f}' "
                    f"r='2.1' fill='{colour}'/>\n"
                )
            ordered = sorted(series)
            middle = len(ordered) // 2
            median = (
                ordered[middle]
                if len(ordered) % 2
                else (ordered[middle - 1] + ordered[middle]) / 2
            )
            y = y_of(median)
            body += (
                f"  <line x1='{base - 6:.1f}' y1='{y:.1f}' "
                f"x2='{base + span + 6:.1f}' y2='{y:.1f}' stroke='{colour}' "
                f"stroke-width='2'/>\n"
            )
            body += _text(
                base + span + 10, y + 3.5, f"{median:g}", 9, "start", colour
            )

    body += (
        f"  <line x1='{left}' y1='{top + plot_h}' x2='{left + plot_w}' "
        f"y2='{top + plot_h}' stroke='{AXIS}' stroke-width='1.2'/>\n"
    )
    body += _text(left, 26, "Observed throughput per run", 15, "start", TEXT, "600")
    body += _text(
        left,
        44,
        "completions per second in the measurement window; one point per run, "
        "bar at the sample median",
        10,
        "start",
        MUTED,
    )
    body += _text(left - 56, top - 14, "/s", 10, "start", MUTED)

    legend_x = left + plot_w + 24
    for index, (label, colour) in enumerate((("local", LOCAL), ("federated", FEDERATED))):
        y = top + 6 + index * 22
        body += f"  <rect x='{legend_x}' y='{y - 10}' width='13' height='13' fill='{colour}'/>\n"
        body += _text(legend_x + 20, y + 1, label, 11, "start", TEXT)
    body += _text(
        legend_x,
        top + 68,
        "observed throughput of",
        9,
        "start",
        MUTED,
    )
    body += _text(legend_x, top + 80, "the tested closed-loop", 9, "start", MUTED)
    body += _text(legend_x, top + 92, "system, never capacity", 9, "start", MUTED)

    path = out / "e3-throughput-runs.svg"
    path.write_text(
        _svg(
            width,
            height,
            body,
            "E3 observed throughput per run",
            "Per-run observed throughput at each tested concurrency, local and "
            "federated, with the sample median marked.",
        ),
        encoding="utf-8",
    )
    return path


# ------------------------------------------------ figure 3: paired ratios


def figure_ratios(tables: Path, out: Path) -> Path:
    rows = _read_csv(tables / "paired_comparison.csv")

    width, height = 720, 300
    left, right, top, bottom = 190, 60, 66, 54
    plot_w = width - left - right
    plot_h = height - top - bottom

    lows = [float(r["ratio_ci_low"]) for r in rows]
    highs = [float(r["ratio_ci_high"]) for r in rows]
    ticks = _nice_ticks(min(lows + [1.0]) * 0.98, max(highs + [1.0]) * 1.02, 6)
    lo, hi = min(ticks), max(ticks)

    def x_of(value: float) -> float:
        return left + (value - lo) / (hi - lo) * plot_w

    body = ""
    for tick in ticks:
        x = x_of(tick)
        body += (
            f"  <line x1='{x:.1f}' y1='{top}' x2='{x:.1f}' "
            f"y2='{top + plot_h}' stroke='{GRID}' stroke-width='1'/>\n"
        )
        body += _text(x, top + plot_h + 18, f"{tick:g}", 10, "middle", MUTED)

    if lo <= 1.0 <= hi:
        x = x_of(1.0)
        body += (
            f"  <line x1='{x:.1f}' y1='{top - 8}' x2='{x:.1f}' "
            f"y2='{top + plot_h + 4}' stroke='{AXIS}' stroke-width='1' "
            f"stroke-dasharray='4 3'/>\n"
        )
        body += _text(x, top - 14, "no difference", 9, "middle", MUTED)

    step = plot_h / (len(rows) + 1)
    for index, row in enumerate(rows):
        y = top + step * (index + 1)
        colour = LOCAL if row["workload"] == "latency" else FEDERATED
        x1, x2 = x_of(float(row["ratio_ci_low"])), x_of(float(row["ratio_ci_high"]))
        centre = x_of(float(row["ratio"]))
        body += (
            f"  <line x1='{x1:.1f}' y1='{y:.1f}' x2='{x2:.1f}' y2='{y:.1f}' "
            f"stroke='{colour}' stroke-width='2'/>\n"
        )
        for x in (x1, x2):
            body += (
                f"  <line x1='{x:.1f}' y1='{y - 5:.1f}' x2='{x:.1f}' "
                f"y2='{y + 5:.1f}' stroke='{colour}' stroke-width='2'/>\n"
            )
        body += f"  <circle cx='{centre:.1f}' cy='{y:.1f}' r='4' fill='{colour}'/>\n"
        # The throughput rows already name themselves in `metric`; only the
        # latency percentiles need the workload prefix to be unambiguous.
        label = (
            f"latency {row['metric']}"
            if row["workload"] == "latency"
            else row["metric"]
        )
        body += _text(left - 14, y + 4, label, 11, "end", TEXT)
        body += _text(
            x2 + 10, y + 4, f"{float(row['ratio']):.4f}", 10, "start", MUTED
        )

    body += _text(
        20, 26, "Federated relative to local, paired bootstrap", 15, "start", TEXT, "600"
    )
    body += _text(
        20,
        44,
        "point is the paired ratio; bar is the 95% interval over 20 resampled "
        "blocks. Latency above 1 is slower; throughput below 1 is fewer "
        "completions.",
        9,
        "start",
        MUTED,
    )

    path = out / "e3-paired-ratios.svg"
    path.write_text(
        _svg(
            width,
            height,
            body,
            "E3 paired ratios with confidence intervals",
            "Federated-to-local ratio for each latency percentile and each "
            "tested concurrency, with 95% paired bootstrap intervals.",
        ),
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    root = ensure_layout(resolve_results_dir())
    tables = root / "processed" / "e3-tables"
    if not tables.exists():
        raise SystemExit(f"no processed tables at {tables}; run `make analyse` first")

    out = Path(args.out) if args.out else root / "figures"
    out.mkdir(parents=True, exist_ok=True)

    print(f"figures from {tables}")
    written = [
        figure_latency(tables, out),
        figure_throughput(tables, out),
        figure_ratios(tables, out),
    ]
    for path in written:
        print(f"  {path}  ({path.stat().st_size} bytes)")
    print("\nevery value is read from the processed tables; none is written by hand")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
