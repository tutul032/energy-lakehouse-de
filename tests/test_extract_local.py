from datetime import date

import pytest

from energy_lakehouse import extract_local, smard, weather

OK = {"written": 1, "skipped": 0, "failed": 0}
WEATHER_ARCHIVE = ["--source", "weather", "--mode", "archive"]


def fake_extractors(monkeypatch, smard_result=OK, weather_result=OK):
    calls = {}

    def fake_smard(base_dir, series_list, resolution, n_latest, overwrite=False, refresh_latest=2):
        calls["smard"] = dict(
            base_dir=base_dir,
            resolution=resolution,
            n=n_latest,
            overwrite=overwrite,
            refresh_latest=refresh_latest,
        )
        return smard_result

    def fake_weather(base_dir, locations, past_days):
        calls["weather"] = dict(base_dir=base_dir, past_days=past_days)
        return weather_result

    def fake_archive(base_dir, locations, start, end, overwrite=False):
        calls["archive"] = dict(base_dir=base_dir, start=start, end=end, overwrite=overwrite)
        return weather_result

    monkeypatch.setattr(smard, "extract", fake_smard)
    monkeypatch.setattr(weather, "extract", fake_weather)
    monkeypatch.setattr(weather, "extract_archive", fake_archive)
    return calls


@pytest.fixture(autouse=True)
def no_out_env(monkeypatch):
    monkeypatch.delenv(extract_local.OUT_ENV_VAR, raising=False)


def test_default_out(monkeypatch):
    assert extract_local.default_out() == "data/landing"
    monkeypatch.setenv("EXTRACT_OUT", "")
    assert extract_local.default_out() == "data/landing"
    monkeypatch.setenv("EXTRACT_OUT", "/data")
    assert extract_local.default_out() == "/data"


def test_main_out_defaults_to_env(monkeypatch):
    calls = fake_extractors(monkeypatch)
    monkeypatch.setenv("EXTRACT_OUT", "/data")
    assert extract_local.main(["--source", "smard"]) == 0
    assert calls["smard"]["base_dir"] == "/data"
    assert extract_local.main(["--source", "weather", "--out", "elsewhere"]) == 0
    assert calls["weather"]["base_dir"] == "elsewhere"


def test_main_default_runs_both_sources(monkeypatch):
    calls = fake_extractors(monkeypatch)
    assert extract_local.main(["--out", "x/landing", "--weeks", "3"]) == 0
    assert calls["smard"] == {
        "base_dir": "x/landing",
        "resolution": "hour",
        "n": 3,
        "overwrite": False,
        "refresh_latest": 2,
    }
    assert calls["weather"] == {"base_dir": "x/landing", "past_days": 7}


def test_main_source_smard_only(monkeypatch):
    calls = fake_extractors(monkeypatch)
    assert extract_local.main(["--source", "smard", "--refresh-latest", "0"]) == 0
    assert set(calls) == {"smard"}
    assert calls["smard"]["refresh_latest"] == 0


def test_main_source_weather_only_with_past_days(monkeypatch):
    calls = fake_extractors(monkeypatch)
    assert extract_local.main(["--source", "weather", "--past-days", "10", "--overwrite"]) == 0
    assert set(calls) == {"weather"}
    # Daily weather files are never overwritten: --overwrite does not reach the weather extractor.
    assert calls["weather"] == {"base_dir": "data/landing", "past_days": 10}


def test_main_nonzero_on_failures(monkeypatch):
    fake_extractors(monkeypatch, weather_result={"written": 0, "skipped": 0, "failed": 2})
    assert extract_local.main([]) == 1


def test_main_nonzero_on_smard_failures_only(monkeypatch):
    fake_extractors(monkeypatch, smard_result={"written": 5, "skipped": 0, "failed": 1})
    assert extract_local.main(["--source", "smard"]) == 1


def test_main_weather_archive_mode(monkeypatch):
    calls = fake_extractors(monkeypatch)
    argv = ["--source", "weather", "--mode", "archive", "--start", "2026-08-01"]
    assert extract_local.main([*argv, "--end", "2026-10-15"]) == 0
    assert set(calls) == {"archive"}
    assert calls["archive"] == {
        "base_dir": "data/landing",
        "start": date(2026, 8, 1),
        "end": date(2026, 10, 15),
        "overwrite": False,
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["--mode", "archive", "--start", "2026-08-01", "--end", "2026-10-15"],  # source all
        [*WEATHER_ARCHIVE, "--start", "2026-08-01"],  # no end
        [*WEATHER_ARCHIVE, "--start", "2026-10-15", "--end", "2026-08-01"],  # reversed
        [*WEATHER_ARCHIVE, "--start", "2026-13-01", "--end", "2026-10-15"],  # invalid date
    ],
)
def test_main_archive_mode_rejects_bad_arguments(monkeypatch, argv):
    calls = fake_extractors(monkeypatch)
    with pytest.raises(SystemExit):
        extract_local.main(argv)
    assert calls == {}
