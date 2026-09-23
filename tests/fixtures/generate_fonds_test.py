"""Génère le jeu de données FICTIF « FONDS TEST » (format administrateur ffxxx1_TST_2026_06_30.csv).

Toutes les données sont inventées : elles servent uniquement à vérifier les calculs à la main.
Actif net = 100 000 000,00 EUR, ce qui rend chaque poids lisible directement (1 000 000 € = 1 %).
Le détail du calcul manuel attendu figure dans expected.yaml.

Usage : python tests/fixtures/generate_fonds_test.py
"""
from __future__ import annotations

import csv
from pathlib import Path

OUT = Path(__file__).parent / "fonds_test"
DATE = "30/06/2026"
FN_DATE = "2026_06_30"


def isin(prefix: str, body: str) -> str:
    """Construit un ISIN valide (clé de contrôle calculée)."""
    base = (prefix + body).upper()
    digits = "".join(str(int(c, 36)) for c in base)
    total, double = 0, True
    for ch in reversed(digits):
        d = int(ch)
        if double:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        double = not double
    return base + str((10 - total % 10) % 10)


POS_HEADERS = ["Fund_code", "Fund_Long_Name", "Fund_ccy", "Sub-Fund_Code", "Sub-Fund_long_name", "Sub-Fund_ccy",
               "Depositary_Bank", "Valuation_date", "Instr_code", "Instr_Category", "Instr_long_name", "Eco_sect_code",
               "Geo_sect_code", "Acc_sort_code", "Instr_evaluation_ccy", "Price_expr", "Maturity_date", "ISIN",
               "Stock_Type", "Line_status", "Price", "Market_Value", "Market_Value_in_Instr_CCY", "Quantity",
               "Accrued_Interest", "Total_Market_Value", "Market_Val+Accr_Int_NAV_%", "Market name", "type_valeur",
               "contrepartie", "Convers_factor", "Categ_sous_jacent", "Code_sous_jacent", "underlying_Long_Name"]

NAV = 100_000_000


def line(code, cat, name, acc, tv, stock, mv, isin_code="-1", qty=None, price=None, market="", cpty="",
         ccy="EUR", accrued=0, price_expr="1", maturity=""):
    qty = qty if qty is not None else mv
    price = price if price is not None else 1
    tmv = mv + accrued if stock == "AD1" else mv
    return {"Fund_code": "9999", "Fund_Long_Name": "SICAV TEST", "Fund_ccy": "EUR", "Sub-Fund_Code": "999901",
            "Sub-Fund_long_name": "SICAV TEST - FONDS TEST", "Sub-Fund_ccy": "EUR", "Depositary_Bank": "01",
            "Valuation_date": DATE, "Instr_code": code, "Instr_Category": cat, "Instr_long_name": name,
            "Eco_sect_code": "-1", "Geo_sect_code": "-1", "Acc_sort_code": acc, "Instr_evaluation_ccy": ccy,
            "Price_expr": price_expr, "Maturity_date": maturity, "ISIN": isin_code, "Stock_Type": stock,
            "Line_status": "PROPRE", "Price": f"{price}", "Market_Value": f"{mv:.2f}",
            "Market_Value_in_Instr_CCY": f"{mv:.2f}", "Quantity": f"{qty}", "Accrued_Interest": f"{accrued:.2f}",
            "Total_Market_Value": f"{tmv:.2f}", "Market_Val+Accr_Int_NAV_%": f"{tmv / NAV * 100:.10f}",
            "Market name": market, "type_valeur": tv, "contrepartie": cpty, "Convers_factor": "", "Categ_sous_jacent": "",
            "Code_sous_jacent": "", "underlying_Long_Name": ""}


ISINS = {k: isin(p, b) for k, (p, b) in {
    "X": ("FR", "000000X01"), "Y1": ("FR", "000000Y01"), "Y2": ("FR", "000000Y02"), "Z": ("XS", "000000Z01"),
    "W": ("DE", "000000W01"), "V": ("NL", "000000V01"), "BTF1": ("FR", "0000BTF01"), "BTF2": ("FR", "0000BTF02"),
    "BTF3": ("FR", "0000BTF03"), "OPC": ("LU", "000000OPC"),
    **{f"D{i}": ("IT", f"00000D{i:03d}") for i in range(10)}}.items()}

EURONEXT = "PA (RT) - Euronext Paris"


def positions() -> list[dict]:
    rows = [
        # Émetteur X : 8 420 000 € -> 8,42 %
        line("X1", "VMOB", "Société X", "010101C", "1010", "AD1", 8_420_000, ISINS["X"], 84_200, 100, EURONEXT),
        # Émetteur Y (groupe G) : 6 000 000 + 3 720 000 = 9 720 000 € -> 9,72 %
        line("Y1", "VMOB", "Société Y action A", "010101C", "1010", "AD1", 6_000_000, ISINS["Y1"], 60_000, 100, EURONEXT),
        line("Y2", "VMOB", "Société Y action B", "010101C", "1010", "AD1", 3_720_000, ISINS["Y2"], 37_200, 100, EURONEXT),
        # Émetteur Z : obligation 12 400 000 € (dont coupon couru 100 000 €) -> 12,40 %
        line("Z1", "VMOB", "Z Finance 3% 2030", "010208C", "2090", "AD1", 12_300_000, ISINS["Z"], 12_000_000,
             102.5, EURONEXT, accrued=100_000, price_expr="2", maturity="30/06/2030"),
        # Émetteur W : 4 250 000 € sur un marché non éligible -> 4,25 %
        line("W1", "VMOB", "Société W", "010101C", "1010", "AD1", 4_250_000, ISINS["W"], 42_500, 100, "HORS COTE"),
        # Émetteur V (groupe G) : 5 000 000 € -> exactement 5,00 % (non retenu dans la somme des > 5 %)
        line("V1", "VMOB", "Société V", "010101C", "1010", "AD1", 5_000_000, ISINS["V"], 50_000, 100, EURONEXT),
        # État français : 3 BTF, 10 + 12 + 8 = 30 000 000 € -> 30,00 %
        line("BTF1", "VMOB", "France 0% 30.09.26", "010208C", "2090", "AD1", 10_000_000, ISINS["BTF1"], 10_000_000,
             100, "BGN (BB) - Bloomberg Generic", price_expr="2", maturity="30/09/2026"),
        line("BTF2", "VMOB", "France 0% 31.10.26", "010208C", "2090", "AD1", 12_000_000, ISINS["BTF2"], 12_000_000,
             100, "BGN (BB) - Bloomberg Generic", price_expr="2", maturity="31/10/2026"),
        line("BTF3", "VMOB", "France 0% 30.11.26", "010208C", "2090", "AD1", 8_000_000, ISINS["BTF3"], 8_000_000,
             100, "BGN (BB) - Bloomberg Generic", price_expr="2", maturity="30/11/2026"),
        # OPCVM cible : 7 000 000 € -> 7,00 %
        line("OPC1", "VMOB", "OPCVM Cible Actions", "010600N", "1341", "AD1", 7_000_000, ISINS["OPC"], 70_000, 100),
    ]
    # 10 émetteurs divers à 2 087 000 € (2,087 %) chacun = 20 870 000 €
    for i in range(10):
        rows.append(line(f"D{i}", "VMOB", f"Société Diverse {i}", "010101C", "1010", "AD1", 2_087_000,
                         ISINS[f"D{i}"], 20_870, 100, "MI (RT) - Milan SE"))
    rows += [
        # Trésorerie : +3 000 000 € et -500 000 € (découvert)
        line("CASH-EUR", "TRES", "Banque Test", "TR500100", "", "AD1", 3_000_000, cpty="Banque Test"),
        line("CASH-USD", "TRES", "Banque Test", "TR500100", "", "AD1", -500_000, cpty="Banque Test"),
        # Frais à payer : -210 000 €
        line("FEES", "TRES", "Management fees", "TR221100", "-1", "AD1", -210_000),
        # Futures : résultats latents (+100 000 / -50 000) et engagements hors bilan (+20 000 000 / -5 000 000)
        line("FUT-A", "FUTU", "Index A FUT 09/26", "050104", "-1", "AD1", 100_000, cpty="Banque Test"),
        line("FUT-A", "FUTU", "Index A FUT 09/26", "050104", "-1", "HOBI", 20_000_000, cpty="Banque Test"),
        line("FUT-B", "FUTU", "Index B FUT 09/26", "050104", "-1", "AD1", -50_000, cpty="Banque Test"),
        line("FUT-B", "FUTU", "Index B FUT 09/26", "050104", "-1", "HOBI", -5_000_000, cpty="Banque Test"),
    ]
    return rows


def write(path: Path, headers, rows, delimiter=",", encoding="utf-8"):
    with open(path, "w", encoding=encoding, newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, delimiter=delimiter)
        w.writeheader()
        w.writerows(rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = positions()
    total = sum(float(r["Total_Market_Value"]) for r in rows if r["Stock_Type"] == "AD1")
    assert abs(total - NAV) < 0.005, total
    write(OUT / f"ffpos1_TST_{FN_DATE}.csv", POS_HEADERS, rows, encoding="cp1252")
    nav_headers = ["Sub-fund_code", "Share_code", "Valuation_date", "Sub-fund_currency", "Net_assets_share_type",
                   "Numbre_of_Shares_outstanding", "NAV_share", "Isin_code", "Sub_fund_long_name", "Total_Net_Assets"]
    write(OUT / f"ffnav1_TST_{FN_DATE}.csv", nav_headers, [
        {"Sub-fund_code": "999901", "Share_code": "A", "Valuation_date": DATE, "Sub-fund_currency": "EUR",
         "Net_assets_share_type": "60000000.00", "Numbre_of_Shares_outstanding": "600000", "NAV_share": "100.00",
         "Isin_code": isin("LU", "000TESTA1"), "Sub_fund_long_name": "SICAV TEST - FONDS TEST",
         "Total_Net_Assets": "100000000.00"},
        {"Sub-fund_code": "999901", "Share_code": "B", "Valuation_date": DATE, "Sub-fund_currency": "EUR",
         "Net_assets_share_type": "40000000.00", "Numbre_of_Shares_outstanding": "400000", "NAV_share": "100.00",
         "Isin_code": isin("LU", "000TESTB1"), "Sub_fund_long_name": "SICAV TEST - FONDS TEST",
         "Total_Net_Assets": "100000000.00"}])
    write(OUT / f"ffcur1_TST_{FN_DATE}.csv", ["Cotation_date", "Fixing_code", "Fixing_currency", "Currency_code",
                                              "Exchange_rate", "Quotity"],
          [{"Cotation_date": DATE, "Fixing_code": "FX", "Fixing_currency": "EUR", "Currency_code": "EUR",
            "Exchange_rate": "1", "Quotity": "1"}])
    # Référentiel émetteurs du fonds test (fictif)
    issuers = [
        (ISINS["X"], "ISIN", "EM-X", "Société X", "GRP-X", "Groupe X", "entreprise"),
        (ISINS["Y1"], "ISIN", "EM-Y", "Société Y", "GRP-G", "Groupe G", "entreprise"),
        (ISINS["Y2"], "ISIN", "EM-Y", "Société Y", "GRP-G", "Groupe G", "entreprise"),
        (ISINS["Z"], "ISIN", "EM-Z", "Z Finance", "GRP-Z", "Groupe Z", "entreprise"),
        (ISINS["W"], "ISIN", "EM-W", "Société W", "GRP-W", "Groupe W", "entreprise"),
        (ISINS["V"], "ISIN", "EM-V", "Société V", "GRP-G", "Groupe G", "entreprise"),
        (ISINS["BTF1"], "ISIN", "FR-ETAT", "République française", "", "", "souverain_ue"),
        (ISINS["BTF2"], "ISIN", "FR-ETAT", "République française", "", "", "souverain_ue"),
        (ISINS["BTF3"], "ISIN", "FR-ETAT", "République française", "", "", "souverain_ue"),
        (ISINS["OPC"], "ISIN", "OPC-CIBLE", "OPCVM Cible Actions", "", "", "opcvm"),
        ("Banque Test", "CONTREPARTIE", "BQ-TEST", "Banque Test", "GRP-BQ", "Groupe Banque Test", "etablissement_credit_ue"),
    ] + [(ISINS[f"D{i}"], "ISIN", f"EM-D{i}", f"Société Diverse {i}", f"GRP-D{i}", f"Groupe D{i}", "entreprise")
         for i in range(10)]
    with open(OUT / "emetteurs.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["identifiant", "type_identifiant", "emetteur_id", "emetteur_nom", "groupe_id", "groupe_nom",
                    "type_emetteur", "encours_emis", "statut", "source", "commentaire"])
        for r in issuers:
            w.writerow([*r, "", "valide", "Jeu de test fictif", ""])
    with open(OUT / "marches.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["marche", "eligibilite", "statut", "source", "commentaire"])
        w.writerow([EURONEXT, "marche_reglemente_ue", "valide", "Jeu de test fictif", ""])
        w.writerow(["MI (RT) - Milan SE", "marche_reglemente_ue", "valide", "Jeu de test fictif", ""])
        w.writerow(["HORS COTE", "non_eligible", "valide", "Jeu de test fictif", ""])
    print("Jeu de test écrit dans", OUT)


if __name__ == "__main__":
    main()
