"""Interface web locale (FastAPI + gabarits Jinja2, aucune ressource externe)."""
from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import DISCLAIMER, ENGINE_VERSION, jsonutil
from ..control import SEVERITY, STATUS_LABELS
from ..numbers import fmt_amount, fmt_date, fmt_number, fmt_pct, fmt_points
from ..referentials import ISSUER_TYPES
from ..rules.loader import validate_rule
from ..rules.model import CATEGORY_LABELS
from ..services import Services
from ..settings import Settings

HERE = Path(__file__).parent


def _dec(v):
    return None if v in (None, "", "SANS_OBJET") else Decimal(str(v))


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    svc = services or Services(settings)
    app = FastAPI(title="Contrôle des ratios OPCVM", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    env = tpl.env
    env.filters["pct"] = lambda v, d=2: fmt_pct(_dec(v), d)
    env.filters["pts"] = lambda v: fmt_points(_dec(v))
    env.filters["amt"] = lambda v, c="EUR": fmt_amount(_dec(v), c)
    env.filters["num"] = lambda v, d=0: fmt_number(_dec(v), d)
    env.filters["date"] = fmt_date
    env.filters["status"] = lambda s: STATUS_LABELS.get(s, s or "")
    env.filters["cat"] = lambda c: CATEGORY_LABELS.get(c, c)
    env.filters["op"] = lambda o: {"<=": "≤", "<": "<", ">=": "≥", ">": ">"}.get(o, o)
    env.filters["short"] = lambda s, n=12: (s or "")[:n]
    env.globals.update(DISCLAIMER=DISCLAIMER, ENGINE_VERSION=ENGINE_VERSION, company=svc.settings.company_name)

    def limval(lim, key="value"):
        v = lim.get(key)
        if v is None:
            return "—"
        if lim.get("unit") == "count":
            return fmt_number(_dec(v), 0)
        return fmt_points(_dec(v)) if key == "margin" else fmt_pct(_dec(v))

    env.globals["limval"] = limval

    def render(request, name, **ctx):
        return tpl.TemplateResponse(request, name, {"request": request, **ctx})

    def latest_results_by_fund():
        """Dernier calcul disponible pour chaque fonds."""
        out = OrderedDict()
        rows = svc.db.q("SELECT fund_id, MAX(run_id) AS run_id FROM ratio_result GROUP BY fund_id ORDER BY fund_id")
        for row in rows:
            out[row["fund_id"]] = svc.results(row["run_id"], row["fund_id"])
        return out

    def counts(results):
        c = {s: 0 for s in STATUS_LABELS}
        for r in results:
            c[r["status"]] = c.get(r["status"], 0) + 1
        c["indicative_breach"] = sum(1 for r in results if r["status"] == "A_CONFIRMER"
                                     and r["indicative_status"] == "DEPASSEMENT")
        c["total"] = len(results)
        return c

    # ------------------------------------------------------------------ tableau de bord
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, msg: str | None = None):
        data = latest_results_by_fund()
        rows = []
        for fid, res in data.items():
            f = svc.funds.get(fid)
            rows.append({"fund_id": fid, "name": f.name if f else res[0]["fund"]["name"], "date": res[0]["data_date"],
                         "run_id": res[0]["_run_id"], "counts": counts(res),
                         "computed_at": res[0].get("computed_at")})
        pending = [f for f in svc.funds.values() if f.fund_id not in data]
        files_err = svc.db.q("SELECT COUNT(*) AS n FROM source_file WHERE status IN ('erreur','illisible')")[0]["n"]
        return render(request, "dashboard.html", rows=rows, pending=pending, files_err=files_err, msg=msg,
                      ref_errors=svc.referentiel.errors + svc.refs.errors)

    @app.post("/scan")
    def scan():
        rep = svc.scan()
        msg = (f"{len(rep.processed)} fichier(s) traité(s), {rep.skipped_duplicates} déjà connu(s), "
               f"{len(rep.errors)} en erreur, {len(rep.snapshots)} jeu(x) de données constitué(s)")
        return RedirectResponse(f"/?msg={msg}", status_code=303)

    @app.post("/run")
    def run(scan_first: str = Form("1")):
        if scan_first == "1":
            svc.scan()
        run_id = svc.run()
        return RedirectResponse(f"/?msg=Calcul #{run_id} terminé", status_code=303)

    # ------------------------------------------------------------------ fonds
    @app.get("/funds", response_class=HTMLResponse)
    def funds(request: Request):
        rows = []
        for f in svc.funds.values():
            snap = svc.db.q1("SELECT * FROM snapshot WHERE fund_id = ? AND status != 'remplace' ORDER BY data_date DESC, id DESC LIMIT 1",
                             (f.fund_id,))
            files = []
            if snap:
                content = jsonutil.loads(snap["content_json"])
                for x in content["files"]:
                    sf = svc.db.q1("SELECT mtime, detected_at FROM source_file WHERE sha256 = ? AND file_name = ?",
                                   (x["sha256"], x["file_name"]))
                    files.append({**x, "mtime": sf["mtime"] if sf else None})
            rows.append({"fund": f, "snap": snap, "files": files,
                         "identifier": ", ".join(f"{k} {v}" for k, v in f.identifiers.items()),
                         "shares": f.share_classes})
        return render(request, "funds.html", rows=rows)

    @app.get("/fund/{fund_id}", response_class=HTMLResponse)
    def fund(request: Request, fund_id: str, run: int | None = None):
        f = svc.funds.get(fund_id)
        if run is None:
            row = svc.db.q1("SELECT MAX(run_id) AS r FROM ratio_result WHERE fund_id = ?", (fund_id,))
            run = row["r"] if row else None
        results = svc.results(run, fund_id) if run else []
        cats = OrderedDict((c, []) for c in CATEGORY_LABELS)
        for r in sorted(results, key=lambda x: (-SEVERITY.get(x["status"], 0), x["rule"]["id"])):
            cats.setdefault(r["rule"]["category"], []).append(r)
        cats = OrderedDict((k, v) for k, v in cats.items() if v)
        snap = None
        anomalies = []
        if results:
            ci = svc.db.q1("SELECT * FROM calc_input WHERE id = ?", (results[0]["_calc_input_id"],))
            snap = svc.snapshot_content(ci["snapshot_id"])
            anomalies = list(svc.db.anomalies("snapshot", ci["snapshot_id"]))
            for iid in jsonutil.loads(svc.db.q1("SELECT import_ids_json FROM snapshot WHERE id = ?",
                                                (ci["snapshot_id"],))["import_ids_json"]):
                anomalies += list(svc.db.anomalies("import", iid))
        attrs = None
        if f and results:
            import datetime as dt
            p = f.profile_at(dt.date.fromisoformat(str(results[0]["data_date"])))
            attrs = p[0].attributes if len(p) == 1 else None
        runs = svc.db.q("SELECT DISTINCT run_id FROM ratio_result WHERE fund_id = ? ORDER BY run_id DESC", (fund_id,))
        return render(request, "fund.html", fund=f, fund_id=fund_id, run_id=run, cats=cats, counts=counts(results),
                      snap=snap, anomalies=anomalies, attrs=attrs, runs=[r["run_id"] for r in runs])

    # ------------------------------------------------------------------ détail ratio
    @app.get("/result/{result_id}", response_class=HTMLResponse)
    def result(request: Request, result_id: int):
        r = svc.result(result_id)
        if not r:
            raise HTTPException(404, "Résultat introuvable")
        ci = svc.calc_input(r["_calc_input_id"])
        lines = {p["line_id"]: p for p in ci.get("positions", [])}
        out = r.get("output") or {}
        used = [lines[l] for l in out.get("included_lines", []) if l in lines]
        history = svc.db.q("SELECT id, run_id, data_date, status, indicative_status, rule_version, fingerprint, computed_at "
                           "FROM ratio_result WHERE fund_id = ? AND rule_id = ? ORDER BY id DESC LIMIT 30",
                           (r["fund"]["id"], r["rule"]["id"]))
        return render(request, "result.html", r=r, out=out, used=used, lines=lines, history=history,
                      issuer_types=ISSUER_TYPES)

    # ------------------------------------------------------------------ historique
    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request, fund_id: str | None = None, rule_id: str | None = None):
        runs = []
        for run in svc.db.q("SELECT * FROM calc_run ORDER BY id DESC LIMIT 100"):
            res = svc.db.q("SELECT fund_id, status, indicative_status FROM ratio_result WHERE run_id = ?", (run["id"],))
            c = {s: sum(1 for x in res if x["status"] == s) for s in STATUS_LABELS}
            runs.append({"run": run, "counts": c, "n": len(res), "funds": sorted({x["fund_id"] for x in res})})
        series = []
        if fund_id and rule_id:
            for row in svc.db.q("SELECT id, run_id, data_date, status, indicative_status, rule_version, result_json "
                                "FROM ratio_result WHERE fund_id = ? AND rule_id = ? ORDER BY data_date, id",
                                (fund_id, rule_id)):
                res = jsonutil.loads(row["result_json"])
                lim = (res.get("limits") or [{}])[0]
                series.append({"id": row["id"], "run_id": row["run_id"], "date": row["data_date"], "status": row["status"],
                               "indicative": row["indicative_status"], "version": row["rule_version"],
                               "value": lim.get("value"), "limit": lim.get("limit"), "unit": lim.get("unit")})
        rule_ids = svc.referentiel.rule_ids()
        return render(request, "history.html", runs=runs, series=series, fund_id=fund_id, rule_id=rule_id,
                      funds=list(svc.funds.values()), rule_ids=rule_ids)

    # ------------------------------------------------------------------ fichiers
    @app.get("/files", response_class=HTMLResponse)
    def files(request: Request):
        rows = svc.db.q("SELECT * FROM source_file ORDER BY id DESC LIMIT 500")
        imports = {r["source_file_id"]: r for r in svc.db.q("SELECT id, source_file_id, status, n_rows, meta_json FROM import_batch")}
        ano = {}
        for a in svc.db.q("SELECT * FROM anomaly ORDER BY id DESC LIMIT 2000"):
            ano.setdefault((a["scope"], a["ref_id"]), []).append(a)
        dataset_ano = svc.db.q("SELECT * FROM anomaly WHERE scope IN ('dataset','scan') ORDER BY id DESC LIMIT 200")
        snaps = svc.db.q("SELECT id, fund_id, data_date, sha256, status, created_at FROM snapshot ORDER BY id DESC LIMIT 100")
        return render(request, "files.html", rows=rows, imports=imports, ano=ano, dataset_ano=dataset_ano,
                      snaps=snaps, source_dir=svc.settings.source_dir, jsonutil=jsonutil)

    # ------------------------------------------------------------------ référentiel
    @app.get("/referentiel", response_class=HTMLResponse)
    def referentiel(request: Request, msg: str | None = None):
        svc.reload()
        return render(request, "referentiel.html", rules=svc.referentiel.rules, errors=svc.referentiel.errors,
                      ref_errors=svc.refs.errors, sources=svc.referentiel.sources, msg=msg,
                      rules_dir=svc.settings.referentiel_dir / "rules")

    @app.get("/referentiel/{rule_id}/{version}", response_class=HTMLResponse)
    def rule_detail(request: Request, rule_id: str, version: int):
        rule = next((r for r in svc.referentiel.rules if r.rule_id == rule_id and r.version == version), None)
        if not rule:
            raise HTTPException(404, "Règle introuvable")
        validations = svc.db.q("SELECT * FROM rule_validation WHERE rule_id = ? AND version = ? ORDER BY id DESC",
                               (rule_id, version))
        versions = svc.referentiel.versions(rule_id)
        return render(request, "rule.html", rule=rule, validations=validations, versions=versions,
                      content_yaml=jsonutil.dumps(rule.content, pretty=True))

    @app.post("/referentiel/{rule_id}/{version}/validate")
    def rule_validate(rule_id: str, version: int, validated_by: str = Form(...), comment: str = Form(""),
                      confirm: str = Form("")):
        rule = next((r for r in svc.referentiel.rules if r.rule_id == rule_id and r.version == version), None)
        if not rule:
            raise HTTPException(404, "Règle introuvable")
        if confirm != "1" or not validated_by.strip():
            raise HTTPException(400, "Validation incomplète : nom et confirmation obligatoires")
        validate_rule(svc.db, rule, validated_by.strip(), comment.strip())
        svc.reload()
        return RedirectResponse(f"/referentiel?msg=Règle {rule_id} v{version} validée par {validated_by}", status_code=303)

    # ------------------------------------------------------------------ exports
    @app.get("/exports", response_class=HTMLResponse)
    def exports(request: Request):
        runs = svc.db.q("SELECT * FROM calc_run WHERE status = 'termine' ORDER BY id DESC LIMIT 50")
        past = svc.db.q("SELECT * FROM export ORDER BY id DESC LIMIT 100")
        return render(request, "exports.html", runs=runs, past=past, funds=list(svc.funds.values()))

    @app.post("/exports")
    def do_export(run_id: int = Form(...), fmt: str = Form("pdf"), funds: list[str] | None = Form(None)):
        from ..exports import export_excel, export_pdf
        fund_ids = [f for f in (funds or []) if f] or None
        path = export_pdf(svc, run_id, fund_ids) if fmt == "pdf" else export_excel(svc, run_id, fund_ids)
        media = "application/pdf" if fmt == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return FileResponse(path, media_type=media, filename=path.name)

    @app.get("/exports/{export_id}")
    def get_export(export_id: int):
        row = svc.db.q1("SELECT * FROM export WHERE id = ?", (export_id,))
        if not row or not Path(row["file_path"]).exists():
            raise HTTPException(404, "Export introuvable")
        p = Path(row["file_path"])
        return FileResponse(p, filename=p.name)

    # ------------------------------------------------------------------ audit
    @app.get("/audit", response_class=HTMLResponse)
    def audit(request: Request):
        ok, bad = svc.db.verify_audit_chain()
        rows = svc.db.q("SELECT * FROM audit_log ORDER BY id DESC LIMIT 500")
        return render(request, "audit.html", rows=rows, ok=ok, bad=bad)

    app.state.services = svc
    return app
