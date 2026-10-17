"""Centralized paths; all defaults are independent of the working directory."""

import os
import math
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    database_path: Path
    dataset_path: Path
    schema_path: Path = PROJECT_ROOT / "db" / "schema.sql"
    busy_timeout_ms: int = 5000
    minimum_sample_size: int = 30
    anomaly_minimum_sample_size: int = 30
    anomaly_minimum_change_pp: float = 10.0
    anomaly_alpha: float = 0.05
    anomaly_confidence_level: float = 0.95
    signal_minimum_change_pp: float = 10.0
    workspace_database_path: Path | None = None

    def __post_init__(self):
        if type(self.minimum_sample_size) is not int or self.minimum_sample_size < 30:
            raise ValueError("minimum_sample_size must be an integer of at least 30")
        if type(self.anomaly_minimum_sample_size) is not int or self.anomaly_minimum_sample_size < 30:
            raise ValueError("anomaly_minimum_sample_size must be an integer of at least 30")
        for name in ("anomaly_alpha", "anomaly_confidence_level", "anomaly_minimum_change_pp", "signal_minimum_change_pp"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
            if name in ("anomaly_minimum_change_pp", "signal_minimum_change_pp"):
                valid = 0 <= value <= 100
            else:
                valid = 0 < value < 1
            if not valid:
                raise ValueError(f"Invalid {name}")


def get_settings() -> Settings:
    bundled = PROJECT_ROOT / "data" / "synthetic"
    if (bundled / "patients.csv").is_file():
        default = bundled
    else:
        local = PROJECT_ROOT / "data" / "raw"
        shared = PROJECT_ROOT.parent / "data" / "raw"
        root = local if local.exists() else shared
        candidates = sorted(root.rglob("patients.csv")) if root.exists() else []
        default = candidates[0].parent if len(candidates) == 1 else root
    return Settings(
        database_path=Path(os.environ.get("AEI_DATABASE_PATH", PROJECT_ROOT / "data/runtime/aei.db")).resolve(),
        dataset_path=Path(os.environ.get("AEI_DATASET_PATH", default)).resolve(),
        minimum_sample_size=int(os.environ.get("AEI_MINIMUM_SAMPLE_SIZE", "30")),
        anomaly_minimum_sample_size=int(os.environ.get("AEI_ANOMALY_MINIMUM_SAMPLE_SIZE", "30")),
        anomaly_minimum_change_pp=float(os.environ.get("AEI_ANOMALY_MINIMUM_CHANGE_PP", "10")),
        anomaly_alpha=float(os.environ.get("AEI_ANOMALY_ALPHA", "0.05")),
        anomaly_confidence_level=float(os.environ.get("AEI_ANOMALY_CONFIDENCE_LEVEL", "0.95")),
        signal_minimum_change_pp=float(os.environ.get("AEI_SIGNAL_MINIMUM_CHANGE_PP", "10")),
        workspace_database_path=Path(os.environ["AEI_WORKSPACE_DATABASE_PATH"]).resolve() if os.environ.get("AEI_WORKSPACE_DATABASE_PATH") else None,
    )
