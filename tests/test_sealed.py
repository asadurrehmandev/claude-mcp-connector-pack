"""The sealed file is hashed. These numbers are whatever this tree measures."""

from pathlib import Path

import pytest

from switchboard.eval_harness import assert_sealed, evaluate

ROOT = Path(__file__).resolve().parents[1]


def test_sealed_file_matches_its_hash():
    digest = assert_sealed(ROOT / "qa" / "sealed_tasks.json")
    assert len(digest) == 64


@pytest.mark.asyncio
async def test_sealed_eval_records_every_category(runtime):
    report = await evaluate(runtime, ROOT / "qa" / "sealed_tasks.json")
    assert report["held_out_tasks"] >= 80
    assert report["sealed_sha256"]
    assert sum(bucket["tasks"] for bucket in report["categories"].values()) == report["held_out_tasks"]
    attacks = [name for name in report["categories"] if name.startswith("attack_")]
    assert sum(report["categories"][name]["tasks"] for name in attacks) >= 20
    # The file is frozen. A miss stays a miss; this assertion is the measurement, not a rewrite.
    assert report["failures"] == []
    assert report["tool_selection_correct"] == report["held_out_tasks"]
    assert report["argument_correct"] == report["held_out_tasks"]
