"""Ads math edges, currency micros, date boundaries, and hypothesis properties."""

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from switchboard.ads_math import Totals, anomaly_flags, previous_window, sum_totals
from switchboard.errors import SwitchboardError
from switchboard.live import _cents_from_dollars, _cents_from_micros, google_rows_from_payload
from switchboard.models import MetricDay
from tests.conftest import principal


def _row(impressions, clicks, spend, conversions, day="2026-03-01", campaign="cmp"):
    return MetricDay(
        tenant_id="halden-bikes",
        campaign_id=campaign,
        date=day,
        impressions=impressions,
        clicks=clicks,
        spend_cents=spend,
        conversions=conversions,
    )


def test_zero_clicks_has_zero_ctr_and_no_cpa():
    totals = Totals(impressions=500, clicks=0, spend_cents=200, conversions=0)
    assert totals.ctr() == "0.000000"
    assert totals.cpa_cents() is None
    assert anomaly_flags(totals, Totals(0, 0, 0, 0)) == ["zero_conversion_spend"]


def test_zero_impressions_has_no_ctr():
    totals = Totals(impressions=0, clicks=0, spend_cents=0, conversions=0)
    assert totals.ctr() is None
    assert totals.cpa_cents() is None
    # Spend fell from 100 cents to 0, which is a drop past the 25% threshold.
    # CTR is not compared when the current window has no impressions.
    assert anomaly_flags(totals, Totals(100, 10, 100, 1)) == ["spend_drop"]


async def test_zero_click_campaign_row(runtime):
    result = await runtime.gateway.invoke(
        "ads_campaign_metrics",
        {
            "start_date": "2026-03-01",
            "end_date": "2026-03-01",
            "platform": "google",
            "campaign_id": "cmp_halden_noclick",
        },
        principal=principal(),
    )
    current = result["data"]["current"]
    assert current["impressions"] == 500
    assert current["clicks"] == 0
    assert current["ctr"] == "0.000000"
    assert current["cpa_cents"] is None
    assert "zero_conversion_spend" in result["data"]["flags"]


@pytest.mark.parametrize(
    ("micros", "cents"),
    [
        (0, 0),
        (10_000, 1),
        (1_000_000, 100),
        (5_000, 0),
        (15_000, 2),
        (25_000, 2),
        (35_000, 4),
    ],
)
def test_google_micros_convert_with_half_even(micros, cents):
    assert _cents_from_micros(micros) == cents


def test_google_payload_uses_micros_and_keeps_the_calendar_date():
    rows = google_rows_from_payload(
        {
            "results": [
                {
                    "campaign": {"id": "99"},
                    "segments": {"date": "2026-03-08"},
                    "metrics": {"impressions": "10", "clicks": "0", "costMicros": "15000", "conversions": 0},
                }
            ]
        },
        "halden-bikes",
    )
    assert rows[0].date == "2026-03-08"
    assert rows[0].spend_cents == 2
    assert rows[0].clicks == 0


@pytest.mark.parametrize(
    ("amount", "cents"),
    [("0", 0), ("1.00", 100), ("1.005", 100), ("1.015", 102), ("12.345", 1234)],
)
def test_meta_spend_string_to_cents(amount, cents):
    assert _cents_from_dollars(amount) == cents


@pytest.mark.parametrize(
    "value",
    [
        "2026-03-08T23:00:00-05:00",
        "2026-03-08T04:00:00Z",
        "2026-03-08 00:00:00",
        "03/08/2026",
        "2026-3-8",
    ],
)
async def test_zoned_or_ambiguous_dates_are_rejected(runtime, value):
    with pytest.raises(SwitchboardError) as caught:
        await runtime.gateway.invoke(
            "ads_campaign_metrics",
            {"start_date": value, "end_date": "2026-03-08", "platform": "google"},
            principal=principal(),
        )
    assert caught.value.code == "validation"


async def test_inclusive_range_includes_both_ends_and_not_the_next_day(runtime):
    inside = await runtime.gateway.invoke(
        "ads_campaign_metrics",
        {
            "start_date": "2026-02-16",
            "end_date": "2026-02-16",
            "platform": "google",
            "campaign_id": "cmp_halden_brand",
        },
        principal=principal(),
    )
    assert inside["data"]["row_count"] == 1
    assert inside["data"]["previous_start_date"] == "2026-02-15"
    assert inside["data"]["previous_end_date"] == "2026-02-15"
    after = await runtime.gateway.invoke(
        "ads_campaign_metrics",
        {
            "start_date": "2026-03-16",
            "end_date": "2026-03-16",
            "platform": "google",
            "campaign_id": "cmp_halden_brand",
        },
        principal=principal(),
    )
    assert after["data"]["row_count"] == 0
    assert after["data"]["current"]["spend_cents"] == 0


def test_previous_window_length_matches_and_stops_the_day_before():
    start, end = previous_window(date(2026, 3, 2), date(2026, 3, 8))
    assert (end - start).days == 6
    assert end == date(2026, 3, 1)
    assert start == date(2026, 2, 23)


@given(
    impressions=st.integers(min_value=0, max_value=5_000_000),
    clicks=st.integers(min_value=0, max_value=5_000_000),
    spend=st.integers(min_value=0, max_value=50_000_000),
    conversions=st.integers(min_value=0, max_value=100_000),
)
@settings(max_examples=40, deadline=None)
def test_ratios_match_decimal_half_even(impressions, clicks, spend, conversions):
    totals = Totals(impressions, clicks, spend, conversions)
    if impressions == 0:
        assert totals.ctr() is None
    else:
        expected = format(
            (Decimal(clicks) / Decimal(impressions)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN),
            "f",
        )
        assert totals.ctr() == expected
    if conversions == 0:
        assert totals.cpa_cents() is None
    else:
        expected = format(
            (Decimal(spend) / Decimal(conversions)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN),
            "f",
        )
        assert totals.cpa_cents() == expected


@given(
    parts=st.lists(
        st.tuples(
            st.integers(0, 1000),
            st.integers(0, 1000),
            st.integers(0, 10000),
            st.integers(0, 50),
        ),
        min_size=0,
        max_size=12,
    )
)
@settings(max_examples=25, deadline=None)
def test_sum_totals_is_the_sum_of_the_parts(parts):
    rows = [_row(*part, day=f"2026-03-{(index % 28) + 1:02d}") for index, part in enumerate(parts)]
    totals = sum_totals(rows)
    assert totals.impressions == sum(part[0] for part in parts)
    assert totals.clicks == sum(part[1] for part in parts)
    assert totals.spend_cents == sum(part[2] for part in parts)
    assert totals.conversions == sum(part[3] for part in parts)


@given(
    current=st.tuples(st.integers(0, 10000), st.integers(0, 10000), st.integers(0, 100000), st.integers(0, 100)),
    previous=st.tuples(st.integers(0, 10000), st.integers(0, 10000), st.integers(0, 100000), st.integers(0, 100)),
)
@settings(max_examples=40, deadline=None)
def test_flags_are_sorted_and_zero_spend_is_exact(current, previous):
    flags = anomaly_flags(Totals(*current), Totals(*previous))
    assert flags == sorted(flags)
    assert ("zero_conversion_spend" in flags) is (current[3] == 0 and current[2] > 0)


@pytest.mark.parametrize(
    ("label", "raw", "needle"),
    [
        ("email", {"query": "mara.ellison@haldenbikes.com"}, "mara.ellison@haldenbikes.com"),
        ("phone", {"query": "612-555-0148"}, "612-555-0148"),
        ("key", {"query": "sk-live-abcdefghij"}, "sk-live-abcdefghij"),
        ("bearer", {"query": "Bearer sb1.aaaa.bbbb"}, "sb1.aaaa.bbbb"),
    ],
)
async def test_audit_redacts_argument_shapes(runtime, label, raw, needle):
    await runtime.gateway.invoke("crm_search_contacts", raw, principal=principal())
    blob = runtime.audit.dump_text()
    assert needle not in blob
    assert label


@pytest.mark.parametrize("name", ["crm_search_contacts", "docs_search", "ads_list_campaigns", "slack_list_channels"])
async def test_rate_limit_is_per_tool(name):
    from switchboard.ratelimit import RateLimiter

    clock = {"now": 0.0}
    limiter = RateLimiter(1, 30, clock=lambda: clock["now"])
    assert limiter.check("halden-bikes", name) is None
    retry = limiter.check("halden-bikes", name)
    assert retry is not None and retry > 0
    assert limiter.check("sable-rye", name) is None
