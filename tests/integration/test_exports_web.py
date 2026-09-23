import openpyxl
import pytest
from fastapi.testclient import TestClient

from opcvm_ratios.exports import export_excel, export_pdf
from opcvm_ratios.services import Services
from opcvm_ratios.web.app import create_app


@pytest.fixture
def services(settings_factory):
    s = Services(settings_factory())
    s.scan()
    s.run()
    return s


def test_excel_export_structure_and_formulas(services):
    path = export_excel(services, services.latest_run_id())
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Synthèse", "Ratios", "Calculs", "Données sources", "Référentiel réglementaire",
                             "Anomalies", "Métadonnées"]
    calc = wb["Calculs"]
    formulas = [c.value for row in calc.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=")]
    assert any(f.startswith("=SUMPRODUCT(") for f in formulas)
    ratios = wb["Ratios"]
    rf = [ratios.cell(r, 11).value for r in range(2, ratios.max_row + 1)]
    assert any(isinstance(v, str) and v.startswith("=MAX(Calculs!") for v in rf)
    assert any(isinstance(v, str) and v.startswith("=SUMIF(Calculs!") for v in rf)
    assert any(isinstance(v, str) and v.startswith("=Calculs!") for v in rf)
    assert wb["Données sources"].max_row == 28  # en-tête + 27 lignes du fonds test


def test_excel_formulas_recompute_engine_values(services):
    formulas = pytest.importorskip("formulas")
    path = export_excel(services, services.latest_run_id())
    sol = formulas.ExcelModel().loads(str(path)).finish().calculate()
    vals = {}
    for k, v in sol.items():
        try:
            vals[k.split("]")[1].replace("'", "").upper()] = v.value[0][0]
        except Exception:
            pass
    wb = openpyxl.load_workbook(path)
    ws = wb["Ratios"]
    checked = 0
    for r in range(2, ws.max_row + 1):
        diff = vals.get(f"RATIOS!L{r}")
        if isinstance(diff, (int, float)):
            assert abs(diff) < 1e-9, (ws.cell(r, 5).value, diff)
            checked += 1
    assert checked >= 10


def test_pdf_export(services):
    path = export_pdf(services, services.latest_run_id())
    data = path.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 5000
    assert services.db.q1("SELECT COUNT(*) AS n FROM export")["n"] == 1


def test_web_pages(services):
    client = TestClient(create_app(services=services))
    rid = services.db.q1("SELECT id FROM ratio_result LIMIT 1")["id"]
    for url in ["/", "/funds", "/fund/999901", f"/result/{rid}", "/history",
                "/history?fund_id=999901&rule_id=LU-UCITS-43-EMETTEUR-5-10-40", "/files", "/referentiel",
                "/referentiel/LU-UCITS-43-EMETTEUR-5-10-40/1", "/exports", "/audit"]:
        r = client.get(url)
        assert r.status_code == 200, url
        assert "http://" not in r.text.replace("http://127.0.0.1", "") and "https://" not in r.text, url
    r = client.post("/exports", data={"run_id": services.latest_run_id(), "fmt": "xlsx", "funds": "999901"})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    r = client.post("/referentiel/LU-UCITS-43-EMETTEUR-5-10-40/1/validate",
                    data={"validated_by": "Contrôleur", "comment": "ok", "confirm": "1"}, follow_redirects=False)
    assert r.status_code == 303
    assert next(x for x in services.referentiel.rules if x.rule_id == "LU-UCITS-43-EMETTEUR-5-10-40").validated
