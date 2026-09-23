"""Stockage SQLite local. Les tables de résultats, snapshots, versions de règles et audit
sont en ajout seul : aucune fonction de ce module ne modifie un résultat existant."""
from __future__ import annotations

import datetime as dt
import getpass
import hashlib
import sqlite3
from pathlib import Path

from .. import jsonutil

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_file (
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL,
  original_path TEXT NOT NULL,
  file_name TEXT NOT NULL,
  size INTEGER, mtime TEXT, detected_at TEXT NOT NULL,
  archive_path TEXT,
  status TEXT NOT NULL,
  profile_id TEXT, profile_version INTEGER, file_type TEXT,
  fund_id TEXT, data_date TEXT, message TEXT,
  UNIQUE (sha256, file_name)
);
CREATE TABLE IF NOT EXISTS import_batch (
  id INTEGER PRIMARY KEY,
  source_file_id INTEGER NOT NULL REFERENCES source_file(id),
  profile_id TEXT, profile_version INTEGER, profile_sha TEXT,
  file_type TEXT, fund_id TEXT, data_date TEXT,
  created_at TEXT NOT NULL, status TEXT NOT NULL,
  n_rows INTEGER, meta_json TEXT, records_json TEXT, raw_json TEXT
);
CREATE TABLE IF NOT EXISTS anomaly (
  id INTEGER PRIMARY KEY,
  scope TEXT NOT NULL, ref_id INTEGER,
  fund_id TEXT, data_date TEXT,
  severity TEXT NOT NULL, code TEXT NOT NULL, message TEXT NOT NULL,
  field TEXT, row_ref TEXT, raw_value TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshot (
  id INTEGER PRIMARY KEY,
  fund_id TEXT NOT NULL, data_date TEXT NOT NULL,
  sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
  import_ids_json TEXT NOT NULL, content_json TEXT NOT NULL,
  status TEXT NOT NULL, superseded_by INTEGER
);
CREATE TABLE IF NOT EXISTS rule_version (
  rule_id TEXT NOT NULL, version INTEGER NOT NULL,
  content_sha TEXT NOT NULL, content_json TEXT NOT NULL,
  file_path TEXT, first_seen TEXT NOT NULL,
  PRIMARY KEY (rule_id, version)
);
CREATE TABLE IF NOT EXISTS rule_closure (
  id INTEGER PRIMARY KEY,
  rule_id TEXT NOT NULL, version INTEGER NOT NULL,
  valid_to TEXT, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rule_validation (
  id INTEGER PRIMARY KEY,
  rule_id TEXT NOT NULL, version INTEGER NOT NULL, content_sha TEXT NOT NULL,
  validated_by TEXT NOT NULL, validated_at TEXT NOT NULL, comment TEXT
);
CREATE TABLE IF NOT EXISTS calc_run (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL, finished_at TEXT,
  triggered_by TEXT, engine_version TEXT, referentiel_sha TEXT,
  status TEXT NOT NULL, scope_json TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS calc_input (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES calc_run(id),
  fund_id TEXT NOT NULL, data_date TEXT NOT NULL,
  snapshot_id INTEGER NOT NULL, snapshot_sha TEXT NOT NULL,
  referentials_json TEXT NOT NULL, input_sha TEXT NOT NULL,
  content_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ratio_result (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES calc_run(id),
  calc_input_id INTEGER NOT NULL REFERENCES calc_input(id),
  fund_id TEXT NOT NULL, data_date TEXT NOT NULL,
  rule_id TEXT NOT NULL, rule_version INTEGER, rule_sha TEXT,
  category TEXT, name TEXT,
  status TEXT NOT NULL, indicative_status TEXT,
  result_json TEXT NOT NULL, fingerprint TEXT NOT NULL,
  computed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL, user TEXT, action TEXT NOT NULL,
  object_type TEXT, object_id TEXT, details_json TEXT,
  prev_hash TEXT, hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS export (
  id INTEGER PRIMARY KEY,
  run_id INTEGER, kind TEXT NOT NULL, scope_json TEXT,
  file_path TEXT NOT NULL, sha256 TEXT NOT NULL,
  created_by TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snapshot_fund ON snapshot(fund_id, data_date);
CREATE INDEX IF NOT EXISTS ix_result_run ON ratio_result(run_id, fund_id);
CREATE INDEX IF NOT EXISTS ix_anomaly_ref ON anomaly(scope, ref_id);
"""


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - dépend de l'environnement
        return "inconnu"


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    # ------------------------------------------------------------------ utilitaires
    def q(self, sql: str, params=()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def q1(self, sql: str, params=()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def insert(self, table: str, **values) -> int:
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        cur = self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))
        self.conn.commit()
        return int(cur.lastrowid)

    def update(self, table: str, row_id: int, **values) -> None:
        # Réservé aux tables de travail (source_file, snapshot.status, calc_run.status).
        assert table in {"source_file", "snapshot", "calc_run", "import_batch"}, table
        sets = ", ".join(f"{k} = ?" for k in values)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*values.values(), row_id))
        self.conn.commit()

    # ------------------------------------------------------------------ anomalies
    def add_anomaly(self, scope: str, ref_id: int | None, severity: str, code: str, message: str,
                    fund_id: str | None = None, data_date: str | None = None, field: str | None = None,
                    row_ref: str | None = None, raw_value: str | None = None) -> int:
        return self.insert("anomaly", scope=scope, ref_id=ref_id, fund_id=fund_id, data_date=data_date,
                           severity=severity, code=code, message=message, field=field, row_ref=row_ref,
                           raw_value=raw_value, created_at=now_iso())

    def anomalies(self, scope: str | None = None, ref_id: int | None = None, fund_id: str | None = None):
        sql, params = "SELECT * FROM anomaly WHERE 1=1", []
        if scope:
            sql += " AND scope = ?"; params.append(scope)
        if ref_id is not None:
            sql += " AND ref_id = ?"; params.append(ref_id)
        if fund_id:
            sql += " AND fund_id = ?"; params.append(fund_id)
        return self.q(sql + " ORDER BY id", params)

    # ------------------------------------------------------------------ audit
    def audit(self, action: str, object_type: str | None = None, object_id=None, details=None,
              user: str | None = None) -> int:
        prev = self.q1("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1")
        prev_hash = prev["hash"] if prev else ""
        entry = {"ts": now_iso(), "user": user or current_user(), "action": action,
                 "object_type": object_type, "object_id": None if object_id is None else str(object_id),
                 "details": details or {}}
        h = hashlib.sha256((prev_hash + jsonutil.dumps(entry)).encode("utf-8")).hexdigest()
        return self.insert("audit_log", ts=entry["ts"], user=entry["user"], action=action,
                           object_type=object_type, object_id=entry["object_id"],
                           details_json=jsonutil.dumps(entry["details"]), prev_hash=prev_hash, hash=h)

    def verify_audit_chain(self) -> tuple[bool, int | None]:
        """Vérifie l'intégrité du chaînage. Retourne (ok, id de la première entrée altérée)."""
        prev_hash = ""
        for row in self.q("SELECT * FROM audit_log ORDER BY id"):
            entry = {"ts": row["ts"], "user": row["user"], "action": row["action"],
                     "object_type": row["object_type"], "object_id": row["object_id"],
                     "details": jsonutil.loads(row["details_json"]) or {}}
            h = hashlib.sha256((prev_hash + jsonutil.dumps(entry)).encode("utf-8")).hexdigest()
            if row["prev_hash"] != prev_hash or row["hash"] != h:
                return False, row["id"]
            prev_hash = h
        return True, None
