"""Détection des fichiers, identification fonds / date, archivage, import et constitution des snapshots."""
from __future__ import annotations

import datetime as dt
import logging
import shutil
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .. import jsonutil
from ..funds import FundConfig, find_by_alias, find_by_identifier
from ..normalization.checks import fx_per_fund_ccy, run_checks
from ..normalization.model import BLOCKING, INFO, WARNING, Anomaly
from ..normalization.normalizer import normalize
from ..settings import Settings
from ..storage.db import Database, now_iso
from .profiles import MappingProfile
from .readers import SUPPORTED_EXTENSIONS, ReadError, read_table

log = logging.getLogger(__name__)

# Champs décimaux des positions (pour la relecture des snapshots JSON)
POSITION_DECIMAL_FIELDS = {"price", "market_value", "market_value_local", "quantity", "accrued_interest",
                           "total_market_value", "weight_reported_pct", "contract_size"}


@dataclass
class ScanReport:
    processed: list[dict] = field(default_factory=list)
    skipped_duplicates: int = 0
    skipped_recent: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    snapshots: list[int] = field(default_factory=list)


class Ingestor:
    def __init__(self, settings: Settings, db: Database, profiles: list[MappingProfile],
                 funds: dict[str, FundConfig]):
        self.settings = settings
        self.db = db
        self.profiles = profiles
        self.funds = funds

    # ------------------------------------------------------------------ scan
    def scan(self, directory: Path | None = None) -> ScanReport:
        directory = directory or self.settings.source_dir
        report = ScanReport()
        if not directory.exists():
            report.errors.append({"file": str(directory), "message": "Dossier source introuvable"})
            self.db.add_anomaly("scan", None, BLOCKING, "DOSSIER_ABSENT", f"Dossier source introuvable : {directory}")
            return report
        touched: set[tuple[str, str, str]] = set()
        for path in sorted(p for p in directory.rglob("*") if p.is_file()):
            if path.name.startswith(("~$", ".")) or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            age = time.time() - path.stat().st_mtime
            if age < self.settings.min_file_age_seconds:
                report.skipped_recent.append(path.name)
                continue
            res = self.process_file(path)
            if res is None:
                report.skipped_duplicates += 1
                continue
            report.processed.append(res)
            if res.get("error"):
                report.errors.append(res)
            if res.get("fund_id") and res.get("data_date") and res.get("profile_id"):
                touched.add((res["fund_id"], res["data_date"], res["profile_id"]))
        for fund_id, data_date, profile_id in sorted(touched):
            sid = self.build_snapshot(fund_id, dt.date.fromisoformat(data_date), profile_id)
            if sid:
                report.snapshots.append(sid)
        self.db.audit("scan", "dossier", str(directory), {
            "fichiers_traites": len(report.processed), "doublons_ignores": report.skipped_duplicates,
            "erreurs": len(report.errors), "snapshots": report.snapshots})
        return report

    # ------------------------------------------------------------------ fichier
    def process_file(self, path: Path) -> dict | None:
        sha = jsonutil.sha256_file(path)
        # Même nom et même contenu : déjà traité, jamais retraité. Un contenu identique sous un autre nom
        # (ex. fichier de cours de change commun à plusieurs fonds) est traité normalement.
        if self.db.q1("SELECT id FROM source_file WHERE sha256 = ? AND file_name = ?", (sha, path.name)):
            return None
        st = path.stat()
        base = dict(sha256=sha, original_path=str(path), file_name=path.name, size=st.st_size,
                    mtime=dt.datetime.fromtimestamp(st.st_mtime).replace(microsecond=0).isoformat(),
                    detected_at=now_iso())
        profile, spec, match = None, None, None
        for p in self.profiles:
            spec, match = p.match_filename(path.name)
            if spec:
                profile = p
                break
        if not profile:
            fid = self.db.insert("source_file", **base, status="non_reconnu",
                                 message="Nom de fichier non reconnu par les profils de mapping")
            self.db.add_anomaly("file", fid, INFO, "FICHIER_NON_RECONNU",
                                f"Fichier non reconnu par les profils de mapping : {path.name}")
            return {"file": path.name, "status": "non_reconnu"}

        archive_path = self._archive(path, sha)
        fid = self.db.insert("source_file", **base, archive_path=str(archive_path), status="en_cours",
                             profile_id=profile.id, profile_version=profile.version, file_type=spec.name)
        result = {"file": path.name, "file_id": fid, "profile_id": profile.id, "file_type": spec.name}

        # Lecture
        try:
            table = read_table(archive_path, spec.expected_headers(), spec.sheet)
        except ReadError as exc:
            self.db.update("source_file", fid, status="illisible", message=str(exc))
            self.db.add_anomaly("file", fid, BLOCKING, "FICHIER_ILLISIBLE", f"{path.name} : {exc}")
            result.update(status="illisible", error=str(exc))
            return result

        nt = normalize(table, spec, profile)
        anomalies = list(nt.anomalies)

        # Identification du fonds
        alias = match.groupdict().get("fund_alias")
        by_alias = find_by_alias(self.funds, profile.id, alias) if alias else []
        by_content: list[FundConfig] = []
        if spec.fund_field and nt.records:
            values = sorted({r.get(spec.fund_field) for r in nt.records} - {None})
            if len(values) > 1:
                anomalies.append(Anomaly(BLOCKING, "PLUSIEURS_FONDS", f"Le fichier contient plusieurs fonds : {values}"))
            elif values:
                by_content = find_by_identifier(self.funds, spec.fund_field, values[0])
                if not by_content:
                    anomalies.append(Anomaly(WARNING if by_alias else BLOCKING, "FONDS_CODE_INCONNU",
                                             f"Code fonds {values[0]!r} absent des fiches fonds (identifiant {spec.fund_field})"))
        fund = None
        candidates = {f.fund_id for f in by_alias} | {f.fund_id for f in by_content}
        if len(candidates) == 1:
            fund = self.funds[candidates.pop()]
            if by_alias and by_content and by_alias[0].fund_id != by_content[0].fund_id:  # pragma: no cover
                fund = None
        elif len(candidates) > 1:
            anomalies.append(Anomaly(BLOCKING, "FONDS_INCOHERENT",
                                     f"Fonds différents selon le nom du fichier et son contenu : {sorted(candidates)}"))
        if fund is None and not any(a.code in ("FONDS_INCOHERENT", "PLUSIEURS_FONDS") for a in anomalies):
            anomalies.append(Anomaly(BLOCKING, "FONDS_NON_IDENTIFIE",
                                     f"Fonds non identifié (alias {alias!r}) : créer ou compléter une fiche dans config/funds"))

        # Date des données
        data_date = None
        fn_date = None
        if match.groupdict().get("date"):
            try:
                fn_date = dt.datetime.strptime(match.group("date"), spec.filename_date_format).date()
            except ValueError:
                anomalies.append(Anomaly(WARNING, "DATE_NOM_FICHIER_ILLISIBLE",
                                         f"Date illisible dans le nom de fichier : {match.group('date')}"))
        content_dates = sorted({r.get(spec.date_field) for r in nt.records if spec.date_field} - {None})
        if spec.date_consistency and len(content_dates) > 1:
            anomalies.append(Anomaly(BLOCKING, "DATES_MULTIPLES",
                                     f"Plusieurs dates de valorisation dans le fichier : {[d.isoformat() for d in content_dates]}"))
        if fn_date and content_dates and spec.date_consistency and fn_date not in content_dates:
            anomalies.append(Anomaly(BLOCKING, "DATE_INCOHERENTE",
                                     f"Date du nom de fichier ({fn_date}) ≠ date du contenu ({content_dates[0]})"))
        data_date = fn_date or (content_dates[0] if len(content_dates) == 1 else None)
        if data_date is None:
            anomalies.append(Anomaly(BLOCKING, "DATE_NON_IDENTIFIEE", "Date des données non identifiable"))

        blocking = any(a.severity == BLOCKING for a in anomalies)
        fund_id = fund.fund_id if fund else None
        ds = data_date.isoformat() if data_date else None
        iid = self.db.insert(
            "import_batch", source_file_id=fid, profile_id=profile.id, profile_version=profile.version,
            profile_sha=profile.sha256, file_type=spec.name, fund_id=fund_id, data_date=ds, created_at=now_iso(),
            status="bloquant" if blocking else "ok", n_rows=len(nt.records),
            meta_json=jsonutil.dumps({"column_map": nt.column_map, "missing_required": nt.missing_required,
                                      "missing_optional": nt.missing_optional, "encoding": table.encoding,
                                      "delimiter": table.delimiter, "sheet": table.sheet,
                                      "header_line": table.header_line, "archive_path": str(archive_path),
                                      "file_name": path.name, "sha256": sha}),
            records_json=jsonutil.dumps(nt.records), raw_json=jsonutil.dumps(nt.raw_rows))
        for a in anomalies:
            self.db.add_anomaly("import", iid, a.severity, a.code, a.message, fund_id=fund_id, data_date=ds,
                                field=a.field, row_ref=a.row_ref, raw_value=a.raw_value)
        status = "erreur" if blocking else "traite"
        self.db.update("source_file", fid, status=status, fund_id=fund_id, data_date=ds,
                       message=f"{len(nt.records)} lignes ; {sum(a.severity == BLOCKING for a in anomalies)} anomalie(s) bloquante(s)")
        self.db.audit("import_fichier", "source_file", fid, {"fichier": path.name, "sha256": sha, "fonds": fund_id,
                                                             "date": ds, "type": spec.name, "statut": status})
        result.update(status=status, fund_id=fund_id, data_date=ds, import_id=iid, n_rows=len(nt.records),
                      error=None if not blocking else "; ".join(a.message for a in anomalies if a.severity == BLOCKING))
        return result

    def _archive(self, path: Path, sha: str) -> Path:
        today = dt.date.today()
        dest_dir = self.settings.archive_dir / f"{today:%Y}" / f"{today:%m}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{sha[:16]}_{path.name}"   # nom + empreinte : unique par contenu
        if not dest.exists():
            shutil.copy2(path, dest)
        return dest

    # ------------------------------------------------------------------ snapshot
    def build_snapshot(self, fund_id: str, data_date: dt.date, profile_id: str) -> int | None:
        profile = next(p for p in self.profiles if p.id == profile_id)
        fund = self.funds[fund_id]
        ds = data_date.isoformat()
        latest: dict[str, dict] = {}
        for row in self.db.q("SELECT * FROM import_batch WHERE fund_id = ? AND data_date = ? AND profile_id = ? "
                             "ORDER BY id", (fund_id, ds, profile_id)):
            latest[row["file_type"]] = dict(row)  # le plus récent l'emporte

        anomalies: list[Anomaly] = []
        for ft in profile.required_file_types:
            if ft not in latest:
                anomalies.append(Anomaly(BLOCKING, "FICHIER_ABSENT", f"Fichier « {ft} » absent pour le {ds}"))
            elif latest[ft]["status"] != "ok":
                anomalies.append(Anomaly(BLOCKING, "FICHIER_EN_ERREUR",
                                         f"Le fichier « {ft} » le plus récent pour le {ds} comporte des anomalies bloquantes"))
        if any(a.severity == BLOCKING for a in anomalies):
            self._dataset_anomalies(fund_id, ds, anomalies)
            return None

        def records(ft):
            return jsonutil.loads(latest[ft]["records_json"]) if ft in latest and latest[ft]["status"] == "ok" else []

        def meta(ft):
            return jsonutil.loads(latest[ft]["meta_json"])

        pos_meta = meta("positions")
        positions = []
        for r in records("positions"):
            p = {k: v for k, v in r.items() if not k.startswith("_")}
            for k in POSITION_DECIMAL_FIELDS:
                if p.get(k) is not None:
                    p[k] = Decimal(p[k])
            p["line_id"] = f"POS-L{r['_row']}"
            p["source_file"] = pos_meta["file_name"]
            p["source_row"] = r["_row"]
            positions.append(p)
        positions.sort(key=lambda p: p["source_row"])

        nav_rows = records("nav")
        for r in nav_rows:
            for k in ("share_net_assets", "shares_outstanding", "nav_per_share", "total_net_assets"):
                if r.get(k) is not None:
                    r[k] = Decimal(r[k])
        fx_rows = records("fx")
        for r in fx_rows:
            for k in ("rate", "quotity"):
                if r.get(k) is not None:
                    r[k] = Decimal(r[k])
        fund_ccy = (positions[0].get("fund_currency") if positions else None) or fund.currency
        fx = fx_per_fund_ccy(fx_rows, fund_ccy) if fx_rows else {}
        if "fx" not in latest:
            anomalies.append(Anomaly(INFO, "FICHIER_CHANGE_ABSENT", "Fichier de cours de change absent : contrôle de conversion non réalisé"))

        nav, check_anomalies = run_checks(positions, nav_rows, fx, fund_ccy, self.settings.tolerances)
        anomalies.extend(check_anomalies)
        nav_meta = meta("nav")
        content = {
            "fund_id": fund_id, "fund_name": fund.name, "data_date": ds, "currency": fund_ccy,
            "nav": {"total": nav,
                    "source": {"file": nav_meta["file_name"], "rows": [r["_row"] for r in nav_rows],
                               "column": nav_meta["column_map"].get("total_net_assets")},
                    "shares": [{k: r.get(k) for k in ("share_code", "share_isin", "share_net_assets",
                                                        "shares_outstanding", "nav_per_share")} for r in nav_rows]},
            "positions": positions,
            "fx": fx,
            "files": [{"file_type": ft, "file_name": meta(ft)["file_name"], "sha256": meta(ft)["sha256"],
                       "import_id": latest[ft]["id"], "archive_path": meta(ft)["archive_path"],
                       "column_map": meta(ft)["column_map"]} for ft in sorted(latest)],
            "profile": {"id": profile.id, "version": profile.version, "sha256": profile.sha256},
            "checks": [a.__dict__ for a in anomalies],
        }
        sha = jsonutil.sha256_obj(content)
        existing = self.db.q1("SELECT id FROM snapshot WHERE fund_id = ? AND data_date = ? AND sha256 = ?",
                              (fund_id, ds, sha))
        if existing:
            return int(existing["id"])
        blocking = any(a.severity == BLOCKING for a in anomalies)
        sid = self.db.insert("snapshot", fund_id=fund_id, data_date=ds, sha256=sha, created_at=now_iso(),
                             import_ids_json=jsonutil.dumps(sorted(r["id"] for r in latest.values())),
                             content_json=jsonutil.dumps(content), status="bloquant" if blocking else "actif")
        for old in self.db.q("SELECT id FROM snapshot WHERE fund_id = ? AND data_date = ? AND id != ? AND status != 'remplace'",
                             (fund_id, ds, sid)):
            self.db.update("snapshot", old["id"], status="remplace", superseded_by=sid)
            self.db.add_anomaly("snapshot", sid, INFO, "NOUVELLE_VERSION_DONNEES",
                                f"Nouvelle version des données du {ds} : le jeu #{old['id']} est remplacé",
                                fund_id=fund_id, data_date=ds)
        for a in anomalies:
            self.db.add_anomaly("snapshot", sid, a.severity, a.code, a.message, fund_id=fund_id, data_date=ds,
                                field=a.field, row_ref=a.row_ref, raw_value=a.raw_value)
        self.db.audit("snapshot", "snapshot", sid, {"fonds": fund_id, "date": ds, "sha256": sha,
                                                    "statut": "bloquant" if blocking else "actif"})
        return sid

    def _dataset_anomalies(self, fund_id: str, ds: str, anomalies: list[Anomaly]) -> None:
        for a in anomalies:
            if not self.db.q1("SELECT id FROM anomaly WHERE scope='dataset' AND fund_id=? AND data_date=? AND code=? AND message=?",
                              (fund_id, ds, a.code, a.message)):
                self.db.add_anomaly("dataset", None, a.severity, a.code, a.message, fund_id=fund_id, data_date=ds)


def load_snapshot_content(row) -> dict:
    content = jsonutil.loads(row["content_json"])
    for p in content["positions"]:
        for k in POSITION_DECIMAL_FIELDS:
            if p.get(k) is not None:
                p[k] = Decimal(p[k])
    if content["nav"]["total"] is not None:
        content["nav"]["total"] = Decimal(content["nav"]["total"])
    content["fx"] = {k: Decimal(v) for k, v in (content.get("fx") or {}).items()}
    return content
