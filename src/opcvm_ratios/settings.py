"""Chargement du paramétrage (config/settings.yaml)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Tolerances:
    nav_reconciliation_abs: Decimal = Decimal("1.00")
    weight_reported_abs: Decimal = Decimal("0.0001")
    price_quantity_rel: Decimal = Decimal("0.005")
    fx_rel: Decimal = Decimal("0.0001")


@dataclass
class Settings:
    root: Path
    source_dir: Path
    archive_dir: Path
    db_path: Path
    exports_dir: Path
    logs_dir: Path
    mappings_dir: Path
    funds_dir: Path
    referentiel_dir: Path
    issuers_file: Path
    markets_file: Path
    min_file_age_seconds: int = 5
    tolerances: Tolerances = field(default_factory=Tolerances)
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    company_name: str = "Société de gestion"

    def ensure_dirs(self) -> None:
        for p in (self.source_dir, self.archive_dir, self.exports_dir, self.logs_dir, self.db_path.parent):
            p.mkdir(parents=True, exist_ok=True)


def _resolve(root: Path, value: str) -> Path:
    p = Path(os.path.expandvars(str(value)))
    return p if p.is_absolute() else (root / p)


def load_settings(path: str | Path | None = None, root: Path | None = None) -> Settings:
    path = Path(path or os.environ.get("OPCVM_RATIOS_SETTINGS") or PROJECT_ROOT / "config" / "settings.yaml")
    root = root or (path.parent.parent if path.parent.name == "config" else path.parent)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tol_raw = raw.get("tolerances") or {}
    tol = Tolerances(**{k: Decimal(str(v)) for k, v in tol_raw.items()})
    web = raw.get("web") or {}
    report = raw.get("report") or {}
    return Settings(
        root=root,
        source_dir=_resolve(root, raw.get("source_dir", "data/source")),
        archive_dir=_resolve(root, raw.get("archive_dir", "data/archive")),
        db_path=_resolve(root, raw.get("db_path", "data/opcvm_ratios.db")),
        exports_dir=_resolve(root, raw.get("exports_dir", "data/exports")),
        logs_dir=_resolve(root, raw.get("logs_dir", "data/logs")),
        mappings_dir=_resolve(root, raw.get("mappings_dir", "config/mappings")),
        funds_dir=_resolve(root, raw.get("funds_dir", "config/funds")),
        referentiel_dir=_resolve(root, raw.get("referentiel_dir", "referentiel")),
        issuers_file=_resolve(root, raw.get("issuers_file", "config/referentiels/local/emetteurs.csv")),
        markets_file=_resolve(root, raw.get("markets_file", "config/referentiels/marches.csv")),
        min_file_age_seconds=int(raw.get("min_file_age_seconds", 5)),
        tolerances=tol,
        web_host=str(web.get("host", "127.0.0.1")),
        web_port=int(web.get("port", 8765)),
        company_name=str(report.get("company_name", "Société de gestion")),
    )
