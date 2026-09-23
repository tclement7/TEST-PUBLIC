"""Fiches fonds (config/funds/*.yaml) : identification et caractéristiques versionnées."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import jsonutil


class FundConfigError(Exception):
    pass


@dataclass
class FundProfileVersion:
    valid_from: dt.date
    valid_to: dt.date | None
    attributes: dict
    source: str = ""


@dataclass
class FundConfig:
    fund_id: str
    name: str
    umbrella: str | None
    currency: str | None
    identifiers: dict
    share_classes: list[dict]
    aliases: dict[str, list[str]]
    profiles: list[FundProfileVersion]
    internal_alerts: dict = field(default_factory=dict)
    rules_exclude: dict = field(default_factory=dict)
    sha256: str = ""
    path: Path | None = None

    def profile_at(self, date: dt.date) -> list[FundProfileVersion]:
        """Versions de profil en vigueur à la date (0, 1 ou plusieurs : plusieurs = conflit)."""
        return [p for p in self.profiles if p.valid_from <= date and (p.valid_to is None or date < p.valid_to)]


def _date(v) -> dt.date | None:
    if v in (None, ""):
        return None
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v))


def load_fund(path: Path) -> FundConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        profiles = [FundProfileVersion(valid_from=_date(p["valid_from"]), valid_to=_date(p.get("valid_to")),
                                       attributes=dict(p.get("attributes") or {}), source=p.get("source", ""))
                    for p in raw.get("profiles") or []]
        return FundConfig(
            fund_id=str(raw["fund_id"]), name=raw["name"], umbrella=raw.get("umbrella"),
            currency=raw.get("currency"), identifiers={k: str(v) for k, v in (raw.get("identifiers") or {}).items()},
            share_classes=list(raw.get("share_classes") or []),
            aliases={k: [str(a) for a in v] for k, v in (raw.get("aliases") or {}).items()},
            profiles=profiles, internal_alerts=dict(raw.get("internal_alerts") or {}),
            rules_exclude=dict(raw.get("rules_exclude") or {}),
            sha256=jsonutil.sha256_obj(raw), path=path)
    except KeyError as exc:
        raise FundConfigError(f"{path.name}: clé obligatoire manquante {exc}") from exc


def load_funds(directory: Path) -> dict[str, FundConfig]:
    funds: dict[str, FundConfig] = {}
    for p in sorted(directory.glob("*.yaml")):
        f = load_fund(p)
        if f.fund_id in funds:
            raise FundConfigError(f"Fonds {f.fund_id} défini deux fois ({p.name})")
        funds[f.fund_id] = f
    return funds


def find_by_alias(funds: dict[str, FundConfig], profile_id: str, alias: str) -> list[FundConfig]:
    return [f for f in funds.values() if alias.casefold() in {a.casefold() for a in f.aliases.get(profile_id, [])}]


def find_by_identifier(funds: dict[str, FundConfig], key: str, value: str) -> list[FundConfig]:
    return [f for f in funds.values() if f.identifiers.get(key) == str(value)]
