"""Reviewed waivers for known source gaps in the data-quality gate (``detect_anomalies``).

A waiver never weakens a detector: the finding is still computed and written to
``gold_dq_anomalies``, only with ``status = 'waived'`` and the waiver's reason, so it does not fail
the run. A waiver matches a **critical** finding exactly: same ``check_name``, same ``entity`` and
the finding's local (Europe/Berlin) date is in ``dates``. Waivers apply only while
``today <= expires``; an expired waiver is ignored (the finding fails the run again) and reported
as a warning.

The file is ``dq_waivers.yaml`` next to this module (package data, so it ships in the wheel).
A malformed file raises ``WaiverError``: the job fails, nothing is ignored silently. Add or remove
a waiver only by PR (docs/runbook.md "Known source gap (data-quality waiver)").
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from energy_lakehouse.anomalies import CHECK_SEVERITIES, CRITICAL, TEST_PREFIX
from energy_lakehouse.forecast import to_utc
from energy_lakehouse.transforms import LOCAL_TZ

WAIVERS_FILE = "dq_waivers.yaml"
STATUS_OPEN = "open"
STATUS_WAIVED = "waived"


class WaiverError(ValueError):
    """The waiver file is missing, not valid YAML, or violates the schema."""


class Waiver(BaseModel):
    """One reviewed exception: a check/entity on specific local dates, valid until ``expires``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check: str
    entity: str = Field(min_length=1)
    dates: tuple[date, ...] = Field(min_length=1)
    reason: str = Field(min_length=10)
    evidence: str = Field(min_length=10)
    approved_by: str = Field(min_length=1)
    expires: date

    @field_validator("check")
    @classmethod
    def _known_check(cls, value: str) -> str:
        if value not in CHECK_SEVERITIES:
            raise ValueError(f"unknown check {value!r}; known: {', '.join(CHECK_SEVERITIES)}")
        if CRITICAL not in CHECK_SEVERITIES[value]:
            raise ValueError(f"check {value!r} never emits critical findings: nothing to waive")
        return value

    @field_validator("entity")
    @classmethod
    def _real_entity(cls, value: str) -> str:
        if value != value.strip() or value.startswith(TEST_PREFIX):
            raise ValueError(f"entity {value!r}: no surrounding spaces, no {TEST_PREFIX} entities")
        return value

    @field_validator("reason", "evidence", "approved_by")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def _consistent_dates(self) -> "Waiver":
        if len(set(self.dates)) != len(self.dates):
            raise ValueError("dates must be unique")
        if self.expires < max(self.dates):
            raise ValueError(f"expires {self.expires} is before the last waived date")
        return self

    def is_active(self, today: date) -> bool:
        """Valid through ``expires`` (inclusive, local date)."""
        return today <= self.expires

    def label(self) -> str:
        days = ", ".join(d.isoformat() for d in self.dates)
        return f"{self.check} {self.entity} [{days}] (expires {self.expires})"


class WaiverFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    waivers: list[Waiver]

    @model_validator(mode="after")
    def _no_overlap(self) -> "WaiverFile":
        seen: set[tuple[str, str, date]] = set()
        for waiver in self.waivers:
            for day in waiver.dates:
                key = (waiver.check, waiver.entity, day)
                if key in seen:
                    raise ValueError(f"{key[0]} {key[1]} {day} is waived more than once")
                seen.add(key)
        return self


def default_waivers_path() -> Traversable:
    """``dq_waivers.yaml`` of the installed package (wheel) or the source checkout."""
    return resources.files("energy_lakehouse") / WAIVERS_FILE


def parse_waivers(text: str, source: str = WAIVERS_FILE) -> list[Waiver]:
    """Validate the YAML text of a waiver file; ``WaiverError`` on any problem."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WaiverError(f"{source}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise WaiverError(f"{source}: expected a mapping with key 'waivers'")
    try:
        return WaiverFile.model_validate(data).waivers
    except ValidationError as exc:
        raise WaiverError(f"{source}: invalid waiver file:\n{exc}") from exc


def load_waivers(path: Traversable | str | None = None) -> list[Waiver]:
    """Waivers from ``path`` (default: the packaged ``dq_waivers.yaml``); a missing file fails."""
    if path is None:
        path = default_waivers_path()
    elif isinstance(path, str):
        path = Path(path)
    if not path.is_file():
        raise WaiverError(f"waiver file not found: {path}")
    return parse_waivers(path.read_text(encoding="utf-8"), source=str(path))


def expired_waivers(waivers: Iterable[Waiver], today: date) -> list[Waiver]:
    return [w for w in waivers if not w.is_active(today)]


@dataclass
class WaiverResult:
    """Anomaly rows with ``status``/``waiver_reason`` plus messages for the run log."""

    rows: pd.DataFrame
    waived: list[str] = field(default_factory=list)  # one "WAIVED: ..." line per waived row
    warnings: list[str] = field(default_factory=list)  # expired waivers (and what they hit)


def _local_dates(ts_utc: pd.Series) -> pd.Series:
    return to_utc(ts_utc).dt.tz_convert(LOCAL_TZ).dt.date


def apply_waivers(anomalies: pd.DataFrame, waivers: Sequence[Waiver], today: date) -> WaiverResult:
    """Mark critical rows that exactly match an active waiver as ``waived``; all others ``open``.

    Match: ``check_name == check``, ``entity == entity`` and the local date of ``ts_utc`` in
    ``dates``. Warnings are never waived (they do not fail the run). Expired waivers are not
    applied; each produces a warning, naming the critical rows it would have matched.
    """
    rows = anomalies.copy()
    rows["status"] = STATUS_OPEN
    rows["waiver_reason"] = None
    result = WaiverResult(rows)
    local_dates = _local_dates(rows["ts_utc"]) if not rows.empty else pd.Series(dtype=object)
    critical = rows["severity"] == CRITICAL
    for waiver in waivers:
        hit = (
            critical
            & (rows["check_name"] == waiver.check)
            & (rows["entity"] == waiver.entity)
            & local_dates.isin(waiver.dates)
        )
        if not waiver.is_active(today):
            note = (
                f", would match {int(hit.sum())} critical row(s) (not waived)" if hit.any() else ""
            )
            result.warnings.append(f"expired waiver ignored: {waiver.label()}{note}")
            continue
        rows.loc[hit, "status"] = STATUS_WAIVED
        rows.loc[hit, "waiver_reason"] = waiver.reason
        for r, day in zip(rows[hit].itertuples(index=False), local_dates[hit], strict=True):
            result.waived.append(
                f"WAIVED: {r.check_name} {r.entity} {day} ({r.reason}); waiver: {waiver.reason} "
                f"[approved by {waiver.approved_by}, expires {waiver.expires}]"
            )
    return result
