"""Lecture brute des fichiers CSV / XLSX / XLS. Toutes les cellules sont lues en texte :
le typage est réalisé explicitement lors de la normalisation."""
from __future__ import annotations

import csv
import datetime as dt
import io
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_EXTENSIONS = {".csv", ".txt", ".xlsx", ".xlsm", ".xls"}
HEADER_SCAN_ROWS = 30


class ReadError(Exception):
    """Fichier illisible."""


@dataclass
class RawTable:
    path: Path
    headers: list[str]
    rows: list[tuple[int, list[str]]]          # (numéro de ligne dans le fichier, 1-based ; cellules)
    header_line: int                           # numéro de ligne (1-based) de l'en-tête
    encoding: str | None = None
    delimiter: str | None = None
    sheet: str | None = None
    notes: list[str] = field(default_factory=list)


def normalize_header(h: str) -> str:
    s = unicodedata.normalize("NFKD", str(h or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.casefold() if c.isalnum())


def _decode(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ReadError("Encodage non reconnu")  # pragma: no cover - latin-1 décode tout


def _sniff_delimiter(first_line: str) -> str:
    candidates = [";", ",", "\t", "|"]
    counts = {c: first_line.count(c) for c in candidates}
    best = max(candidates, key=lambda c: counts[c])
    return best if counts[best] > 0 else ","


def _detect_header(rows: list[list[str]], expected: set[str] | None) -> int:
    """Index (0-based) de la ligne d'en-tête parmi les premières lignes."""
    if not expected:
        for i, r in enumerate(rows[:HEADER_SCAN_ROWS]):
            if any(c.strip() for c in r):
                return i
        return 0
    best_i, best_score = 0, -1
    for i, r in enumerate(rows[:HEADER_SCAN_ROWS]):
        score = sum(1 for c in r if normalize_header(c) in expected)
        if score > best_score:
            best_i, best_score = i, score
    return best_i


def read_csv(path: Path, expected_headers: set[str] | None = None) -> RawTable:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ReadError(f"Lecture impossible : {exc}") from exc
    if not data.strip():
        raise ReadError("Fichier vide")
    text, enc = _decode(data)
    first_line = text.splitlines()[0] if text else ""
    delim = _sniff_delimiter(first_line)
    try:
        all_rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    except csv.Error as exc:
        raise ReadError(f"CSV mal formé : {exc}") from exc
    hi = _detect_header(all_rows, expected_headers)
    headers = [h.strip() for h in all_rows[hi]]
    rows = []
    for i, r in enumerate(all_rows[hi + 1:], start=hi + 2):
        if not any(c.strip() for c in r):
            continue
        rows.append((i, [c for c in r]))
    return RawTable(path=path, headers=headers, rows=rows, header_line=hi + 1, encoding=enc, delimiter=delim)


def _cell_to_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if v.time() == dt.time(0) else v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float):
        s = repr(v)
        return s[:-2] if s.endswith(".0") else s
    return str(v)


def read_xlsx(path: Path, expected_headers: set[str] | None = None, sheet: str | None = None) -> RawTable:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ReadError(f"Classeur illisible : {exc}") from exc
    try:
        ws = wb[sheet] if sheet else wb.worksheets[0]
        all_rows = [[_cell_to_str(c) for c in row] for row in ws.iter_rows(values_only=True)]
        name = ws.title
    finally:
        wb.close()
    return _table_from_rows(path, all_rows, expected_headers, name)


def read_xls(path: Path, expected_headers: set[str] | None = None, sheet: str | None = None) -> RawTable:
    import xlrd
    try:
        book = xlrd.open_workbook(str(path))
    except Exception as exc:
        raise ReadError(f"Classeur .xls illisible : {exc}") from exc
    sh = book.sheet_by_name(sheet) if sheet else book.sheet_by_index(0)
    all_rows = []
    for r in range(sh.nrows):
        row = []
        for c in range(sh.ncols):
            cell = sh.cell(r, c)
            if cell.ctype == xlrd.XL_CELL_DATE:
                row.append(_cell_to_str(xlrd.xldate_as_datetime(cell.value, book.datemode)))
            else:
                row.append(_cell_to_str(cell.value if cell.ctype != xlrd.XL_CELL_EMPTY else None))
        all_rows.append(row)
    return _table_from_rows(path, all_rows, expected_headers, sh.name)


def _table_from_rows(path, all_rows, expected_headers, sheet_name) -> RawTable:
    if not all_rows:
        raise ReadError("Feuille vide")
    hi = _detect_header(all_rows, expected_headers)
    headers = [h.strip() for h in all_rows[hi]]
    rows = [(i, r) for i, r in enumerate(all_rows[hi + 1:], start=hi + 2) if any(c.strip() for c in r)]
    return RawTable(path=path, headers=headers, rows=rows, header_line=hi + 1, sheet=sheet_name)


def read_table(path: Path, expected_headers: set[str] | None = None, sheet: str | None = None) -> RawTable:
    ext = path.suffix.lower()
    if ext in (".csv", ".txt"):
        return read_csv(path, expected_headers)
    if ext in (".xlsx", ".xlsm"):
        return read_xlsx(path, expected_headers, sheet)
    if ext == ".xls":
        return read_xls(path, expected_headers, sheet)
    raise ReadError(f"Extension non prise en charge : {ext}")
