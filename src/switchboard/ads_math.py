"""CTR, CPA, and anomaly flags. All figures are computed here, never by a model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

_SIX = Decimal("0.000001")
_TWO = Decimal("0.01")
_SPEND_SPIKE = Decimal("0.25")
_CTR_DROP = Decimal("-0.20")
_CPA_SPIKE = Decimal("0.30")


@dataclass(frozen=True)
class Totals:
    impressions: int
    clicks: int
    spend_cents: int
    conversions: int

    def ctr(self) -> str | None:
        if self.impressions <= 0:
            return None
        return _q(Decimal(self.clicks) / Decimal(self.impressions), _SIX)

    def cpa_cents(self) -> str | None:
        if self.conversions <= 0:
            return None
        return _q(Decimal(self.spend_cents) / Decimal(self.conversions), _TWO)

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "impressions": self.impressions,
            "clicks": self.clicks,
            "spend_cents": self.spend_cents,
            "conversions": self.conversions,
            "ctr": self.ctr(),
            "cpa_cents": self.cpa_cents(),
        }


def _q(value: Decimal, places: Decimal) -> str:
    return format(value.quantize(places, rounding=ROUND_HALF_EVEN), "f")


def sum_totals(rows: list[object]) -> Totals:
    impressions = clicks = spend = conversions = 0
    for row in rows:
        impressions += int(getattr(row, "impressions"))
        clicks += int(getattr(row, "clicks"))
        spend += int(getattr(row, "spend_cents"))
        conversions += int(getattr(row, "conversions"))
    return Totals(impressions, clicks, spend, conversions)


def previous_window(start: date, end: date) -> tuple[date, date]:
    if end < start:
        raise ValueError("end date is before start date")
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return prev_start, prev_end


def anomaly_flags(current: Totals, previous: Totals) -> list[str]:
    flags: list[str] = []
    if previous.spend_cents > 0:
        spend_change = (Decimal(current.spend_cents) - Decimal(previous.spend_cents)) / Decimal(previous.spend_cents)
        if spend_change > _SPEND_SPIKE:
            flags.append("spend_spike")
        elif spend_change < -_SPEND_SPIKE:
            flags.append("spend_drop")
    if current.impressions > 0 and previous.impressions > 0 and previous.clicks > 0:
        ctr_current = Decimal(current.clicks) / Decimal(current.impressions)
        ctr_previous = Decimal(previous.clicks) / Decimal(previous.impressions)
        ctr_change = (ctr_current - ctr_previous) / ctr_previous
        if ctr_change < _CTR_DROP:
            flags.append("ctr_drop")
    if current.conversions > 0 and previous.conversions > 0 and previous.spend_cents > 0:
        cpa_current = Decimal(current.spend_cents) / Decimal(current.conversions)
        cpa_previous = Decimal(previous.spend_cents) / Decimal(previous.conversions)
        if cpa_previous > 0:
            cpa_change = (cpa_current - cpa_previous) / cpa_previous
            if cpa_change > _CPA_SPIKE:
                flags.append("cpa_spike")
    if current.conversions == 0 and current.spend_cents > 0:
        flags.append("zero_conversion_spend")
    return sorted(flags)
