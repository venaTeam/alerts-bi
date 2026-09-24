"""Weekly history charts, as inline SVG rendered on the server (design section 7.10).

One point per published week, dated by the day the week ends. The chart plots numbers and
concludes nothing: no delta, no percentage, no trend line. Published weeks are managed back
to back; where two consecutive weeks are not adjacent the line is broken rather than drawn
across days nobody reviewed.

No inline ``style`` attributes and no script: colours come from CSS classes, so the portal's
Content-Security-Policy can forbid both.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape

__all__ = ["ChartPoint", "line_chart", "nice_step"]


@dataclass(frozen=True, slots=True)
class ChartPoint:
    label: str
    value: int
    window_start: datetime
    window_end: datetime
    href: str
    title: str
    selected: bool = False


def nice_step(raw: float) -> float:
    """A round tick step (1, 2 or 5 times a power of ten) at or above ``raw``."""
    if raw <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(raw))
    for multiple in (1, 2, 5, 10):
        if raw <= multiple * power:
            return float(multiple * power)
    return float(10 * power)


def _fmt(value: float) -> str:
    return f"{value:.1f}"


def line_chart(points: Sequence[ChartPoint], *, series: str, label: str) -> str:
    """Render one series. ``series`` is the CSS class that colours it (``v1`` or ``v2``)."""
    width, height = 560, 180
    left, right, top, bottom = 48, 28, 24, 30
    count = len(points)
    plot_width = width - left - right

    def x(index: int) -> float:
        if count == 1:
            return left + plot_width / 2
        return left + 18 + index * (plot_width - 36) / (count - 1)

    maximum = max([point.value for point in points] + [4])
    step = nice_step(maximum / 4)
    top_value = math.ceil(maximum * 1.18 / step) * step

    def y(value: float) -> float:
        return top + (1 - value / top_value) * (height - top - bottom)

    parts: list[str] = []
    ticks = round(top_value / step)
    for tick in range(ticks + 1):
        value = tick * step
        parts.append(
            f'<line class="grid" x1="{left}" x2="{width - right}" y1="{_fmt(y(value))}" '
            f'y2="{_fmt(y(value))}"/>'
            f'<text class="tick" x="{left - 6}" y="{_fmt(y(value) + 3.5)}" '
            f'text-anchor="end">{int(value):,}</text>'
        )
    parts.append(
        f'<line class="axis" x1="{left}" x2="{width - right}" y1="{height - bottom}" '
        f'y2="{height - bottom}"/>'
    )

    # Contiguous runs of weeks become one polyline each; a gap starts a new one.
    segments: list[list[int]] = []
    for index, point in enumerate(points):
        if index and points[index - 1].window_end == point.window_start:
            segments[-1].append(index)
        else:
            segments.append([index])
    for segment in segments:
        if len(segment) < 2:
            continue
        coordinates = " ".join(f"{_fmt(x(i))},{_fmt(y(points[i].value))}" for i in segment)
        parts.append(f'<polyline class="line {series}" points="{coordinates}"/>')

    for index, point in enumerate(points):
        cx, cy = _fmt(x(index)), _fmt(y(point.value))
        selected = " selected" if point.selected else ""
        parts.append(
            f'<a href="{escape(point.href, quote=True)}">'
            f"<title>{escape(point.title)}</title>"
            f'<circle class="hit" cx="{cx}" cy="{cy}" r="14"/>'
            + (
                f'<circle class="ring {series}" cx="{cx}" cy="{cy}" r="9.5"/>'
                if point.selected
                else ""
            )
            + f'<circle class="dot {series}{selected}" cx="{cx}" cy="{cy}" r="{5.5 if point.selected else 4}"/>'
            f'<text class="value" x="{cx}" y="{_fmt(y(point.value) - 11)}" '
            f'text-anchor="middle">{point.value:,}</text>'
            "</a>"
            f'<text class="tick{selected}" x="{cx}" y="{height - bottom + 16}" '
            f'text-anchor="middle">{escape(point.label)}</text>'
        )

    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{escape(label, quote=True)}">{"".join(parts)}</svg>'
    )
