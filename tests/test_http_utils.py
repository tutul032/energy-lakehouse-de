"""Offline tests for energy_lakehouse.http_utils."""

from energy_lakehouse import http_utils


def test_write_bytes_creates_parent_dirs(tmp_path):
    path = tmp_path / "a" / "b" / "x.json"
    http_utils.write_bytes(str(path), b"{}")
    assert path.read_bytes() == b"{}"
