from energy_lakehouse import extract_local, smard, weather

OK = {"written": 1, "skipped": 0, "failed": 0}


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

    def fake_weather(base_dir, locations, past_days, overwrite=False):
        calls["weather"] = dict(base_dir=base_dir, past_days=past_days, overwrite=overwrite)
        return weather_result

    monkeypatch.setattr(smard, "extract", fake_smard)
    monkeypatch.setattr(weather, "extract", fake_weather)
    return calls


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
    assert calls["weather"] == {"base_dir": "x/landing", "past_days": 63, "overwrite": False}


def test_main_source_smard_only(monkeypatch):
    calls = fake_extractors(monkeypatch)
    assert extract_local.main(["--source", "smard", "--refresh-latest", "0"]) == 0
    assert set(calls) == {"smard"}
    assert calls["smard"]["refresh_latest"] == 0


def test_main_source_weather_only_with_past_days(monkeypatch):
    calls = fake_extractors(monkeypatch)
    assert extract_local.main(["--source", "weather", "--past-days", "10", "--overwrite"]) == 0
    assert set(calls) == {"weather"}
    assert calls["weather"] == {"base_dir": "data/landing", "past_days": 10, "overwrite": True}


def test_main_nonzero_on_failures(monkeypatch):
    fake_extractors(monkeypatch, weather_result={"written": 0, "skipped": 0, "failed": 2})
    assert extract_local.main([]) == 1
