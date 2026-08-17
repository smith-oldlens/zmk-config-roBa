"""Config loading and validation (spec 02 §1, config.example.yaml).

Every key in config.example.yaml maps 1:1 onto a dataclass field here, and
unknown keys are an error rather than a silent typo — a misspelled threshold
that silently keeps its default would quietly change culling behaviour.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Raised for malformed / unknown / out-of-range configuration."""


@dataclass
class IngestConfig:
    max_verify_retries: int = 3
    size_stable_seconds: float = 2.0


@dataclass
class GroupingConfig:
    gap_seconds: float = 2.0
    quiet_seconds: float = 120.0
    gap_wait_max_seconds: float = 600.0


@dataclass
class SubjectConfig:
    det_model: str = "models/rtmdet_nano.onnx"
    conf_threshold: float = 0.25
    center_sigma: float = 0.35


@dataclass
class SharpnessConfig:
    bootstrap_log10: float = 2.0
    reject_pct: float = 0.15
    keeper_pct: float = 0.50


@dataclass
class MomentConfig:
    classifier: str = "models/moment_classifier.pkl"
    embed_model: str = "models/siglip2_base.onnx"
    pose_model: str = "models/rtmpose_m.onnx"
    star5_threshold: float = 0.70


@dataclass
class AfConfig:
    # Priority order; confirmed against real hardware in M0 (docs/OPEN_QUESTIONS.md).
    tag_names: list[str] = field(
        default_factory=lambda: [
            "MakerNotes:FocusLocation",
            "MakerNotes:FlexibleSpotPosition",
            "MakerNotes:FocalPlaneAFPointLocation",
        ]
    )


@dataclass
class RatingsConfig:
    """Star values the tool writes, matched to the user's own convention.

    The owner's existing catalog uses: 0 = not selected, 1 = selected,
    2-5 = own child / more important than 1. The tool must speak that language
    rather than impose its own, so these defaults follow it: confident keeps
    get the user's "selected" star, decisive moments enter the "important"
    band, and both rejects and review frames stay at 0 — telling them apart is
    the colour label's job (reject=Purple, review=Yellow).
    """

    keep: int = 1
    moment: int = 2
    reject: int = 0
    review: int = 0


@dataclass
class DeliverConfig:
    # Must match Lightroom's colour label set verbatim, in English (docs/04 §5).
    label_reject: str = "Purple"
    label_review: str = "Yellow"


@dataclass
class NotifyConfig:
    enabled: bool = False
    topic: str = ""
    interval_seconds: float = 600.0


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class Config:
    base_dir: Path
    exiftool_path: str = "exiftool"
    camera_profile: str = "sony_a7c2"
    ingest: IngestConfig = field(default_factory=IngestConfig)
    grouping: GroupingConfig = field(default_factory=GroupingConfig)
    subject: SubjectConfig = field(default_factory=SubjectConfig)
    sharpness: SharpnessConfig = field(default_factory=SharpnessConfig)
    moment: MomentConfig = field(default_factory=MomentConfig)
    af: AfConfig = field(default_factory=AfConfig)
    ratings: RatingsConfig = field(default_factory=RatingsConfig)
    deliver: DeliverConfig = field(default_factory=DeliverConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    # --- runtime layout (spec 02 §1) -------------------------------------
    @property
    def inbox_dir(self) -> Path:
        return self.base_dir / "inbox"

    @property
    def work_dir(self) -> Path:
        return self.base_dir / "work"

    @property
    def arw_dir(self) -> Path:
        return self.work_dir / "arw"

    @property
    def deliver_dir(self) -> Path:
        return self.base_dir / "deliver"

    @property
    def raw_select_dir(self) -> Path:
        return self.base_dir / "raw_select"

    @property
    def quarantine_dir(self) -> Path:
        return self.base_dir / "quarantine"

    @property
    def models_dir(self) -> Path:
        return self.base_dir / "models"

    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "logs"

    @property
    def db_path(self) -> Path:
        return self.base_dir / "state.db"

    def all_dirs(self) -> list[Path]:
        return [
            self.inbox_dir,
            self.work_dir,
            self.arw_dir,
            self.deliver_dir,
            self.raw_select_dir,
            self.quarantine_dir,
            self.models_dir,
            self.logs_dir,
        ]

    def ensure_dirs(self) -> None:
        for d in self.all_dirs():
            d.mkdir(parents=True, exist_ok=True)


_SECTIONS: dict[str, type] = {
    "ingest": IngestConfig,
    "grouping": GroupingConfig,
    "subject": SubjectConfig,
    "sharpness": SharpnessConfig,
    "moment": MomentConfig,
    "af": AfConfig,
    "ratings": RatingsConfig,
    "deliver": DeliverConfig,
    "notify": NotifyConfig,
    "logging": LoggingConfig,
}
_SCALARS = {"base_dir", "exiftool_path", "camera_profile"}


def _build_section(name: str, cls: type, raw: Any):
    if raw is None:
        return cls()
    if not isinstance(raw, dict):
        raise ConfigError(f"config section {name!r} must be a mapping, got {type(raw).__name__}")
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(
            f"unknown key(s) in section {name!r}: {', '.join(sorted(unknown))}. "
            f"Known keys: {', '.join(sorted(known))}"
        )
    return cls(**raw)


def _validate(cfg: Config) -> None:
    """Range checks for values that would silently corrupt culling behaviour."""
    problems: list[str] = []
    if cfg.ingest.max_verify_retries < 1:
        problems.append("ingest.max_verify_retries must be >= 1")
    if cfg.ingest.size_stable_seconds < 0:
        problems.append("ingest.size_stable_seconds must be >= 0")
    if cfg.grouping.gap_seconds <= 0:
        problems.append("grouping.gap_seconds must be > 0")
    if cfg.grouping.quiet_seconds < 0:
        problems.append("grouping.quiet_seconds must be >= 0")
    for name, value in (
        ("sharpness.reject_pct", cfg.sharpness.reject_pct),
        ("sharpness.keeper_pct", cfg.sharpness.keeper_pct),
        ("moment.star5_threshold", cfg.moment.star5_threshold),
        ("subject.conf_threshold", cfg.subject.conf_threshold),
    ):
        if not 0.0 <= value <= 1.0:
            problems.append(f"{name} must be within 0.0..1.0 (got {value})")
    if cfg.sharpness.reject_pct > cfg.sharpness.keeper_pct:
        problems.append("sharpness.reject_pct must be <= sharpness.keeper_pct")
    if not cfg.af.tag_names:
        problems.append("af.tag_names must list at least one tag")
    if not cfg.deliver.label_reject:
        problems.append("deliver.label_reject must not be empty")
    if not cfg.deliver.label_review:
        problems.append("deliver.label_review must not be empty")
    r = cfg.ratings
    for name, value in (("keep", r.keep), ("moment", r.moment), ("reject", r.reject), ("review", r.review)):
        if not 0 <= value <= 5:
            problems.append(f"ratings.{name} must be within 0..5 (got {value})")
    if r.keep <= r.reject or r.keep <= r.review:
        problems.append("ratings.keep must be greater than ratings.reject and ratings.review")
    if r.moment < r.keep:
        problems.append("ratings.moment must be >= ratings.keep")
    if cfg.notify.enabled and not cfg.notify.topic:
        problems.append("notify.topic is required when notify.enabled is true")
    if problems:
        raise ConfigError("invalid config:\n  - " + "\n  - ".join(problems))


def load_config(path: str | Path) -> Config:
    """Load and validate a config.yaml. Raises ConfigError on any problem."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")

    unknown = set(raw) - _SCALARS - set(_SECTIONS)
    if unknown:
        raise ConfigError(f"unknown top-level key(s): {', '.join(sorted(unknown))}")
    if "base_dir" not in raw:
        raise ConfigError("base_dir is required")

    kwargs: dict[str, Any] = {"base_dir": Path(str(raw["base_dir"])).expanduser()}
    for key in ("exiftool_path", "camera_profile"):
        if key in raw and raw[key] is not None:
            kwargs[key] = str(raw[key])
    for name, cls in _SECTIONS.items():
        if name in raw:
            kwargs[name] = _build_section(name, cls, raw[name])

    try:
        cfg = Config(**kwargs)
    except TypeError as exc:  # wrong scalar type for a section field
        raise ConfigError(str(exc)) from exc
    _validate(cfg)
    return cfg
