"""Contrôle : comparaison des valeurs calculées aux limites du référentiel, marge, statut.

Aucune limite n'est codée ici : elles proviennent exclusivement de la version de règle,
et les seuils d'alerte ("proche de la limite") exclusivement de la fiche fonds validée.
"""
from __future__ import annotations

from decimal import Decimal

from .engine.model import NOT_RELEVANT, CalcOutput
from .numbers import ZERO
from .rules.model import RuleVersion

CONFORME = "CONFORME"
PROCHE = "PROCHE_LIMITE"
DEPASSEMENT = "DEPASSEMENT"
NON_CALCULABLE = "NON_CALCULABLE"
NON_APPLICABLE = "NON_APPLICABLE"
A_CONFIRMER = "A_CONFIRMER"
CONFLIT = "CONFLIT_REGLES"
SANS_OBJET = "SANS_OBJET"

STATUS_LABELS = {
    CONFORME: "Conforme",
    PROCHE: "Proche de la limite",
    DEPASSEMENT: "Dépassement",
    NON_CALCULABLE: "Non calculable",
    NON_APPLICABLE: "Non applicable",
    A_CONFIRMER: "À confirmer",
    CONFLIT: "Conflit de règles",
    SANS_OBJET: "Sans objet",
}
SEVERITY = {DEPASSEMENT: 6, CONFLIT: 5, NON_CALCULABLE: 4, A_CONFIRMER: 3, PROCHE: 2, CONFORME: 1, SANS_OBJET: 0,
            NON_APPLICABLE: 0}


def _breach(op: str, value: Decimal, limit: Decimal) -> bool:
    return {"<=": value > limit, "<": value >= limit, ">=": value < limit, ">": value <= limit}[op]


def _margin(op: str, value: Decimal, limit: Decimal) -> Decimal:
    return limit - value if op in ("<=", "<") else value - limit


def evaluate_limits(rule: RuleVersion, output: CalcOutput, nav: Decimal | None, alerts: dict) -> list[dict]:
    rows = []
    for lim in rule.limits:
        v = output.values.get(lim.key)
        alert_cfg = alerts.get(lim.key)
        alert = Decimal(str(alert_cfg)) if alert_cfg not in (None, "") else None
        row = {"key": lim.key, "label": lim.label, "operator": lim.operator, "limit": lim.value, "unit": lim.unit,
               "alert": alert, "value": None, "margin": None, "margin_amount": None}
        if v is NOT_RELEVANT:
            row["status"] = SANS_OBJET
        elif v is None or output.missing:
            row["status"] = NON_CALCULABLE
        else:
            row["value"] = v
            row["margin"] = _margin(lim.operator, v, lim.value)
            if lim.unit == "ratio" and nav is not None:
                row["margin_amount"] = row["margin"] * nav
            if _breach(lim.operator, v, lim.value):
                row["status"] = DEPASSEMENT
            elif alert is not None and _breach(lim.operator, v, alert):
                row["status"] = PROCHE
            else:
                row["status"] = CONFORME
        rows.append(row)
    return rows


def decide_status(rule: RuleVersion, output: CalcOutput, limits: list[dict], applicable: bool | None,
                  extra_reasons: list[str] | None = None) -> tuple[str, str, list[str]]:
    """Retourne (statut final, statut indicatif, motifs)."""
    reasons = list(extra_reasons or [])
    statuses = [l["status"] for l in limits]
    if NON_CALCULABLE in statuses:
        indicative = NON_CALCULABLE
    elif DEPASSEMENT in statuses:
        indicative = DEPASSEMENT
    elif PROCHE in statuses:
        indicative = PROCHE
    else:
        indicative = CONFORME
    if indicative == NON_CALCULABLE:
        reasons += output.missing or ["Valeur non calculée"]
        return NON_CALCULABLE, NON_CALCULABLE, reasons
    if applicable is None:
        reasons.append("Applicabilité indéterminée : caractéristique(s) du fonds non renseignée(s)")
    if not rule.validated:
        reasons.append("Règle en brouillon : non validée par la conformité")
    if output.hypotheses:
        reasons.append("Résultat fondé sur des hypothèses ou des données de référentiel non validées")
    if reasons:
        return A_CONFIRMER, indicative, reasons
    return indicative, indicative, reasons
