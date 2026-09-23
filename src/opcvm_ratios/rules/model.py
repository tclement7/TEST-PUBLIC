from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

OPERATORS = {"<=", "<", ">=", ">"}


@dataclass
class Limit:
    key: str
    label: str
    operator: str
    value: Decimal
    unit: str = "ratio"          # ratio (fraction de l'actif net) | count


@dataclass
class SourceRef:
    source_id: str
    article: str
    confidence: str
    title: str = ""
    reference: str = ""


@dataclass
class RuleVersion:
    rule_id: str
    version: int
    name: str
    category: str
    framework: str | None
    jurisdiction: str | None
    definition: str
    formula: str
    calculator: str
    params: dict
    limits: list[Limit]
    applicability: dict
    exceptions: list[str]
    required_data: list[str]
    sources: list[SourceRef]
    valid_from: dt.date
    valid_to: dt.date | None
    validity_note: str = ""
    overrides: list[str] = field(default_factory=list)
    content_sha: str = ""
    content: dict = field(default_factory=dict)
    path: Path | None = None
    validation: dict | None = None     # {"by":..., "on":..., "comment":...} si validée

    @property
    def validated(self) -> bool:
        return bool(self.validation)

    def in_force(self, date: dt.date) -> bool:
        return self.valid_from <= date and (self.valid_to is None or date < self.valid_to)


CATEGORY_LABELS = {
    "dispersion_emetteur": "Ratios de dispersion par émetteur",
    "dispersion_emetteur_public": "Émetteurs publics",
    "concentration_groupe": "Ratios par groupe d'émetteurs",
    "depots_liquidites": "Dépôts et liquidités",
    "derives": "Instruments dérivés",
    "opc": "Investissements en OPC",
    "actifs_eligibles": "Actifs éligibles",
    "emprise": "Ratios d'emprise",
    "emprunts": "Emprunts",
    "geographique": "Ratios géographiques",
    "liquidite": "Ratios de liquidité",
    "statutaire": "Ratios statutaires",
    "autre": "Autres ratios",
}
