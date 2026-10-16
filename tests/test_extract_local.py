from energy_lakehouse import extract_local


def test_main_passes_args_and_returns_exit_code(monkeypatch):
    captured = {}

    def fake_extract(base_dir, series_list, resolution, n_latest, overwrite=False):
        captured.update(base_dir=base_dir, resolution=resolution, n=n_latest, overwrite=overwrite)
        return {"written": 1, "skipped": 0, "failed": 0}

    monkeypatch.setattr(extract_local, "extract", fake_extract)
    assert extract_local.main(["--out", "x/landing", "--weeks", "3"]) == 0
    assert captured == {"base_dir": "x/landing", "resolution": "hour", "n": 3, "overwrite": False}


def test_main_nonzero_on_failures(monkeypatch):
    monkeypatch.setattr(
        extract_local, "extract", lambda *a, **k: {"written": 0, "skipped": 0, "failed": 2}
    )
    assert extract_local.main([]) == 1
