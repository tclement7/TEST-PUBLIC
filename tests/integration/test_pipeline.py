"""Tests de bout en bout sur le FONDS TEST fictif (tests/fixtures/fonds_test)."""
import shutil
from decimal import Decimal

import pytest

from opcvm_ratios.rules.loader import validate_rule
from opcvm_ratios.services import Services

FUND = "999901"


def _validate_all(s: Services):
    for r in s.referentiel.rules:
        validate_rule(s.db, r, "Jeu de test", "validation pour test")
    s.reload()


def _by_rule(results):
    return {r["rule"]["id"]: r for r in results}


def test_scan_identifies_fund_date_and_reconciles(settings_factory):
    s = Services(settings_factory())
    rep = s.scan()
    assert len(rep.processed) == 3 and not rep.errors and len(rep.snapshots) == 1
    snap = s.db.q1("SELECT * FROM snapshot WHERE id = ?", (rep.snapshots[0],))
    assert snap["fund_id"] == FUND and snap["data_date"] == "2026-06-30" and snap["status"] == "actif"
    codes = {a["code"] for a in s.db.anomalies("snapshot", snap["id"])}
    assert "RAPPROCHEMENT_ACTIF_NET_OK" in codes
    assert not codes & {"ECART_RAPPROCHEMENT_ACTIF_NET", "POIDS_INCOHERENT", "PRIX_QUANTITE_INCOHERENT", "ISIN_INVALIDE"}
    # les fichiers sont archivés et hachés
    for f in s.db.q("SELECT * FROM source_file"):
        assert f["archive_path"] and len(f["sha256"]) == 64


def test_expected_results_with_validated_rules(settings_factory, expected):
    s = Services(settings_factory())
    s.scan()
    _validate_all(s)
    res = _by_rule(s.results(s.run()))
    assert set(res) == set(expected["rules"])
    for rid, exp in expected["rules"].items():
        r = res[rid]
        assert r["status"] == exp["status"], (rid, r["status"], r.get("reasons"))
        if "indicative_status" in exp:
            assert r["indicative_status"] == exp["indicative_status"], rid
        for key, val in (exp.get("values") or {}).items():
            got = r["output"]["values"][key]
            assert Decimal(str(got)) == Decimal(val), (rid, key, got, val)
    assert Decimal(str(res["LU-UCITS-43-EMETTEUR-5-10-40"]["nav"])) == Decimal(expected["nav"])


def test_draft_rules_never_report_conforme(settings_factory):
    s = Services(settings_factory())
    s.scan()
    res = s.results(s.run())
    assert not [r for r in res if r["status"] in ("CONFORME", "PROCHE_LIMITE", "DEPASSEMENT")]
    breach = _by_rule(res)["LU-UCITS-43-EMETTEUR-5-10-40"]
    assert breach["status"] == "A_CONFIRMER" and breach["indicative_status"] == "DEPASSEMENT"


def test_traceability_of_a_result(settings_factory):
    s = Services(settings_factory())
    s.scan()
    run_id = s.run()
    r = _by_rule(s.results(run_id))["LU-UCITS-43-EMETTEUR-5-10-40"]
    assert r["rule"]["version"] == 1 and len(r["rule"]["sha256"]) == 64
    assert r["nav_source"]["file"] == "ffnav1_TST_2026_06_30.csv"
    top = r["output"]["details"][0]
    assert top["key"] == "EM-Z" and Decimal(top["amount"]) == Decimal("12400000")
    ci = s.calc_input(r["_calc_input_id"])
    lines = {p["line_id"]: p for p in ci["positions"]}
    src = lines[top["line_ids"][0]]
    assert src["source_file"] == "ffpos1_TST_2026_06_30.csv" and src["source_row"] == 5
    assert any("12 400 000,00 € / 100 000 000,00 € × 100" in t["expression"] for t in r["output"]["trace"])


def test_determinism_same_inputs_same_fingerprints(settings_factory):
    s = Services(settings_factory())
    s.scan()
    a = {r["rule"]["id"]: r["fingerprint"] for r in s.results(s.run())}
    b = {r["rule"]["id"]: r["fingerprint"] for r in s.results(s.run())}
    assert a == b


def test_rescan_does_not_reprocess(settings_factory):
    s = Services(settings_factory())
    s.scan()
    rep = s.scan()
    assert rep.skipped_duplicates == 3 and not rep.processed


def test_new_rule_version_does_not_change_history(settings_factory, tmp_path):
    settings = settings_factory()
    ref = tmp_path / "referentiel"
    shutil.copytree(settings.referentiel_dir, ref)
    settings.referentiel_dir = ref
    s = Services(settings)
    s.scan()
    run1 = s.run()
    old = _by_rule(s.results(run1))["LU-UCITS-43-3-EMETTEUR-PUBLIC-35"]
    # Ajout d'une v2 (limite fictive 25 %) sans fermer la v1 : deux versions en vigueur -> conflit signalé
    f1 = next(ref.rglob("02_souverain_35.v1.yaml"))
    text = f1.read_text(encoding="utf-8")
    v2 = text.replace("version: 1", "version: 2").replace('value: "0.35"', 'value: "0.25"').replace(
        'valid_from: "2010-12-17"', 'valid_from: "2026-01-01"')
    (f1.parent / "02_souverain_35.v2.yaml").write_text(v2, encoding="utf-8")
    run2 = s.run()
    new = _by_rule(s.results(run2))["LU-UCITS-43-3-EMETTEUR-PUBLIC-35"]
    assert new["status"] == "CONFLIT_REGLES"
    # le résultat historique est inchangé
    again = _by_rule(s.results(run1))["LU-UCITS-43-3-EMETTEUR-PUBLIC-35"]
    assert again["fingerprint"] == old["fingerprint"] and again["rule"]["version"] == 1


def test_rule_versioning_with_closed_previous_version(settings_factory, tmp_path):
    settings = settings_factory()
    ref = tmp_path / "referentiel"
    shutil.copytree(settings.referentiel_dir, ref)
    f1 = next(ref.rglob("02_souverain_35.v1.yaml"))
    text = f1.read_text(encoding="utf-8")
    f1.write_text(text.replace("valid_to: null", 'valid_to: "2026-01-01"'), encoding="utf-8")
    v2 = text.replace("version: 1", "version: 2").replace('value: "0.35"', 'value: "0.25"').replace(
        'valid_from: "2010-12-17"', 'valid_from: "2026-01-01"')
    (f1.parent / "02_souverain_35.v2.yaml").write_text(v2, encoding="utf-8")
    settings.referentiel_dir = ref
    s = Services(settings)
    s.scan()
    _validate_all(s)
    r = _by_rule(s.results(s.run()))["LU-UCITS-43-3-EMETTEUR-PUBLIC-35"]
    assert r["rule"]["version"] == 2 and r["status"] == "DEPASSEMENT"   # 30 % > 25 % (limite fictive de test)


def test_modified_file_creates_new_snapshot(settings_factory):
    settings = settings_factory()
    s = Services(settings)
    s.scan()
    p = settings.source_dir / "ffnav1_TST_2026_06_30.csv"
    p.write_text(p.read_text(encoding="utf-8").replace("100000000.00", "100000100.00"), encoding="utf-8")
    rep = s.scan()
    assert len(rep.processed) == 1 and len(rep.snapshots) == 1
    statuses = [r["status"] for r in s.db.q("SELECT status FROM snapshot ORDER BY id")]
    assert statuses == ["remplace", "actif"]
    new = s.db.q1("SELECT id FROM snapshot WHERE status='actif'")["id"]
    codes = {a["code"] for a in s.db.anomalies("snapshot", new)}
    assert "ECART_RAPPROCHEMENT_ACTIF_NET" in codes and "ACTIF_NET_CLASSES" in codes


def test_missing_nav_file(settings_factory):
    settings = settings_factory()
    (settings.source_dir / "ffnav1_TST_2026_06_30.csv").unlink()
    s = Services(settings)
    rep = s.scan()
    assert rep.snapshots == []
    assert any(a["code"] == "FICHIER_ABSENT" for a in s.db.anomalies("dataset"))


def test_unknown_fund_and_date_mismatch(settings_factory):
    settings = settings_factory()
    src = settings.source_dir
    shutil.copy2(src / "ffcur1_TST_2026_06_30.csv", src / "ffcur1_ZZZ_2026_06_30.csv")
    shutil.copy2(src / "ffnav1_TST_2026_06_30.csv", src / "ffnav1_TST_2026_07_01.csv")
    (src / "notes.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (src / "ffpos1_TST_2026_05_31.xlsx").write_bytes(b"pas un classeur")
    s = Services(settings)
    rep = s.scan()
    by = {x["file"]: x for x in rep.processed}
    assert by["notes.csv"]["status"] == "non_reconnu"
    assert by["ffpos1_TST_2026_05_31.xlsx"]["status"] == "illisible"
    codes = lambda name: {a["code"] for a in s.db.anomalies("import", by[name]["import_id"])}
    assert "FONDS_NON_IDENTIFIE" in codes("ffcur1_ZZZ_2026_06_30.csv")
    assert "DATE_INCOHERENTE" in codes("ffnav1_TST_2026_07_01.csv")


def test_missing_issuer_referential_uses_explicit_hypothesis(settings_factory):
    s = Services(settings_factory(issuers=False))
    s.scan()
    _validate_all(s)
    res = _by_rule(s.results(s.run()))
    r = res["LU-UCITS-43-EMETTEUR-5-10-40"]
    assert r["status"] == "A_CONFIRMER"
    assert any("1 instrument = 1 émetteur" in h for h in r["output"]["hypotheses"])
    assert res["LU-UCITS-46-2-OPC-NON-OPCVM-30"]["status"] == "NON_CALCULABLE"


def test_missing_fund_characteristic_gives_a_confirmer(settings_factory, tmp_path):
    settings = settings_factory()
    funds = tmp_path / "funds"
    funds.mkdir()
    text = (settings.funds_dir / "999901_fonds_test.yaml").read_text(encoding="utf-8")
    (funds / "f.yaml").write_text(text.replace("global_exposure_method: commitment", "global_exposure_method: null"),
                                  encoding="utf-8")
    settings.funds_dir = funds
    s = Services(settings)
    s.scan()
    _validate_all(s)
    r = _by_rule(s.results(s.run()))["LU-UCITS-42-3-EXPOSITION-GLOBALE-ENGAGEMENT"]
    assert r["status"] == "A_CONFIRMER" and r["applicability"]["result"] is None
    assert any("indéterminé" in n for n in r["applicability"]["notes"])


def test_audit_chain(settings_factory):
    s = Services(settings_factory())
    s.scan()
    s.run()
    ok, bad = s.db.verify_audit_chain()
    assert ok
    actions = {a["action"] for a in s.db.q("SELECT action FROM audit_log")}
    assert {"scan", "import_fichier", "snapshot", "calcul"} <= actions
    s.db.conn.execute("UPDATE audit_log SET details_json = '{}' WHERE action = 'calcul'")
    assert s.db.verify_audit_chain()[0] is False


def test_closing_a_registered_version_is_allowed_and_audited(settings_factory, tmp_path):
    """Procédure normale d'évolution : fermer la v1 déjà utilisée (valid_to) et ajouter une v2."""
    settings = settings_factory()
    ref = tmp_path / "referentiel"
    shutil.copytree(settings.referentiel_dir, ref)
    settings.referentiel_dir = ref
    s = Services(settings)
    s.scan()
    run1 = s.run()
    old = _by_rule(s.results(run1))["LU-UCITS-43-3-EMETTEUR-PUBLIC-35"]
    f1 = next(ref.rglob("02_souverain_35.v1.yaml"))
    text = f1.read_text(encoding="utf-8")
    f1.write_text(text.replace("valid_to: null", 'valid_to: "2026-01-01"'), encoding="utf-8")
    (f1.parent / "02_souverain_35.v2.yaml").write_text(
        text.replace("version: 1", "version: 2").replace('value: "0.35"', 'value: "0.25"')
            .replace('valid_from: "2010-12-17"', 'valid_from: "2026-01-01"'), encoding="utf-8")
    run2 = s.run()
    assert not s.referentiel.errors
    new = _by_rule(s.results(run2))["LU-UCITS-43-3-EMETTEUR-PUBLIC-35"]
    assert new["rule"]["version"] == 2 and new["indicative_status"] == "DEPASSEMENT"
    again = _by_rule(s.results(run1))["LU-UCITS-43-3-EMETTEUR-PUBLIC-35"]
    assert again["fingerprint"] == old["fingerprint"] and again["rule"]["version"] == 1
    assert any(a["action"] == "regle_fin_validite" for a in s.db.q("SELECT action FROM audit_log"))
