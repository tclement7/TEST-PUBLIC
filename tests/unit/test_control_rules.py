import datetime as dt
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from opcvm_ratios.control import (A_CONFIRMER, CONFORME, DEPASSEMENT, NON_CALCULABLE, PROCHE, decide_status,
                                  evaluate_limits)
from opcvm_ratios.engine.calculators import CALCULATORS
from opcvm_ratios.engine.model import CalcOutput
from opcvm_ratios.engine.runner import resolve_rules
from opcvm_ratios.rules.applicability import evaluate
from opcvm_ratios.rules.loader import load_referentiel, parse_rule, register_referentiel, validate_rule
from opcvm_ratios.storage.db import Database

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {"SRC": {"id": "SRC", "title": "Source test", "confidence": "a_confirmer"}}


def rule_dict(**over):
    d = {"id": "R1", "version": 1, "name": "Règle", "category": "dispersion_emetteur", "definition": "déf",
         "formula": "f", "calculator": "weight_by_key", "params": {"filters": []},
         "limits": [{"key": "max_weight", "label": "max", "operator": "<=", "value": "0.10"}],
         "applicability": {"all": [{"field": "legal_framework", "op": "eq", "value": "UCITS"}]},
         "sources": [{"source": "SRC", "article": "art. 1"}], "valid_from": "2020-01-01"}
    d.update(over)
    return d


def make_rule(validated=True, **over):
    r = parse_rule(rule_dict(**over), SOURCES, known_calculators=set(CALCULATORS))
    if validated:
        r.validation = {"by": "test", "on": "2026-01-01"}
    return r


@pytest.mark.parametrize("value,alert,expected", [
    ("0.0842", None, CONFORME), ("0.0972", "0.095", PROCHE), ("0.124", "0.095", DEPASSEMENT),
    ("0.10", None, CONFORME),  # égal à la limite : conforme pour l'opérateur <=
    ("0.0972", None, CONFORME),  # sans seuil d'alerte paramétré, jamais "proche"
])
def test_limit_status(value, alert, expected):
    rule = make_rule()
    out = CalcOutput(values={"max_weight": Decimal(value)})
    lims = evaluate_limits(rule, out, Decimal("100000000"), {"max_weight": alert} if alert else {})
    assert lims[0]["status"] == expected
    assert lims[0]["margin"] == Decimal("0.10") - Decimal(value)
    assert lims[0]["margin_amount"] == (Decimal("0.10") - Decimal(value)) * Decimal("100000000")
    status, indicative, _ = decide_status(rule, out, lims, True)
    assert status == indicative == expected


def test_min_operator_margin():
    rule = make_rule(limits=[{"key": "n", "label": "n", "operator": ">=", "value": "6", "unit": "count"}])
    lims = evaluate_limits(rule, CalcOutput(values={"n": Decimal(9)}), Decimal(1), {})
    assert lims[0]["status"] == CONFORME and lims[0]["margin"] == Decimal(3) and lims[0]["margin_amount"] is None
    lims = evaluate_limits(rule, CalcOutput(values={"n": Decimal(5)}), Decimal(1), {})
    assert lims[0]["status"] == DEPASSEMENT


def test_draft_rule_never_conforme():
    rule = make_rule(validated=False)
    out = CalcOutput(values={"max_weight": Decimal("0.01")})
    status, indicative, reasons = decide_status(rule, out, evaluate_limits(rule, out, Decimal(1), {}), True)
    assert status == A_CONFIRMER and indicative == CONFORME and any("brouillon" in r for r in reasons)


def test_unknown_applicability_and_hypotheses_give_a_confirmer():
    rule = make_rule()
    out = CalcOutput(values={"max_weight": Decimal("0.01")})
    assert decide_status(rule, out, evaluate_limits(rule, out, Decimal(1), {}), None)[0] == A_CONFIRMER
    out.hypotheses = ["h"]
    assert decide_status(rule, out, evaluate_limits(rule, out, Decimal(1), {}), True)[0] == A_CONFIRMER


def test_missing_data_gives_non_calculable():
    rule = make_rule()
    out = CalcOutput(values={"max_weight": Decimal("0.01")}, missing=["donnée X"])
    status, _, reasons = decide_status(rule, out, evaluate_limits(rule, out, Decimal(1), {}), True)
    assert status == NON_CALCULABLE and "donnée X" in reasons


def test_applicability_three_states():
    expr = {"all": [{"field": "legal_framework", "op": "eq", "value": "UCITS"},
                    {"field": "derogation", "op": "is_true"}]}
    assert evaluate(expr, {"legal_framework": "UCITS", "derogation": True})[0] is True
    assert evaluate(expr, {"legal_framework": "UCITS", "derogation": False})[0] is False
    assert evaluate(expr, {"legal_framework": "UCITS", "derogation": None})[0] is None
    assert evaluate(expr, {"legal_framework": "FIA", "derogation": None})[0] is False  # faux l'emporte
    assert evaluate({"any": [{"field": "a", "op": "eq", "value": 1}, {"field": "b", "op": "eq", "value": 2}]},
                    {"a": None, "b": 2})[0] is True


@pytest.mark.parametrize("over,msg", [
    ({"sources": [{"source": "INCONNUE"}]}, "source inconnue"),
    ({"limits": [{"key": "k", "operator": "=<", "value": "0.1"}]}, "opérateur"),
    ({"calculator": "inexistant"}, "calculateur inconnu"),
    ({"valid_to": "2019-01-01"}, "valid_to"),
    ({"limits": []}, "aucune limite"),
])
def test_invalid_rules_are_rejected(over, msg):
    with pytest.raises(ValueError, match=msg):
        parse_rule(rule_dict(**over), SOURCES, known_calculators=set(CALCULATORS))


def test_rule_versions_resolution_and_conflicts():
    v1 = make_rule(valid_to="2025-01-01")
    v2 = make_rule(version=2, valid_from="2025-01-01", limits=[{"key": "max_weight", "label": "m", "operator": "<=", "value": "0.08"}])
    ref = type("R", (), {})()
    from opcvm_ratios.rules.loader import Referentiel
    ref = Referentiel(rules=[v1, v2])
    sel, conflicts, none = resolve_rules(ref, dt.date(2024, 6, 30))
    assert [r.version for r in sel] == [1]
    sel, _, _ = resolve_rules(ref, dt.date(2026, 6, 30))
    assert [r.version for r in sel] == [2]
    v3 = make_rule(version=3, valid_from="2025-06-01")
    _, conflicts, _ = resolve_rules(Referentiel(rules=[v1, v2, v3]), dt.date(2026, 6, 30))
    assert conflicts and conflicts[0][0] == "R1"
    _, _, none = resolve_rules(Referentiel(rules=[v1]), dt.date(2026, 6, 30))
    assert none == ["R1"]


def test_rule_content_hash_ignores_validation_block():
    a = parse_rule(rule_dict(), SOURCES)
    b = parse_rule(rule_dict(validation={"by": "X", "on": "2026-01-01"}), SOURCES)
    c = parse_rule(rule_dict(limits=[{"key": "max_weight", "label": "max", "operator": "<=", "value": "0.11"}]), SOURCES)
    assert a.content_sha == b.content_sha != c.content_sha


def test_repository_referentiel_is_valid():
    ref = load_referentiel(ROOT / "referentiel", set(CALCULATORS))
    assert not ref.errors, ref.errors
    assert len(ref.rules) >= 14
    for r in ref.rules:
        assert r.sources and r.limits and r.definition and r.formula
        assert not r.validated, "les règles du dépôt sont livrées en brouillon"


def test_immutability_of_registered_versions(tmp_path):
    shutil.copytree(ROOT / "referentiel", tmp_path / "ref")
    db = Database(tmp_path / "db.sqlite")
    ref = load_referentiel(tmp_path / "ref", set(CALCULATORS))
    register_referentiel(db, ref)
    assert not ref.errors
    f = next((tmp_path / "ref" / "rules").rglob("01_*.yaml"))
    data = yaml.safe_load(f.read_text(encoding="utf-8"))
    data["limits"][0]["value"] = "0.12"          # modification sans changement de version
    f.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    ref2 = load_referentiel(tmp_path / "ref", set(CALCULATORS))
    register_referentiel(db, ref2)
    assert any("modifié sans changement de version" in e for e in ref2.errors)
    assert data["id"] not in {r.rule_id for r in ref2.rules}


def test_validation_is_bound_to_content(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    ref = load_referentiel(ROOT / "referentiel", set(CALCULATORS))
    register_referentiel(db, ref)
    validate_rule(db, ref.rules[0], "Contrôleur", "vérifié")
    ref2 = load_referentiel(ROOT / "referentiel", set(CALCULATORS))
    register_referentiel(db, ref2)
    assert ref2.rules[0].validated and ref2.rules[0].validation["by"] == "Contrôleur"
    assert not ref2.rules[1].validated
    ok, _ = db.verify_audit_chain()
    assert ok
