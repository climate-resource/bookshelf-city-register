import sys
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

import register

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import parity_urbclim


def run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, city_ids: list[str]) -> None:
    rings = gpd.GeoDataFrame(
        {"city_id": city_ids, "kind": "ucdb-buffer-5km"},
        geometry=[box(index, 0, index + 0.5, 0.5) for index in range(len(city_ids))],
        crs="EPSG:4326",
    )
    rings.to_parquet(tmp_path / "boundaries.parquet")
    rings[["city_id", "geometry"]].to_parquet(tmp_path / "legacy.parquet")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "parity_urbclim.py",
            "--ucdb=unused.zip",
            f"--boundaries={tmp_path / 'boundaries.parquet'}",
            f"--legacy={tmp_path / 'legacy.parquet'}",
        ],
    )
    parity_urbclim.main()


def test_matching_rings_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run(monkeypatch, tmp_path, register.read_csv(register.ROSTER_IDS)["city_id"].tolist())


def test_a_city_missing_from_both_inputs_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    city_ids = register.read_csv(register.ROSTER_IDS)["city_id"].tolist()

    with pytest.raises(SystemExit):
        run(monkeypatch, tmp_path, city_ids[1:])

    assert city_ids[0] in capsys.readouterr().out
