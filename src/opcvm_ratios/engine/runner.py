"""Exécution des contrôles : snapshot figé -> enrichissement -> règles applicables -> calcul -> contrôle -> résultat.

Déterminisme : à snapshot, référentiels, règles et version du moteur identiques, l'empreinte du
résultat (fingerprint) est identique. La date d'exécution est une métadonnée hors empreinte.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import asdict

from .. import ENGINE_VERSION, jsonutil
from ..control import CONFLIT, NON_APPLICABLE, NON_CALCULABLE, decide_status, evaluate_limits
from ..funds import FundConfig
from ..ingestion.scanner import load_snapshot_content
from ..referentials import Referentials, enrich_positions
from ..rules.applicability import evaluate
from ..rules.loader import Referentiel
from ..rules.model import RuleVersion
from ..storage.db import Database, current_user, now_iso
from .calculators import CALCULATORS
from .model import NOT_RELEVANT, CalcContext, CalcOutput

log = logging.getLogger(__name__)

DATA_WARNING_CODES = {"ECART_RAPPROCHEMENT_ACTIF_NET", "ACTIF_NET_CLASSES", "CHANGE_INCOHERENT", "POIDS_INCOHERENT"}


def rule_summary(rule: RuleVersion) -> dict:
    return {"id": rule.rule_id, "version": rule.version, "sha256": rule.content_sha, "name": rule.name,
            "category": rule.category, "definition": rule.definition, "formula": rule.formula,
            "calculator": rule.calculator, "params": rule.params,
            "limits": [asdict(l) for l in rule.limits], "exceptions": rule.exceptions,
            "required_data": rule.required_data, "sources": [asdict(s) for s in rule.sources],
            "valid_from": rule.valid_from, "valid_to": rule.valid_to, "validity_note": rule.validity_note,
            "validated": rule.validated, "validation": rule.validation, "file": rule.path.name if rule.path else None}


def build_context(content: dict, refs: Referentials) -> CalcContext:
    warnings = [c["message"] for c in content.get("checks", []) if c["code"] in DATA_WARNING_CODES]
    return CalcContext(fund_id=content["fund_id"], fund_name=content["fund_name"],
                       data_date=dt.date.fromisoformat(content["data_date"]), currency=content["currency"],
                       nav=content["nav"]["total"], nav_source=content["nav"]["source"],
                       positions=enrich_positions(content["positions"], refs), data_warnings=warnings)


def _serialize_output(o: CalcOutput) -> dict:
    return {"values": {k: ("SANS_OBJET" if v is NOT_RELEVANT else v) for k, v in o.values.items()},
            "details": [asdict(d) for d in o.details], "trace": [asdict(t) for t in o.trace],
            "missing": o.missing, "hypotheses": o.hypotheses, "included_lines": o.included_lines,
            "excluded_count": o.excluded_count}


def compute_rule(rule: RuleVersion, ctx: CalcContext, fund: FundConfig, attrs: dict | None,
                 profile_note: str | None = None) -> dict:
    """Calcule et contrôle une règle pour un contexte. Fonction pure (hors horodatage ajouté par l'appelant)."""
    base = {"rule": rule_summary(rule), "fund": {"id": ctx.fund_id, "name": ctx.fund_name},
            "data_date": ctx.data_date, "currency": ctx.currency, "nav": ctx.nav, "nav_source": ctx.nav_source,
            "engine_version": ENGINE_VERSION}
    if rule.rule_id in fund.rules_exclude:
        return {**base, "status": NON_APPLICABLE, "indicative_status": NON_APPLICABLE,
                "reasons": [f"Exclue pour ce fonds : {fund.rules_exclude[rule.rule_id]}"], "limits": [],
                "applicability": {"result": False, "notes": []}}
    applicable, notes = evaluate(rule.applicability, attrs or {})
    if profile_note:
        notes = [profile_note] + notes
        applicable = None if applicable is not False else False
    if applicable is False:
        return {**base, "status": NON_APPLICABLE, "indicative_status": NON_APPLICABLE,
                "reasons": ["Conditions d'applicabilité non remplies"], "limits": [],
                "applicability": {"result": False, "notes": notes}}
    calc = CALCULATORS.get(rule.calculator)
    if calc is None:
        return {**base, "status": NON_CALCULABLE, "indicative_status": NON_CALCULABLE,
                "reasons": [f"Calculateur inconnu : {rule.calculator}"], "limits": [],
                "applicability": {"result": applicable, "notes": notes}}
    output = calc.compute(ctx, rule.params)
    for w in ctx.data_warnings:
        output.hypotheses.append(f"Contrôle de cohérence des données : {w}")
    limits = evaluate_limits(rule, output, ctx.nav, fund.internal_alerts.get(rule.rule_id) or {})
    status, indicative, reasons = decide_status(rule, output, limits, applicable)
    return {**base, "status": status, "indicative_status": indicative, "reasons": reasons, "limits": limits,
            "applicability": {"result": applicable, "notes": notes}, "output": _serialize_output(output)}


def resolve_rules(ref: Referentiel, date: dt.date) -> tuple[list[RuleVersion], list[tuple[str, list[RuleVersion]]], list[str]]:
    """Règles en vigueur à la date. Retourne (règles, conflits, règles sans version en vigueur)."""
    selected, conflicts, none_in_force = [], [], []
    for rid in ref.rule_ids():
        in_force = [r for r in ref.versions(rid) if r.in_force(date)]
        if len(in_force) == 1:
            selected.append(in_force[0])
        elif len(in_force) > 1:
            conflicts.append((rid, in_force))
        else:
            none_in_force.append(rid)
    return selected, conflicts, none_in_force


class Engine:
    def __init__(self, db: Database, referentiel: Referentiel, funds: dict[str, FundConfig], refs: Referentials):
        self.db = db
        self.ref = referentiel
        self.funds = funds
        self.refs = refs

    def latest_snapshots(self, fund_ids: list[str] | None = None) -> list:
        rows = self.db.q("SELECT * FROM snapshot WHERE status = 'actif' ORDER BY fund_id, data_date DESC, id DESC")
        seen, out = set(), []
        for r in rows:
            if fund_ids and r["fund_id"] not in fund_ids:
                continue
            if r["fund_id"] in seen:
                continue
            seen.add(r["fund_id"])
            out.append(r)
        return out

    def run(self, snapshot_ids: list[int] | None = None, fund_ids: list[str] | None = None,
            triggered_by: str | None = None) -> int:
        snaps = ([self.db.q1("SELECT * FROM snapshot WHERE id = ?", (i,)) for i in snapshot_ids]
                 if snapshot_ids else self.latest_snapshots(fund_ids))
        snaps = [s for s in snaps if s is not None]
        run_id = self.db.insert("calc_run", started_at=now_iso(), triggered_by=triggered_by or current_user(),
                                engine_version=ENGINE_VERSION, referentiel_sha=self.ref.sha256, status="en_cours",
                                scope_json=jsonutil.dumps({"snapshots": [s["id"] for s in snaps]}))
        n = 0
        try:
            for s in snaps:
                n += self._run_snapshot(run_id, s)
            self.db.update("calc_run", run_id, status="termine", finished_at=now_iso(),
                           message=f"{len(snaps)} fonds, {n} ratios")
        except Exception as exc:
            self.db.update("calc_run", run_id, status="erreur", finished_at=now_iso(), message=str(exc))
            self.db.audit("calcul_erreur", "calc_run", run_id, {"erreur": str(exc)})
            raise
        self.db.audit("calcul", "calc_run", run_id, {
            "snapshots": [{"id": s["id"], "fonds": s["fund_id"], "date": s["data_date"], "sha256": s["sha256"]} for s in snaps],
            "referentiel_sha": self.ref.sha256, "referentiels": self.refs.fingerprint(),
            "moteur": ENGINE_VERSION, "ratios": n})
        return run_id

    def _run_snapshot(self, run_id: int, snap) -> int:
        content = load_snapshot_content(snap)
        fund = self.funds.get(content["fund_id"])
        if fund is None:
            self.db.add_anomaly("run", run_id, "bloquante", "FONDS_NON_IDENTIFIE",
                                f"Fiche fonds {content['fund_id']} introuvable", fund_id=content["fund_id"])
            return 0
        ctx = build_context(content, self.refs)
        date = ctx.data_date
        profiles = fund.profile_at(date)
        attrs, profile_note = None, None
        if len(profiles) == 1:
            attrs = {"fund_id": fund.fund_id, **profiles[0].attributes}   # fund_id utilisable par les règles statutaires
        elif not profiles:
            profile_note = f"Aucune caractéristique du fonds en vigueur au {date} (fiche fonds)"
        else:
            profile_note = f"Plusieurs versions des caractéristiques du fonds en vigueur au {date} : conflit"
        calc_input = {"snapshot_sha": snap["sha256"], "referentials": self.refs.fingerprint(),
                      "fund_config_sha": fund.sha256, "attributes": attrs, "positions": ctx.positions,
                      "nav": ctx.nav, "nav_source": ctx.nav_source, "data_warnings": ctx.data_warnings}
        input_sha = jsonutil.sha256_obj(calc_input)
        ci_id = self.db.insert("calc_input", run_id=run_id, fund_id=ctx.fund_id, data_date=date.isoformat(),
                               snapshot_id=snap["id"], snapshot_sha=snap["sha256"],
                               referentials_json=jsonutil.dumps({**self.refs.fingerprint(), "fund_config_sha": fund.sha256}),
                               input_sha=input_sha, content_json=jsonutil.dumps(calc_input))
        selected, conflicts, none_in_force = resolve_rules(self.ref, date)
        results = [compute_rule(r, ctx, fund, attrs, profile_note) for r in selected]

        # Priorités explicites : une règle applicable peut en remplacer d'autres ("overrides")
        applicable_ids = {res["rule"]["id"] for res in results if res["status"] != NON_APPLICABLE}
        for res, rule in zip(results, selected):
            overriders = [r.rule_id for r in selected if rule.rule_id in r.overrides and r.rule_id in applicable_ids]
            if overriders and res["status"] != NON_APPLICABLE:
                res.update(status=NON_APPLICABLE, indicative_status=NON_APPLICABLE,
                           reasons=[f"Remplacée par la règle {', '.join(overriders)}"])
        for rid, versions in conflicts:
            r = versions[-1]
            results.append({"rule": rule_summary(r), "fund": {"id": ctx.fund_id, "name": ctx.fund_name},
                            "data_date": date, "currency": ctx.currency, "nav": ctx.nav, "nav_source": ctx.nav_source,
                            "engine_version": ENGINE_VERSION, "status": CONFLIT, "indicative_status": CONFLIT,
                            "reasons": [f"Plusieurs versions en vigueur au {date} : " +
                                        ", ".join(f"v{v.version}" for v in versions)],
                            "limits": [], "applicability": {"result": None, "notes": []}})
        for rid in none_in_force:
            r = self.ref.versions(rid)[-1]
            results.append({"rule": rule_summary(r), "fund": {"id": ctx.fund_id, "name": ctx.fund_name},
                            "data_date": date, "currency": ctx.currency, "nav": ctx.nav, "nav_source": ctx.nav_source,
                            "engine_version": ENGINE_VERSION, "status": NON_APPLICABLE,
                            "indicative_status": NON_APPLICABLE,
                            "reasons": [f"Aucune version de la règle en vigueur au {date}"], "limits": [],
                            "applicability": {"result": False, "notes": []}})

        computed_at = now_iso()
        for res in results:
            res["input_sha"] = input_sha
            res["snapshot_sha"] = snap["sha256"]
            fingerprint = jsonutil.sha256_obj(res)
            res["computed_at"] = computed_at
            res["fingerprint"] = fingerprint
            self.db.insert("ratio_result", run_id=run_id, calc_input_id=ci_id, fund_id=ctx.fund_id,
                           data_date=date.isoformat(), rule_id=res["rule"]["id"], rule_version=res["rule"]["version"],
                           rule_sha=res["rule"]["sha256"], category=res["rule"]["category"], name=res["rule"]["name"],
                           status=res["status"], indicative_status=res["indicative_status"],
                           result_json=jsonutil.dumps(res), fingerprint=fingerprint, computed_at=computed_at)
        return len(results)
