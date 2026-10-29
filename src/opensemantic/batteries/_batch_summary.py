"""Aggregation of per-cycle values over the members of a batch.

Pure helper without any UI or unit handling: the caller passes the values of
every member in one unit and converts the result afterwards.
"""

import math
from typing import List, Mapping, Optional, Sequence, Tuple

__all__ = ["aggregate_batch"]


def aggregate_batch(
    members: Sequence[Mapping[float, float]],
) -> List[Tuple[float, float, Optional[float], int]]:
    """Mean, sample standard deviation and count per cycle over batch members.

    ``members`` holds one ``{cycle number: value}`` mapping per member. A
    missing or NaN value is left out, so a cycle is averaged over the members
    that have a value for it. The result has one
    ``(cycle, mean, std, n)`` tuple per cycle with at least one value, sorted
    by cycle. ``std`` is the sample standard deviation (``ddof=1``) and
    ``None`` when ``n`` is 1.
    """
    per_cycle: dict = {}
    for member in members:
        for cycle, value in member.items():
            if value is None or not math.isfinite(value):
                continue
            per_cycle.setdefault(cycle, []).append(float(value))

    result: List[Tuple[float, float, Optional[float], int]] = []
    for cycle in sorted(per_cycle):
        values = per_cycle[cycle]
        n = len(values)
        mean = sum(values) / n
        std = (
            math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
            if n >= 2
            else None
        )
        result.append((cycle, mean, std, n))
    return result
