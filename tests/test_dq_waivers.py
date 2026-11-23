"""Offline tests for energy_lakehouse.dq_waivers (reviewed waivers for known source gaps)."""

from datetime import date, timedelta

import pandas as pd
import pytest

from energy_lakehouse import anomalies as an
from energy_lakehouse import dq_waivers as dw
from energy_lakehouse.transforms import GOLD_DQ_ANOMALIES_COLUMNS

TODAY = date(2026, 11, 23)
GAP_DAYS = [date(2026, 11, 20), date(2026, 11, 21)]

VALID_ENTRY = """\
  - check: completeness
    entity: day_ahead_price
    dates: [2026-11-20, 2026-11-21]
    reason: SMARD published no day-ahead prices for these days
    evidence: SMARD filter 4169 hour 0 of 24, quarterhour 0 of 96 filled
    approved_by: Data Owner
    expires: 2026-12-31
"""


def waivers(expires: str = "2026-12-31") -> list[dw.Waiver]:
    return dw.parse_waivers("waivers:\n" + VALID_ENTRY.replace("2026-12-31", expires))


def completeness_row(entity: str, day: date, severity: str = an.CRITICAL) -> dict:
    return {
        "check_name": an.COMPLETENESS,
        "entity": entity,
        "ts_utc": an.local_day_start(day),  # 23:00 UTC of the previous day in winter
        "value": 0.0,
        "expected": 24.0,
        "lower": 22.8,
        "upper": 24.0,
        "score": 100.0,
        "severity": severity,
        "reason": f"24 of 24 hours missing on {day} (100%)",
    }


def rows(*records: dict) -> pd.DataFrame:
    return an.combine([an._frame(records)])


# --- matching ---------------------------------------------------------------------------------


def test_exact_match_is_waived_not_raised():
    found = rows(*(completeness_row("day_ahead_price", d) for d in GAP_DAYS))
    assert len(an.critical_failures(found)) == 2  # without waivers both fail

    result = dw.apply_waivers(found, waivers(), TODAY)

    assert an.critical_failures(result.rows) == []
    assert list(result.rows["status"]) == [dw.STATUS_WAIVED] * 2
    assert set(result.rows["waiver_reason"]) == {
        "SMARD published no day-ahead prices for these days"
    }
    assert len(result.waived) == 2
    assert result.waived[0].startswith("WAIVED: completeness day_ahead_price 2026-11-2")
    assert "approved by Data Owner, expires 2026-12-31" in result.waived[0]
    assert result.warnings == []


@pytest.mark.parametrize(
    "record",
    [
        completeness_row("day_ahead_price", date(2026, 11, 19)),  # other date
        completeness_row("day_ahead_price", date(2026, 11, 22)),  # other date
        completeness_row("total_load", date(2026, 11, 20)),  # other series
        {**completeness_row("day_ahead_price", date(2026, 11, 20)), "check_name": an.DRIFT_MAPE},
    ],
    ids=["date-before", "date-after", "other-series", "other-check"],
)
def test_non_matching_critical_still_raises(record):
    found = rows(record, completeness_row("day_ahead_price", GAP_DAYS[0]))
    result = dw.apply_waivers(found, waivers(), TODAY)
    failures = an.critical_failures(result.rows)
    assert len(failures) == 1
    assert failures[0].startswith(f"{record['check_name']} {record['entity']}")
    assert len(result.waived) == 1


def test_match_uses_local_date_not_utc_date():
    # 2026-11-20 local starts at 2026-11-19 23:00 UTC: matched as the 20th, not the 19th.
    row = completeness_row("day_ahead_price", GAP_DAYS[0])
    assert row["ts_utc"].date() == date(2026, 11, 19)
    result = dw.apply_waivers(rows(row), waivers(), TODAY)
    assert list(result.rows["status"]) == [dw.STATUS_WAIVED]


def test_warnings_are_never_waived():
    found = rows(completeness_row("day_ahead_price", GAP_DAYS[0], severity=an.WARNING))
    result = dw.apply_waivers(found, waivers(), TODAY)
    assert list(result.rows["status"]) == [dw.STATUS_OPEN]
    assert result.rows["waiver_reason"].isna().all()
    assert result.waived == []


def test_expired_waiver_raises_again_and_warns():
    found = rows(completeness_row("day_ahead_price", GAP_DAYS[0]))
    result = dw.apply_waivers(found, waivers(expires="2026-11-22"), TODAY)
    assert len(an.critical_failures(result.rows)) == 1
    assert list(result.rows["status"]) == [dw.STATUS_OPEN]
    assert result.waived == []
    assert len(result.warnings) == 1
    assert result.warnings[0].startswith("expired waiver ignored: completeness day_ahead_price")
    assert "would match 1 critical row(s) (not waived)" in result.warnings[0]


def test_expired_waiver_without_match_still_warns():
    result = dw.apply_waivers(an.empty_anomalies(), waivers(expires="2026-11-22"), TODAY)
    assert result.warnings and "would match" not in result.warnings[0]
    assert result.rows.empty and {"status", "waiver_reason"} <= set(result.rows.columns)


def test_expires_is_inclusive():
    (waiver,) = waivers(expires="2026-11-23")
    assert waiver.is_active(TODAY)
    assert not waiver.is_active(TODAY + timedelta(days=1))
    assert dw.expired_waivers([waiver], TODAY + timedelta(days=1)) == [waiver]


def test_test_entities_stay_excluded_and_open():
    found = rows(completeness_row("TEST:total_load", GAP_DAYS[0]))
    result = dw.apply_waivers(found, waivers(), TODAY)
    assert an.critical_failures(result.rows) == []
    assert list(result.rows["status"]) == [dw.STATUS_OPEN]


def test_real_completeness_gap_is_waived_by_packaged_file():
    """End to end: the 2026-11-20/21 price gap from completeness(), packaged waiver file."""
    hours = pd.date_range("2026-11-14 23:00", "2026-11-22 22:00", freq="h", tz="UTC")
    local_day = hours.tz_convert("Europe/Berlin").date
    df = pd.concat(
        [
            pd.DataFrame({"entity": "total_load", "ts_utc": hours, "value": 50000.0}),
            pd.DataFrame({"entity": "day_ahead_price", "ts_utc": hours, "value": 90.0}).loc[
                ~pd.Series(local_day).isin(GAP_DAYS).to_numpy()
            ],
        ]
    )
    found = an.combine([an.completeness(df, TODAY)])
    assert len(an.critical_failures(found)) == 2

    result = dw.apply_waivers(found, dw.load_waivers(), TODAY)
    assert an.critical_failures(result.rows) == []
    assert len(result.waived) == 2
    # After expiry the gate fails again.
    later = dw.apply_waivers(found, dw.load_waivers(), date(2027, 1, 1))
    assert len(an.critical_failures(later.rows)) == 2 and later.warnings


# --- the waiver file --------------------------------------------------------------------------


def test_packaged_waiver_file_is_valid():
    path = dw.default_waivers_path()
    assert path.name == dw.WAIVERS_FILE and path.is_file()
    loaded = dw.load_waivers()
    first = loaded[0]
    assert (first.check, first.entity, list(first.dates)) == (
        an.COMPLETENESS,
        "day_ahead_price",
        GAP_DAYS,
    )
    assert first.expires == date(2026, 12, 31)
    assert "SMARD published no day-ahead prices" in first.reason


def test_empty_waiver_list_is_valid():
    assert dw.parse_waivers("waivers: []") == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("waivers: [", "invalid YAML"),
        ("", "expected a mapping"),
        ("- check: completeness", "expected a mapping"),
        ("other: []", "waivers"),
        ("waivers:\n" + VALID_ENTRY.replace("    expires: 2026-12-31\n", ""), "expires"),
        (
            "waivers:\n"
            + VALID_ENTRY.replace(
                "    reason: SMARD published no day-ahead prices for these days\n", ""
            ),
            "reason",
        ),
        (
            "waivers:\n"
            + VALID_ENTRY.replace(
                "SMARD published no day-ahead prices for these days", '"            "'
            ),
            "blank",
        ),
        ("waivers:\n" + VALID_ENTRY.replace("    approved_by: Data Owner\n", ""), "approved_by"),
        (
            "waivers:\n"
            + VALID_ENTRY.replace(
                "SMARD filter 4169 hour 0 of 24, quarterhour 0 of 96 filled", "see SMARD"
            ),
            "evidence",
        ),
        ("waivers:\n" + VALID_ENTRY.replace("2026-12-31", "not-a-date"), "expires"),
        ("waivers:\n" + VALID_ENTRY.replace("2026-12-31", "2026-11-20"), "before the last"),
        ("waivers:\n" + VALID_ENTRY.replace("[2026-11-20, 2026-11-21]", "[]"), "dates"),
        (
            "waivers:\n" + VALID_ENTRY.replace("2026-11-21]", "2026-11-20]"),
            "dates must be unique",
        ),
        ("waivers:\n" + VALID_ENTRY.replace("check: completeness", "check: nope"), "unknown"),
        (
            "waivers:\n" + VALID_ENTRY.replace("check: completeness", "check: seasonal_robust_z"),
            "never emits critical",
        ),
        ("waivers:\n" + VALID_ENTRY.replace("day_ahead_price", "TEST:total_load"), "TEST:"),
        ("waivers:\n" + VALID_ENTRY + "    ticket: X-1\n", "Extra inputs"),
        ("waivers:\n" + VALID_ENTRY + VALID_ENTRY, "waived more than once"),
    ],
    ids=[
        "bad-yaml",
        "empty",
        "list",
        "no-waivers-key",
        "no-expires",
        "no-reason",
        "blank-reason",
        "no-approver",
        "short-evidence",
        "bad-date",
        "expires-before-dates",
        "no-dates",
        "duplicate-dates",
        "unknown-check",
        "warning-only-check",
        "test-entity",
        "unknown-key",
        "overlap",
    ],
)
def test_invalid_waiver_file_fails(text, message):
    with pytest.raises(dw.WaiverError, match=message):
        dw.parse_waivers(text)


def test_load_waivers_missing_or_invalid_file(tmp_path):
    with pytest.raises(dw.WaiverError, match="not found"):
        dw.load_waivers(str(tmp_path / "missing.yaml"))
    bad = tmp_path / "dq_waivers.yaml"
    bad.write_text("waivers:\n  - check: completeness\n", encoding="utf-8")
    with pytest.raises(dw.WaiverError, match="invalid waiver file"):
        dw.load_waivers(str(bad))


# --- gold_dq_anomalies columns ----------------------------------------------------------------


def test_to_spark_rows_carries_status_and_defaults_to_open():
    detected_at = pd.Timestamp("2026-11-23 05:20", tz="UTC")
    found = rows(*(completeness_row("day_ahead_price", d) for d in [*GAP_DAYS, TODAY]))
    waived = an.to_spark_rows(dw.apply_waivers(found, waivers(), TODAY).rows, detected_at)
    assert list(waived.columns) == [name for name, _, _ in GOLD_DQ_ANOMALIES_COLUMNS]
    assert sorted(waived["status"]) == ["open", "waived", "waived"]
    assert waived.loc[waived["status"] == "open", "waiver_reason"].tolist() == [None]

    plain = an.to_spark_rows(found, detected_at)  # no apply_waivers: everything open
    assert set(plain["status"]) == {"open"} and plain["waiver_reason"].isna().all()


def test_add_missing_columns_sql():
    fqn = "`workspace`.`energy_dev`.`gold_dq_anomalies`"
    current = [name for name, _, _ in GOLD_DQ_ANOMALIES_COLUMNS]
    assert an.add_missing_columns_sql(fqn, current) == []
    assert an.add_missing_columns_sql(fqn, [c.upper() for c in current]) == []
    old = [c for c in current if c not in ("status", "waiver_reason")]
    alter, update = an.add_missing_columns_sql(fqn, old)
    assert alter.startswith(f"ALTER TABLE {fqn} ADD COLUMNS (status STRING COMMENT 'open, or")
    assert ", waiver_reason STRING COMMENT 'Reason of the matching waiver" in alter
    assert update == f"UPDATE {fqn} SET status = 'open' WHERE status IS NULL"


def test_llm_prompt_marks_waived_rows():
    top = [
        {
            "check_name": an.COMPLETENESS,
            "entity": "day_ahead_price",
            "ts_utc": "2026-11-19 23:00:00",
            "severity": an.CRITICAL,
            "status": dw.STATUS_WAIVED,
            "reason": "24 of 24 hours missing",
        }
    ]
    prompt = an.llm_prompt(TODAY, [], top)
    assert "- [critical, waived] completeness day_ahead_price at 2026-11-19 23:00:00 UTC" in prompt
    assert "'waived' marks a known, reviewed source gap" in prompt
    assert "status" in an.top_anomalies_sql("a", TODAY)
