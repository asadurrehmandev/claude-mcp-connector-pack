"""Exact aggregates. The expected strings are calculated by hand, not by the module under test."""

import pytest

from switchboard.ads_math import Totals, anomaly_flags
from tests.conftest import principal


def test_hand_calculated_ratios_and_flags():
    # Previous window: 7 days * (1000 impr, 25 clicks, 4000 cents, 2 conv)
    #   impressions 7000, clicks 175, spend 28000, conversions 14
    #   CTR 175/7000 = 0.025, CPA 28000/14 = 2000 cents
    # Current window: 6 days * (1000, 19, 5000, 1) + 1 day * (1000, 19, 6000, 1)
    #   impressions 7000, clicks 133, spend 36000, conversions 7
    #   CTR 133/7000 = 0.019, CPA 36000/7 = 5142.857... -> 5142.86 half-even
    #   spend change 8000/28000 = 0.2857... > 0.25
    #   CTR change (0.019-0.025)/0.025 = -0.24 < -0.20
    #   CPA change (5142.857-2000)/2000 = 1.571... > 0.30
    previous = Totals(7000, 175, 28000, 14)
    current = Totals(7000, 133, 36000, 7)
    assert previous.ctr() == "0.025000"
    assert previous.cpa_cents() == "2000.00"
    assert current.ctr() == "0.019000"
    assert current.cpa_cents() == "5142.86"
    assert anomaly_flags(current, previous) == ["cpa_spike", "ctr_drop", "spend_spike"]


def test_zero_conversion_flag_without_a_spend_spike():
    flags = anomaly_flags(Totals(800, 10, 1500, 0), Totals(0, 0, 0, 0))
    assert flags == ["zero_conversion_spend"]


@pytest.mark.asyncio
async def test_campaign_tool_matches_the_hand_calculation(runtime):
    result = await runtime.gateway.invoke(
        "ads_campaign_metrics",
        {
            "start_date": "2026-03-02",
            "end_date": "2026-03-08",
            "platform": "google",
            "campaign_id": "cmp_halden_exact",
        },
        principal=principal(),
    )
    data = result["data"]
    assert data["current"] == {
        "impressions": 7000,
        "clicks": 133,
        "spend_cents": 36000,
        "conversions": 7,
        "ctr": "0.019000",
        "cpa_cents": "5142.86",
    }
    assert data["previous"]["ctr"] == "0.025000"
    assert data["previous"]["cpa_cents"] == "2000.00"
    assert data["previous"]["spend_cents"] == 28000
    assert data["flags"] == ["cpa_spike", "ctr_drop", "spend_spike"]
    assert data["row_count"] == 7

    zero = await runtime.gateway.invoke(
        "ads_campaign_metrics",
        {
            "start_date": "2026-03-10",
            "end_date": "2026-03-10",
            "platform": "google",
            "campaign_id": "cmp_bright_zero",
        },
        principal=principal("brightline-dental"),
    )
    assert zero["data"]["flags"] == ["zero_conversion_spend"]
    assert zero["data"]["current"]["spend_cents"] == 1500
    assert zero["data"]["current"]["cpa_cents"] is None


@pytest.mark.asyncio
async def test_other_tenant_cannot_read_the_fixture_campaign(runtime):
    with pytest.raises(Exception) as exc:
        await runtime.gateway.invoke(
            "ads_campaign_metrics",
            {
                "start_date": "2026-03-02",
                "end_date": "2026-03-08",
                "campaign_id": "cmp_halden_exact",
            },
            principal=principal("sable-rye"),
        )
    assert exc.value.code == "not_found"
