from decimal import Decimal
from pathlib import Path

import openpyxl

from opcvm_ratios.ingestion.profiles import load_profile
from opcvm_ratios.ingestion.readers import read_table
from opcvm_ratios.normalization.normalizer import normalize

ROOT = Path(__file__).resolve().parents[2]
PROFILE = load_profile(ROOT / "config" / "mappings" / "efa_standard.yaml")
POS = PROFILE.file_types["positions"]
FIX = ROOT / "tests" / "fixtures" / "fonds_test"


def test_csv_cp1252_and_mapping():
    t = read_table(FIX / "ffpos1_TST_2026_06_30.csv", POS.expected_headers())
    assert t.encoding in ("utf-8-sig", "cp1252") and t.delimiter == ","
    nt = normalize(t, POS, PROFILE)
    assert not nt.missing_required and not nt.blocking
    first = nt.records[0]
    assert first["_row"] == 2 and first["instrument_type"] == "equity"
    assert first["total_market_value"] == Decimal("8420000.00")
    futures = [r for r in nt.records if r["src_category"] == "FUTU" and r["stock_type"] == "HOBI"]
    assert futures and all(not r["balance_sheet"] for r in futures)


def test_semicolon_csv_with_missing_required_column(tmp_path):
    p = tmp_path / "ffpos1_TST_2026_06_30.csv"
    lines = (FIX / "ffpos1_TST_2026_06_30.csv").read_text(encoding="cp1252").splitlines()
    header = lines[0].split(",")
    drop = header.index("Total_Market_Value")
    import csv, io
    rows = list(csv.reader(io.StringIO("\n".join(lines))))
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    for r in rows:
        w.writerow([c for i, c in enumerate(r) if i != drop])
    p.write_text(out.getvalue(), encoding="utf-8")
    t = read_table(p, POS.expected_headers())
    assert t.delimiter == ";"
    nt = normalize(t, POS, PROFILE)
    assert "total_market_value" in nt.missing_required
    assert any(a.code == "COLONNE_ABSENTE" for a in nt.anomalies) and nt.blocking


def test_xlsx_with_title_rows_and_alias_headers(tmp_path):
    p = tmp_path / "ffnav1_TST_2026_06_30.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Rapport administrateur"])
    ws.append([])
    ws.append(["Sub-fund_code", "Share_code", "Valuation_date", "Sub-fund_currency", "Net_assets_share_type",
               "Total_Net_Assets"])
    ws.append(["999901", "A", "30/06/2026", "EUR", 60000000.0, 100000000.0])
    ws.append(["999901", "B", "30/06/2026", "EUR", "illisible", 100000000.0])
    wb.save(p)
    spec = PROFILE.file_types["nav"]
    t = read_table(p, spec.expected_headers())
    assert t.header_line == 3
    nt = normalize(t, spec, PROFILE)
    assert nt.records[0]["total_net_assets"] == Decimal("100000000")
    assert nt.records[1]["share_net_assets"] is None
    assert any(a.code == "DONNEE_ILLISIBLE" and a.row_ref == "ligne 5" for a in nt.anomalies)


def test_unclassified_line_is_flagged(tmp_path):
    src = (FIX / "ffpos1_TST_2026_06_30.csv").read_text(encoding="cp1252").splitlines()
    src[1] = src[1].replace(",VMOB,", ",XXXX,", 1)
    p = tmp_path / "ffpos1_TST_2026_06_30.csv"
    p.write_text("\n".join(src), encoding="cp1252")
    nt = normalize(read_table(p, POS.expected_headers()), POS, PROFILE)
    assert nt.records[0]["instrument_type"] == "unknown"
    assert any(a.code == "INSTRUMENT_NON_CLASSE" for a in nt.anomalies)
