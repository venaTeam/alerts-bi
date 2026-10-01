"""Each schema's repeat interval for a still-firing Grafana alert (design section 1.1).

Shared by R6 (src/rules) and the summary's fire-rate display (src/insights), so both read
one definition. v1 re-fires every 5 minutes and v2 every 12 hours: a 144x gap.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

REPEAT_INTERVAL: Final[dict[str, timedelta]] = {
    "v1": timedelta(minutes=5),
    "v2": timedelta(hours=12),
}
