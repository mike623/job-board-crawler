"""Turn the free-text salary each board prints into something sortable.

Boards state pay in whatever shape the advertiser typed: ranges, floors, ceilings, bare
numbers, "k" shorthand, and trailing prose about benefits. The original text is always kept;
these structured fields exist so the dashboard can sort and filter on pay.

Anything without a number in it — "Competitive", "Salary negotiable" — yields empty fields
rather than a guess.
"""
from __future__ import annotations

import re

# A money amount, optionally prefixed with a currency symbol and suffixed with "k".
_AMOUNT = r"£?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*([kK])?"

# "£60,000 - £70,000", "£70k - 85k", "From £55,000 to £85,000", "71,250-118,000".
_RANGE = re.compile(_AMOUNT + r"\s*(?:-|–|—|\bto\b)\s*" + _AMOUNT)
_SINGLE = re.compile(_AMOUNT)

# Percentages ("+ 10% employer pension") are not pay; drop them before looking for amounts.
_PERCENT = re.compile(r"\d[\d,.]*\s*%")

# Contractor adverts abbreviate heavily: "£395p/d", "£55ph", "£90k p.a.". The abbreviation often
# butts straight against the digits, so \b cannot be used to anchor its left edge — a lookbehind
# that rejects letters but allows digits is what distinguishes "55ph" from the "ph" inside a word.
_ABBREV = r"(?<![A-Za-z])p\s*[./]?\s*{}\.?(?![A-Za-z])"
_PERIODS = [
    (re.compile(r"per\s+day|\bday\s*rate\b|\bdaily\b|" + _ABBREV.format("d"), re.I), "day"),
    (re.compile(r"per\s+hour|\bhourly\b|" + _ABBREV.format("h"), re.I), "hour"),
    (re.compile(r"per\s+annum|per\s+year|\bannual(?:ly)?\b|" + _ABBREV.format("a"), re.I), "year"),
    (re.compile(r"per\s+week|\bweekly\b", re.I), "week"),
    (re.compile(r"per\s+month|\bmonthly\b", re.I), "month"),
]

# "Up to £80,000" is a ceiling; "From £55,000" is a floor. Only meaningful for a lone amount.
_CEILING = re.compile(r"\b(?:up\s+to|max(?:imum)?|to)\b", re.I)
_FLOOR = re.compile(r"\b(?:from|start(?:ing)?\s+(?:at|from)|min(?:imum)?|\+)\b", re.I)

# Below this, a bare unlabelled number could plausibly be a day or hourly rate, so the period
# is left unstated rather than assumed.
_ANNUAL_FLOOR = 10_000


def _to_int(number: str, k_suffix: str | None, k_implied: bool = False) -> int:
    value = float(number.replace(",", ""))
    # "£70k - 85k" marks only the first value; the second inherits the shorthand.
    if k_suffix or (k_implied and value < 1000):
        value *= 1000
    return int(round(value))


def _period(text: str) -> str:
    for pattern, name in _PERIODS:
        if pattern.search(text):
            return name
    return ""


def parse_salary(text: str | None) -> dict:
    """Extract a minimum, maximum and period from a board's salary text.

    Returns keys `salary_min`, `salary_max` and `salary_period`. A missing bound is None —
    "Up to £80,000" has no minimum, "From £55,000" has no maximum.
    """
    empty = {"salary_min": None, "salary_max": None, "salary_period": ""}
    text = (text or "").strip()
    if not text:
        return empty

    scannable = _PERCENT.sub(" ", text)
    period = _period(text)

    match = _RANGE.search(scannable)
    if match:
        low_num, low_k, high_num, high_k = match.groups()
        k_anywhere = bool(low_k or high_k)
        low = _to_int(low_num, low_k, k_anywhere)
        high = _to_int(high_num, high_k, k_anywhere)
        if low > high:
            low, high = high, low
        return {
            "salary_min": low,
            "salary_max": high,
            "salary_period": period or _infer_period(high),
        }

    match = _SINGLE.search(scannable)
    if not match:
        # "Competitive", "Salary negotiable", "Training Course" — nothing to extract.
        return empty

    amount = _to_int(match.group(1), match.group(2))
    period = period or _infer_period(amount)
    before = scannable[:match.start()]
    if _CEILING.search(before):
        return {"salary_min": None, "salary_max": amount, "salary_period": period}
    if _FLOOR.search(before):
        return {"salary_min": amount, "salary_max": None, "salary_period": period}
    return {"salary_min": amount, "salary_max": amount, "salary_period": period}


def _infer_period(amount: int) -> str:
    """A five-figure unlabelled sum is annual; anything smaller is left unstated."""
    return "year" if amount >= _ANNUAL_FLOOR else ""


def sort_key(lead) -> tuple[int, int]:
    """Order leads by advertised pay, for use with `reverse=True`.

    Adverts stating no figure sort last rather than as zero. Day and hourly rates are compared
    on their face value, so they land below annual figures — converting between periods would
    mean inventing a working-day count the advert never stated.
    """
    top = getattr(lead, "salary_max", None) or getattr(lead, "salary_min", None)
    return (0 if top is None else 1, top or 0)


def apply_to(lead) -> None:
    """Populate a lead's structured salary fields from its salary text, in place."""
    for key, value in parse_salary(getattr(lead, "salary", "")).items():
        setattr(lead, key, value)
