from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal


class _NotRelevant:
    """Valeur « sans objet » : la contrainte ne s'applique à aucune entité (ex. aucun émetteur > 35 %)."""

    def __repr__(self) -> str:
        return "SANS_OBJET"


NOT_RELEVANT = _NotRelevant()


@dataclass
class TraceStep:
    kind: str                  # entree | filtre | agregation | formule | resultat | hypothese
    label: str
    expression: str = ""
    value: str | None = None
    refs: list[str] = field(default_factory=list)


@dataclass
class DetailRow:
    key: str
    label: str
    amount: Decimal | None
    weight: Decimal | None
    line_ids: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


@dataclass
class CalcContext:
    fund_id: str
    fund_name: str
    data_date: dt.date
    currency: str
    nav: Decimal | None
    nav_source: dict
    positions: list[dict]
    data_warnings: list[str] = field(default_factory=list)

    def line(self, line_id: str) -> dict | None:
        for p in self.positions:
            if p["line_id"] == line_id:
                return p
        return None


@dataclass
class CalcOutput:
    values: dict
    details: list[DetailRow] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    included_lines: list[str] = field(default_factory=list)
    excluded_count: int = 0
    amount_label: str = ""
