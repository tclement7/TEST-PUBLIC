"""Profils de mapping : correspondance colonnes sources -> champs canoniques, versionnés."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .. import jsonutil
from .readers import normalize_header

FIELD_TYPES = {"str", "decimal", "date"}


class ProfileError(Exception):
    pass


@dataclass
class ColumnSpec:
    name: str
    sources: list[str]
    type: str = "str"
    required: bool = False
    null_values: list[str] = field(default_factory=lambda: [""])


@dataclass
class ClassificationRule:
    when: dict
    instrument_type: str
    label: str
    balance_sheet: bool = True

    def matches(self, rec: dict) -> bool:
        for k, v in self.when.items():
            if k.endswith("_regex"):
                val = rec.get(k[: -len("_regex")])
                if val is None or not re.search(v, str(val)):
                    return False
            elif str(rec.get(k)) != str(v):
                return False
        return True


@dataclass
class FileTypeSpec:
    name: str
    filename_pattern: re.Pattern
    filename_date_format: str
    date_field: str | None
    fund_field: str | None
    columns: dict[str, ColumnSpec]
    date_consistency: bool = True
    sheet: str | None = None
    classification: list[ClassificationRule] = field(default_factory=list)

    def expected_headers(self) -> set[str]:
        return {normalize_header(s) for c in self.columns.values() for s in c.sources}


@dataclass
class MappingProfile:
    id: str
    version: int
    description: str
    sha256: str
    required_file_types: list[str]
    decimal_sep: str
    thousands_sep: str
    date_formats: list[str]
    file_types: dict[str, FileTypeSpec]
    path: Path | None = None

    def match_filename(self, file_name: str):
        for ft in self.file_types.values():
            m = ft.filename_pattern.match(file_name)
            if m:
                return ft, m
        return None, None


def load_profile(path: Path) -> MappingProfile:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        defaults = raw.get("defaults") or {}
        number = defaults.get("number") or {}
        fts = {}
        for name, spec in (raw.get("file_types") or {}).items():
            cols = {}
            for cname, cspec in (spec.get("columns") or {}).items():
                sources = cspec.get("source")
                sources = [sources] if isinstance(sources, str) else list(sources)
                ctype = cspec.get("type", "str")
                if ctype not in FIELD_TYPES:
                    raise ProfileError(f"{path.name}: type inconnu {ctype!r} pour {name}.{cname}")
                cols[cname] = ColumnSpec(name=cname, sources=sources, type=ctype,
                                         required=bool(cspec.get("required", False)),
                                         null_values=[str(x) for x in cspec.get("null_values", [""])])
            classification = [
                ClassificationRule(when=r["when"], instrument_type=r["instrument_type"], label=r.get("label", ""),
                                   balance_sheet=bool(r.get("balance_sheet", True)))
                for r in spec.get("classification") or []
            ]
            fts[name] = FileTypeSpec(
                name=name, filename_pattern=re.compile(spec["filename_pattern"], re.IGNORECASE),
                filename_date_format=spec.get("filename_date_format", "%Y_%m_%d"),
                date_field=spec.get("date_field"), fund_field=spec.get("fund_field"),
                columns=cols, date_consistency=bool(spec.get("date_consistency", True)),
                sheet=spec.get("sheet"), classification=classification)
        return MappingProfile(
            id=raw["id"], version=int(raw["version"]), description=raw.get("description", ""),
            sha256=jsonutil.sha256_obj(raw), required_file_types=list(raw.get("required_file_types") or []),
            decimal_sep=number.get("decimal", "."), thousands_sep=number.get("thousands", ""),
            date_formats=list(defaults.get("date_formats") or ["%d/%m/%Y", "%Y-%m-%d"]),
            file_types=fts, path=path)
    except KeyError as exc:
        raise ProfileError(f"{path.name}: clé obligatoire manquante {exc}") from exc


def load_profiles(directory: Path) -> list[MappingProfile]:
    profiles = [load_profile(p) for p in sorted(directory.glob("*.yaml"))]
    ids = [p.id for p in profiles]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ProfileError(f"Profils en double : {sorted(dup)}")
    return profiles
