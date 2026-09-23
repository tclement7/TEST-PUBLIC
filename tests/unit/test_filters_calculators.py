from decimal import Decimal

from helpers import ctx, pos

from opcvm_ratios.engine.calculators import CALCULATORS
from opcvm_ratios.engine.filters import apply_filters
from opcvm_ratios.engine.model import NOT_RELEVANT

EQUITY_FILTER = [{"field": "instrument_type", "op": "in", "value": ["equity", "debt_security"]}]


def test_issuer_weight_example_from_specification():
    """Exemple du cahier des charges : émetteur X 8 420 000 € / actif net 100 000 000 € = 8,42 %."""
    c = ctx([pos("L1", "8420000", issuer_id="X", issuer_name="Émetteur X"),
             pos("L2", "1000000", issuer_id="Y")])
    out = CALCULATORS["weight_by_key"].compute(c, {"filters": EQUITY_FILTER, "key_field": "issuer_id"})
    assert out.values["max_weight"] == Decimal("0.0842")
    assert out.details[0].key == "X" and out.details[0].amount == Decimal("8420000")
    exprs = " ".join(t.expression for t in out.trace)
    assert "8 420 000,00 € / 100 000 000,00 € × 100" in exprs
    assert not out.missing and not out.hypotheses


def test_weights_are_aggregated_by_issuer_and_sorted_deterministically():
    c = ctx([pos("L1", "3000000", issuer_id="B"), pos("L2", "2000000", issuer_id="A"),
             pos("L3", "3000000", issuer_id="A")])
    out = CALCULATORS["weight_by_key"].compute(c, {"filters": EQUITY_FILTER})
    assert [d.key for d in out.details] == ["A", "B"]
    assert out.details[0].line_ids == ["L2", "L3"]


def test_5_10_40_threshold_is_strictly_greater_than_5_percent():
    c = ctx([pos("L1", "8000000", issuer_id="A"), pos("L2", "5000000", issuer_id="B"),
             pos("L3", "6000000", issuer_id="C")])
    out = CALCULATORS["rule_5_10_40"].compute(c, {"filters": EQUITY_FILTER, "threshold": "0.05"})
    assert out.values["max_weight"] == Decimal("0.08")
    assert out.values["sum_above_threshold"] == Decimal("0.14")  # B à 5 % exactement exclu


def test_missing_nav_makes_everything_not_calculable():
    out = CALCULATORS["weight_by_key"].compute(ctx([pos("L1", "1")], nav=None), {"filters": EQUITY_FILTER})
    assert out.values == {} and out.missing


def test_filter_on_missing_modes():
    lines = [pos("L1", "1", issuer_type=None), pos("L2", "1", issuer_type="souverain_ue"), pos("L3", "1", issuer_type="entreprise")]
    f = {"field": "issuer_type", "op": "not_in", "value": ["souverain_ue"]}
    inc = apply_filters(lines, [dict(f, on_missing="include")])
    assert [l["line_id"] for l in inc.included] == ["L1", "L3"] and inc.notes
    exc = apply_filters(lines, [dict(f, on_missing="exclude")])
    assert [l["line_id"] for l in exc.included] == ["L3"]
    nc = apply_filters(lines, [f])  # défaut : non calculable
    assert nc.missing and [l["line_id"] for l in nc.included] == ["L3"]


def test_not_all_exemption():
    lines = [pos("L1", "1", instrument_type="debt_security", issuer_type="souverain_ue"),
             pos("L2", "1", instrument_type="equity", issuer_type="souverain_ue"),
             pos("L3", "1", instrument_type="debt_security", issuer_type="entreprise")]
    res = apply_filters(lines, [{"not_all": [{"field": "instrument_type", "op": "in", "value": ["debt_security"]},
                                             {"field": "issuer_type", "op": "in", "value": ["souverain_ue"]}]}])
    assert [l["line_id"] for l in res.included] == ["L2", "L3"]


def test_unvalidated_referential_data_is_flagged():
    lines = [pos("L1", "1", issuer_type="entreprise", issuer_validated=False)]
    res = apply_filters(lines, [{"field": "issuer_type", "op": "in", "value": ["entreprise"]}])
    assert any("non validée" in n for n in res.notes)


def test_issuer_fallback_hypothesis():
    c = ctx([pos("L1", "1000000", issuer_id="ISIN:FR0000000001")])
    out = CALCULATORS["weight_by_key"].compute(c, {"filters": EQUITY_FILTER})
    assert any("1 instrument = 1 émetteur" in h for h in out.hypotheses)


def test_group_key_fallback():
    c = ctx([pos("L1", "1000000", issuer_id="A", group_id=None), pos("L2", "2000000", issuer_id="B", group_id="G"),
             pos("L3", "3000000", issuer_id="C", group_id="G", group_name="Groupe G")])
    out = CALCULATORS["weight_by_key"].compute(c, {"filters": EQUITY_FILTER, "key_field": "group_id",
                                                   "key_fallback": "issuer_id", "key_fallback_note": "fallback"})
    assert out.values["max_weight"] == Decimal("0.05")
    assert any(h.startswith("fallback") for h in out.hypotheses)


def test_aggregate_transforms():
    lines = [pos("C1", "3000000", instrument_type="cash_account"), pos("C2", "-500000", instrument_type="cash_account"),
             pos("F1", "0", market_value="20000000", instrument_type="derivative_listed_commitment"),
             pos("F2", "0", market_value="-5000000", instrument_type="derivative_listed_commitment")]
    cash = [{"field": "instrument_type", "op": "in", "value": ["cash_account"]}]
    agg = CALCULATORS["aggregate_filter"]
    assert agg.compute(ctx(lines), {"filters": cash, "amount_transform": "positive"}).values["total"] == Decimal("0.03")
    assert agg.compute(ctx(lines), {"filters": cash, "amount_transform": "negative_abs"}).values["total"] == Decimal("0.005")
    fut = [{"field": "instrument_type", "op": "in", "value": ["derivative_listed_commitment"]}]
    out = agg.compute(ctx(lines), {"filters": fut, "amount_field": "market_value", "amount_transform": "abs",
                                   "assumptions": ["brut"]})
    assert out.values["total"] == Decimal("0.25") and "brut" in out.hypotheses


def test_public_issuer_issues():
    f = [{"field": "issuer_type", "op": "in", "value": ["souverain_ue"]}]
    lines = [pos(f"B{i}", "6000000", instrument_type="debt_security", issuer_id="FR", issuer_type="souverain_ue",
                 isin=f"FR{i}") for i in range(6)]
    out = CALCULATORS["public_issuer_issues"].compute(ctx(lines), {"filters": f, "over_threshold": "0.35"})
    assert out.values["max_issue_weight"] == Decimal("0.06")
    assert out.values["min_issue_count"] == Decimal(6)  # 36 % > 35 % -> 6 émissions
    out2 = CALCULATORS["public_issuer_issues"].compute(ctx(lines[:2]), {"filters": f})
    assert out2.values["min_issue_count"] is NOT_RELEVANT


def test_counterparty_requires_type():
    lines = [pos("O1", "0", market_value="4000000", instrument_type="derivative_otc", issuer_id="BQ", issuer_type=None)]
    out = CALCULATORS["counterparty_exposure"].compute(ctx(lines), {"filters": [
        {"field": "instrument_type", "op": "in", "value": ["derivative_otc"]}]})
    assert out.missing


def test_counterparty_split_by_type_and_positive_only():
    lines = [pos("O1", "0", market_value="4000000", instrument_type="derivative_otc", issuer_id="BQ", issuer_type="etablissement_credit_ue"),
             pos("O2", "0", market_value="-9000000", instrument_type="derivative_otc", issuer_id="BQ", issuer_type="etablissement_credit_ue"),
             pos("O3", "0", market_value="3000000", instrument_type="derivative_otc", issuer_id="HF", issuer_type="entreprise")]
    out = CALCULATORS["counterparty_exposure"].compute(ctx(lines), {
        "filters": [{"field": "instrument_type", "op": "in", "value": ["derivative_otc"]}],
        "credit_institution_types": ["etablissement_credit_ue"]})
    assert out.values == {"max_credit_institution": Decimal("0.04"), "max_other": Decimal("0.03")}


def test_holding_ratio_needs_outstanding():
    lines = [pos("B1", "1", quantity=Decimal("1000000"), instrument_type="debt_security", issuer_id="Z", issuer_outstanding=None)]
    f = [{"field": "instrument_type", "op": "in", "value": ["debt_security"]}]
    assert CALCULATORS["holding_ratio"].compute(ctx(lines), {"filters": f}).missing
    lines[0]["issuer_outstanding"] = Decimal("20000000")
    assert CALCULATORS["holding_ratio"].compute(ctx(lines), {"filters": f}).values["max_holding"] == Decimal("0.05")


def test_calculators_are_deterministic():
    lines = [pos(f"L{i}", str(1000000 + i), issuer_id=f"E{i % 3}") for i in range(20)]
    a = CALCULATORS["rule_5_10_40"].compute(ctx(lines), {"filters": EQUITY_FILTER})
    b = CALCULATORS["rule_5_10_40"].compute(ctx(list(reversed(lines))), {"filters": EQUITY_FILTER})
    assert a.values == b.values
    assert [d.key for d in a.details] == [d.key for d in b.details]
