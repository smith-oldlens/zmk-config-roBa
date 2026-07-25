"""Config loading and validation (spec 02 §1, bps/config.py)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bps.config import ConfigError, load_config


def write_config(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_minimal_config_uses_defaults(tmp_path: Path):
    cfg = load_config(write_config(tmp_path / "c.yaml", {"base_dir": str(tmp_path / "bps")}))
    assert cfg.base_dir == tmp_path / "bps"
    assert cfg.grouping.quiet_seconds == 120.0
    assert cfg.deliver.label_reject == "Purple"
    assert cfg.af.tag_names[0] == "MakerNotes:FocusLocation"


def test_example_config_is_valid():
    """config.example.yaml must always load — it is the documented template."""
    example = Path(__file__).resolve().parent.parent / "config.example.yaml"
    cfg = load_config(example)
    assert cfg.camera_profile == "sony_a7c2"
    assert cfg.subject.det_model.endswith("rtmdet_nano.onnx")


def test_runtime_layout_paths(tmp_path: Path):
    cfg = load_config(write_config(tmp_path / "c.yaml", {"base_dir": str(tmp_path / "bps")}))
    assert cfg.inbox_dir.name == "inbox"
    assert cfg.arw_dir == cfg.work_dir / "arw"
    assert cfg.db_path.name == "state.db"
    cfg.ensure_dirs()
    assert all(d.is_dir() for d in cfg.all_dirs())


def test_missing_base_dir_rejected(tmp_path: Path):
    with pytest.raises(ConfigError, match="base_dir is required"):
        load_config(write_config(tmp_path / "c.yaml", {"exiftool_path": "exiftool"}))


def test_unknown_top_level_key_rejected(tmp_path: Path):
    with pytest.raises(ConfigError, match="unknown top-level key"):
        load_config(write_config(tmp_path / "c.yaml", {"base_dir": "/x", "typo": 1}))


def test_unknown_section_key_rejected(tmp_path: Path):
    """A misspelled threshold must fail loudly, not silently keep its default."""
    data = {"base_dir": "/x", "sharpness": {"reject_pcnt": 0.2}}
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(write_config(tmp_path / "c.yaml", data))


@pytest.mark.parametrize(
    "section,payload,message",
    [
        ("sharpness", {"reject_pct": 1.5}, "0.0..1.0"),
        ("sharpness", {"reject_pct": 0.8, "keeper_pct": 0.2}, "must be <="),
        ("grouping", {"gap_seconds": 0}, "gap_seconds"),
        ("ingest", {"max_verify_retries": 0}, "max_verify_retries"),
        ("notify", {"enabled": True}, "notify.topic"),
        ("af", {"tag_names": []}, "tag_names"),
    ],
)
def test_range_validation(tmp_path: Path, section: str, payload: dict, message: str):
    data = {"base_dir": "/x", section: payload}
    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path / "c.yaml", data))


def test_missing_file(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_malformed_yaml(tmp_path: Path):
    path = tmp_path / "c.yaml"
    path.write_text("base_dir: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)
