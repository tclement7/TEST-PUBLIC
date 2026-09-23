"""Ligne de commande :  python -m opcvm_ratios <commande>"""
from __future__ import annotations

import argparse
import sys

from . import DISCLAIMER


def _services(args):
    from .services import Services
    from .settings import load_settings
    return Services(load_settings(args.settings) if args.settings else None)


def cmd_scan(args):
    s = _services(args)
    rep = s.scan()
    print(f"Fichiers traités : {len(rep.processed)} ; doublons ignorés : {rep.skipped_duplicates} ; "
          f"trop récents : {len(rep.skipped_recent)} ; erreurs : {len(rep.errors)} ; snapshots : {rep.snapshots}")
    for e in rep.errors:
        print(f"  ERREUR {e.get('file')}: {e.get('error') or e.get('message')}")


def cmd_run(args):
    s = _services(args)
    if not args.no_scan:
        cmd_scan(args)
    run_id = s.run(fund_ids=args.fund or None)
    res = s.results(run_id)
    print(f"Calcul #{run_id} : {len(res)} ratios")
    for r in res:
        lim = r.get("limits") or []
        val = ", ".join(f"{l['label']}={l['value']}" for l in lim if l.get("value") is not None)
        print(f"  [{r['status']:<15}] ({r['indicative_status']:<15}) {r['fund']['id']} {r['rule']['id']} {val}")
    print(DISCLAIMER)


def cmd_export(args):
    s = _services(args)
    run_id = args.run or s.latest_run_id()
    if not run_id:
        sys.exit("Aucun calcul disponible : lancer d'abord « run ».")
    from .exports import export_excel, export_pdf
    if args.format in ("xlsx", "both"):
        print("Excel :", export_excel(s, run_id, args.fund or None))
    if args.format in ("pdf", "both"):
        print("PDF   :", export_pdf(s, run_id, args.fund or None))


def cmd_rules(args):
    s = _services(args)
    for r in s.referentiel.rules:
        print(f"{r.rule_id} v{r.version} [{'validée' if r.validated else 'brouillon'}] {r.content_sha[:12]} – {r.name}")
    for e in s.referentiel.errors:
        print("ERREUR :", e)


def cmd_draft_issuers(args):
    s = _services(args)
    from .referentials import draft_issuers_file
    n = 0
    for snap in s.engine.latest_snapshots():
        content = s.snapshot_content(snap["id"])
        n += draft_issuers_file(content["positions"], s.settings.issuers_file, s.refs)
        s.reload()
    print(f"{n} ligne(s) ajoutée(s) au brouillon {s.settings.issuers_file} (statut a_valider)")


def cmd_serve(args):
    import uvicorn
    from .settings import load_settings
    from .web.app import create_app
    settings = load_settings(args.settings) if args.settings else load_settings()
    host = args.host or settings.web_host
    print(f"Interface disponible sur http://{host}:{settings.web_port}  (Ctrl+C pour arrêter)")
    uvicorn.run(create_app(settings), host=host, port=settings.web_port, log_level="warning")


def cmd_verify_audit(args):
    s = _services(args)
    ok, bad = s.db.verify_audit_chain()
    print("Journal d'audit intègre" if ok else f"Journal d'audit ALTÉRÉ à partir de l'entrée #{bad}")
    sys.exit(0 if ok else 1)


def main(argv=None):
    p = argparse.ArgumentParser(prog="opcvm_ratios", description="Contrôle des ratios réglementaires OPCVM (local)")
    p.add_argument("--settings", help="chemin du fichier settings.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan", help="détecter et importer les nouveaux fichiers").set_defaults(func=cmd_scan)
    r = sub.add_parser("run", help="importer puis calculer les ratios")
    r.add_argument("--fund", action="append")
    r.add_argument("--no-scan", action="store_true")
    r.set_defaults(func=cmd_run)
    e = sub.add_parser("export", help="exporter un calcul (Excel / PDF)")
    e.add_argument("--run", type=int)
    e.add_argument("--fund", action="append")
    e.add_argument("--format", choices=["xlsx", "pdf", "both"], default="both")
    e.set_defaults(func=cmd_export)
    sub.add_parser("rules", help="lister le référentiel réglementaire").set_defaults(func=cmd_rules)
    sub.add_parser("draft-issuers", help="générer un brouillon de référentiel émetteurs").set_defaults(func=cmd_draft_issuers)
    sv = sub.add_parser("serve", help="lancer l'interface web locale")
    sv.add_argument("--host")
    sv.set_defaults(func=cmd_serve)
    sub.add_parser("verify-audit", help="vérifier l'intégrité du journal d'audit").set_defaults(func=cmd_verify_audit)
    args = p.parse_args(argv)
    args.func(args)
