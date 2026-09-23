"""Mapping des colonnes et typage strict des valeurs. Aucune valeur n'est inventée :
une cellule illisible devient None et génère une anomalie."""
from __future__ import annotations

import datetime as dt

from ..ingestion.profiles import FileTypeSpec, MappingProfile
from ..ingestion.readers import RawTable, normalize_header
from ..numbers import NumberParseError, parse_decimal
from .model import BLOCKING, WARNING, Anomaly, NormalizedTable


def parse_date(raw: str, formats: list[str]) -> dt.date:
    s = raw.strip()
    for fmt in formats:
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError as exc:
        raise ValueError(f"Date illisible : {raw!r}") from exc


def map_columns(table: RawTable, spec: FileTypeSpec):
    index = {}
    for i, h in enumerate(table.headers):
        index.setdefault(normalize_header(h), i)
    colmap, missing_req, missing_opt = {}, [], []
    for cname, cspec in spec.columns.items():
        found = None
        for src in cspec.sources:
            j = index.get(normalize_header(src))
            if j is not None:
                found = j
                break
        if found is None:
            (missing_req if cspec.required else missing_opt).append(cname)
        else:
            colmap[cname] = found
    return colmap, missing_req, missing_opt


def normalize(table: RawTable, spec: FileTypeSpec, profile: MappingProfile) -> NormalizedTable:
    colmap, missing_req, missing_opt = map_columns(table, spec)
    anomalies: list[Anomaly] = []
    for c in missing_req:
        anomalies.append(Anomaly(BLOCKING, "COLONNE_ABSENTE",
                                 f"Colonne obligatoire absente : {c} (sources attendues : {', '.join(spec.columns[c].sources)})",
                                 field=c))
    for c in missing_opt:
        anomalies.append(Anomaly(WARNING, "COLONNE_OPTIONNELLE_ABSENTE",
                                 f"Colonne facultative absente : {c} (sources attendues : {', '.join(spec.columns[c].sources)})",
                                 field=c))
    records, raw_rows = [], []
    if missing_req:
        return NormalizedTable(spec.name, records, raw_rows, {k: table.headers[v] for k, v in colmap.items()},
                               missing_req, missing_opt, anomalies)

    for line_no, cells in table.rows:
        rec: dict = {"_row": line_no}
        raw_rows.append({"_row": line_no, **{table.headers[i]: (cells[i] if i < len(cells) else "")
                                              for i in range(len(table.headers))}})
        for cname, idx in colmap.items():
            cspec = spec.columns[cname]
            raw = cells[idx].strip() if idx < len(cells) else ""
            if raw in cspec.null_values:
                rec[cname] = None
                if cspec.required and cspec.type != "str":
                    anomalies.append(Anomaly(WARNING, "DONNEE_MANQUANTE", f"Valeur manquante pour {cname}",
                                             field=cname, row_ref=f"ligne {line_no}", raw_value=raw))
                continue
            try:
                if cspec.type == "decimal":
                    rec[cname] = parse_decimal(raw, profile.decimal_sep, profile.thousands_sep)
                elif cspec.type == "date":
                    rec[cname] = parse_date(raw, profile.date_formats)
                else:
                    rec[cname] = raw
            except (NumberParseError, ValueError) as exc:
                rec[cname] = None
                anomalies.append(Anomaly(WARNING, "DONNEE_ILLISIBLE", str(exc), field=cname,
                                         row_ref=f"ligne {line_no}", raw_value=raw))
        if spec.classification:
            _classify(rec, spec, anomalies)
        records.append(rec)
    if not records:
        anomalies.append(Anomaly(BLOCKING, "FICHIER_SANS_DONNEES", "Aucune ligne de données"))
    return NormalizedTable(spec.name, records, raw_rows, {k: table.headers[v] for k, v in colmap.items()},
                           missing_req, missing_opt, anomalies)


def _classify(rec: dict, spec: FileTypeSpec, anomalies: list[Anomaly]) -> None:
    for rule in spec.classification:
        if rule.matches(rec):
            rec["instrument_type"] = rule.instrument_type
            rec["instrument_label"] = rule.label
            rec["balance_sheet"] = rule.balance_sheet
            return
    rec["instrument_type"] = "unknown"
    rec["instrument_label"] = "Type non reconnu"
    rec["balance_sheet"] = True
    anomalies.append(Anomaly(WARNING, "INSTRUMENT_NON_CLASSE",
                             "Ligne non classée par le profil de mapping : exclue de tous les ratios",
                             row_ref=f"ligne {rec['_row']}",
                             raw_value=f"{rec.get('src_category')}/{rec.get('account_code')}/{rec.get('security_type_code')}/{rec.get('stock_type')}"))
