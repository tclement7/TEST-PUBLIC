"""Filtres déclaratifs appliqués aux positions (définis dans les règles, jamais dans le code).

Chaque filtre : {field, op, value, on_missing, missing_note}
  op : in | not_in | eq | ne | gt0 | lt0 | is_true | is_false
  on_missing : not_calculable (défaut) | exclude | include
Filtre composé : {not_all: [filtre, ...], label} exclut les lignes satisfaisant toutes les sous-conditions.
Les filtres sont évalués dans l'ordre ; une ligne écartée n'est plus évaluée par les filtres suivants.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..numbers import ZERO

# Champs issus des référentiels et indicateur de validation associé
REFERENTIAL_FIELDS = {"issuer_type": "issuer_validated", "group_id": "issuer_validated",
                      "issuer_id": "issuer_validated", "market_eligibility": "market_validated"}
FIELD_LABELS = {"instrument_type": "type d'instrument", "issuer_type": "type d'émetteur",
                "market_eligibility": "éligibilité du marché", "group_id": "groupe", "issuer_id": "émetteur",
                "currency": "devise", "country": "pays"}


@dataclass
class FilterResult:
    included: list[dict] = field(default_factory=list)
    excluded: int = 0
    missing: list[str] = field(default_factory=list)                 # messages bloquants (non calculable)
    notes: dict[str, list[str]] = field(default_factory=dict)        # hypothèse -> lignes concernées

    def note(self, text: str, line_id: str) -> None:
        self.notes.setdefault(text, []).append(line_id)


def eval_condition(line: dict, f: dict) -> bool | None:
    val = line.get(f["field"])
    op = f.get("op", "eq")
    target = f.get("value")
    if op in ("gt0", "lt0"):
        if val is None:
            return None
        return val > ZERO if op == "gt0" else val < ZERO
    if val is None:
        return None
    if op == "in":
        return val in target
    if op == "not_in":
        return val not in target
    if op == "eq":
        return val == target
    if op == "ne":
        return val != target
    if op == "is_true":
        return val is True
    if op == "is_false":
        return val is False
    raise ValueError(f"Opérateur de filtre inconnu : {op}")


def describe_filter(f: dict) -> str:
    if "not_all" in f:
        return f.get("label") or "Exclusion : " + " ET ".join(describe_filter(s) for s in f["not_all"])
    op = {"in": "∈", "not_in": "∉", "eq": "=", "ne": "≠", "gt0": "> 0", "lt0": "< 0",
          "is_true": "= vrai", "is_false": "= faux"}[f.get("op", "eq")]
    value = f.get("value")
    if isinstance(value, list):
        value = "{" + ", ".join(map(str, value)) + "}"
    return f"{FIELD_LABELS.get(f['field'], f['field'])} {op}" + (f" {value}" if value is not None else "")


def apply_filters(positions: list[dict], filters: list[dict]) -> FilterResult:
    res = FilterResult()
    for line in positions:
        keep = True
        for f in filters:
            if "not_all" in f:
                subs = [eval_condition(line, s) for s in f["not_all"]]
                if all(r is True for r in subs):
                    _track_referential(res, line, f["not_all"])
                    keep = False
                    break
                if any(r is None for r in subs) and not any(r is False for r in subs):
                    res.note(f"{describe_filter(f)} : non évaluable (donnée manquante), ligne conservée", line["line_id"])
                continue
            r = eval_condition(line, f)
            if r is None:
                mode = f.get("on_missing", "not_calculable")
                note = f.get("missing_note") or f"{FIELD_LABELS.get(f['field'], f['field'])} non renseigné"
                if mode == "include":
                    res.note(note, line["line_id"])
                    continue
                if mode == "exclude":
                    res.note(note, line["line_id"])
                    keep = False
                    break
                res.missing.append(f"{note} — {line['line_id']} ({line.get('name') or line.get('isin') or ''})")
                keep = False
                break
            _track_referential(res, line, [f])
            if not r:
                keep = False
                break
        if keep:
            res.included.append(line)
        else:
            res.excluded += 1
    return res


def _track_referential(res: FilterResult, line: dict, filters: list[dict]) -> None:
    for f in filters:
        flag = REFERENTIAL_FIELDS.get(f.get("field"))
        if flag and line.get(f["field"]) is not None and not line.get(flag):
            res.note(f"{FIELD_LABELS.get(f['field'], f['field']).capitalize()} issu d'une donnée de référentiel non validée",
                     line["line_id"])


def line_amount(line: dict, amount_field: str, transform: str | None, by_type: dict | None = None):
    v = line.get(amount_field)
    if v is None:
        return None
    t = (by_type or {}).get(line.get("instrument_type"), transform)
    if t in (None, "none"):
        return v
    if t == "abs":
        return abs(v)
    if t == "positive":
        return v if v > ZERO else ZERO
    if t == "negative_abs":
        return -v if v < ZERO else ZERO
    raise ValueError(f"Transformation de montant inconnue : {t}")
