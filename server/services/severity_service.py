from __future__ import annotations

from typing import Iterable, Optional

SEVERITY_RANK = {
    "NONE": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "MID": 2,
    "HIGH": 3,
    "UNKNOWN": 3,
    "CRITICAL": 4,
}


def canonical_severity(value: object) -> str:
    normalized = str(value or "NONE").strip().upper()
    if normalized == "MID":
        return "MEDIUM"
    return normalized if normalized in SEVERITY_RANK else "NONE"


def severity_rank(value: object) -> int:
    return SEVERITY_RANK[canonical_severity(value)]


def highest_severity(values: Iterable[object], *, empty: Optional[str] = None) -> Optional[str]:
    canonical = [canonical_severity(value) for value in values]
    if not canonical:
        return empty
    return max(canonical, key=severity_rank)
