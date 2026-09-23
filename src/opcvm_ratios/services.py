"""Assemblage des couches (utilisé par la ligne de commande et l'interface web)."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from . import jsonutil
from .engine.calculators import CALCULATORS
from .engine.runner import Engine
from .funds import load_funds
from .ingestion.profiles import load_profiles
from .ingestion.scanner import Ingestor, load_snapshot_content
from .referentials import load_referentials
from .rules.loader import load_referentiel, register_referentiel
from .settings import Settings, load_settings
from .storage.db import Database


def setup_logging(settings: Settings) -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("opcvm_ratios")
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    h = RotatingFileHandler(settings.logs_dir / "opcvm_ratios.log", maxBytes=5_000_000, backupCount=10,
                            encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(h)
    root.setLevel(logging.INFO)


class Services:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.settings.ensure_dirs()
        setup_logging(self.settings)
        self.db = Database(self.settings.db_path)
        self.reload()

    def reload(self) -> None:
        """Recharge paramétrage, fiches fonds, référentiels et règles (sans redémarrage)."""
        self.profiles = load_profiles(self.settings.mappings_dir)
        self.funds = load_funds(self.settings.funds_dir)
        self.refs = load_referentials(self.settings.issuers_file, self.settings.markets_file)
        self.referentiel = load_referentiel(self.settings.referentiel_dir, set(CALCULATORS))
        register_referentiel(self.db, self.referentiel)

    @property
    def ingestor(self) -> Ingestor:
        return Ingestor(self.settings, self.db, self.profiles, self.funds)

    @property
    def engine(self) -> Engine:
        return Engine(self.db, self.referentiel, self.funds, self.refs)

    def scan(self):
        return self.ingestor.scan()

    def run(self, fund_ids=None, snapshot_ids=None, triggered_by=None) -> int:
        self.reload()
        return self.engine.run(snapshot_ids=snapshot_ids, fund_ids=fund_ids, triggered_by=triggered_by)

    # ------------------------------------------------------------------ lecture
    def latest_run_id(self) -> int | None:
        r = self.db.q1("SELECT id FROM calc_run WHERE status = 'termine' ORDER BY id DESC LIMIT 1")
        return int(r["id"]) if r else None

    def results(self, run_id: int, fund_id: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM ratio_result WHERE run_id = ?", [run_id]
        if fund_id:
            sql += " AND fund_id = ?"
            params.append(fund_id)
        out = []
        for row in self.db.q(sql + " ORDER BY fund_id, category, rule_id", params):
            res = jsonutil.loads(row["result_json"])
            res["_id"] = row["id"]
            res["_run_id"] = row["run_id"]
            res["_calc_input_id"] = row["calc_input_id"]
            out.append(res)
        return out

    def result(self, result_id: int) -> dict | None:
        row = self.db.q1("SELECT * FROM ratio_result WHERE id = ?", (result_id,))
        if not row:
            return None
        res = jsonutil.loads(row["result_json"])
        res.update(_id=row["id"], _run_id=row["run_id"], _calc_input_id=row["calc_input_id"])
        return res

    def calc_input(self, calc_input_id: int) -> dict:
        row = self.db.q1("SELECT * FROM calc_input WHERE id = ?", (calc_input_id,))
        return jsonutil.loads(row["content_json"]) if row else {}

    def snapshot_content(self, snapshot_id: int) -> dict | None:
        row = self.db.q1("SELECT * FROM snapshot WHERE id = ?", (snapshot_id,))
        return load_snapshot_content(row) if row else None
