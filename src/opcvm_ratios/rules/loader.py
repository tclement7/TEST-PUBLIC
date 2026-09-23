"""Chargement et validation du référentiel réglementaire (fichiers YAML indépendants du code).

Immuabilité : le contenu normatif d'une version (tout sauf "validation" et "valid_to") est haché.
Une version déjà enregistrée dont le contenu change est rejetée : il faut créer une nouvelle version.
Seule la date de fin de validité ("valid_to") peut être renseignée après coup, pour fermer une version
remplacée ; chaque changement de cette date est enregistré (table rule_closure + journal d'audit).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from .. import jsonutil
from ..storage.db import Database, now_iso
from .model import OPERATORS, Limit, RuleVersion, SourceRef

REQUIRED_KEYS = ["id", "version", "name", "category", "definition", "formula", "calculator", "limits",
                 "applicability", "sources", "valid_from"]
NON_NORMATIVE_KEYS = {"validation", "valid_to"}


@dataclass
class Referentiel:
    rules: list[RuleVersion] = field(default_factory=list)
    sources: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    sha256: str = ""

    def versions(self, rule_id: str) -> list[RuleVersion]:
        return sorted((r for r in self.rules if r.rule_id == rule_id), key=lambda r: r.version)

    def rule_ids(self) -> list[str]:
        return sorted({r.rule_id for r in self.rules})


def _date(v) -> dt.date | None:
    if v in (None, ""):
        return None
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


def parse_rule(raw: dict, sources: dict[str, dict], path: Path | None = None,
               known_calculators: set[str] | None = None) -> RuleVersion:
    where = path.name if path else raw.get("id", "?")
    missing = [k for k in REQUIRED_KEYS if k not in raw]
    if missing:
        raise ValueError(f"{where} : clés obligatoires manquantes {missing}")
    if known_calculators is not None and raw["calculator"] not in known_calculators:
        raise ValueError(f"{where} : calculateur inconnu {raw['calculator']!r}")
    limits = []
    for lim in raw["limits"]:
        if lim.get("operator") not in OPERATORS:
            raise ValueError(f"{where} : opérateur de limite invalide {lim.get('operator')!r}")
        try:
            value = Decimal(str(lim["value"]))
        except (InvalidOperation, KeyError) as exc:
            raise ValueError(f"{where} : seuil invalide pour {lim.get('key')!r}") from exc
        limits.append(Limit(key=lim["key"], label=lim.get("label", lim["key"]), operator=lim["operator"],
                            value=value, unit=lim.get("unit", "ratio")))
    if not limits:
        raise ValueError(f"{where} : aucune limite définie")
    srcs = []
    for s in raw["sources"]:
        sid = s.get("source")
        if sid not in sources:
            raise ValueError(f"{where} : source inconnue {sid!r} (à déclarer dans referentiel/sources.yaml)")
        srcs.append(SourceRef(source_id=sid, article=str(s.get("article", "")),
                              confidence=s.get("confidence", sources[sid].get("confidence", "a_confirmer")),
                              title=sources[sid].get("title", ""), reference=sources[sid].get("reference", "")))
    if not srcs:
        raise ValueError(f"{where} : au moins une source est obligatoire")
    valid_from, valid_to = _date(raw["valid_from"]), _date(raw.get("valid_to"))
    if valid_to and valid_to <= valid_from:
        raise ValueError(f"{where} : valid_to doit être postérieure à valid_from")
    normative = {k: v for k, v in raw.items() if k not in NON_NORMATIVE_KEYS}
    validation = raw.get("validation") or None
    if validation and not (validation.get("by") and validation.get("on")):
        raise ValueError(f"{where} : le bloc validation doit comporter 'by' et 'on'")
    return RuleVersion(
        rule_id=str(raw["id"]), version=int(raw["version"]), name=raw["name"], category=raw["category"],
        framework=raw.get("framework"), jurisdiction=raw.get("jurisdiction"),
        definition=" ".join(str(raw["definition"]).split()), formula=" ".join(str(raw["formula"]).split()),
        calculator=raw["calculator"], params=dict(raw.get("params") or {}), limits=limits,
        applicability=dict(raw["applicability"] or {}), exceptions=list(raw.get("exceptions") or []),
        required_data=list(raw.get("required_data") or []), sources=srcs, valid_from=valid_from,
        valid_to=valid_to, validity_note=raw.get("validity_note", ""), overrides=list(raw.get("overrides") or []),
        content_sha=jsonutil.sha256_obj(normative), content=normative, path=path, validation=validation)


def load_referentiel(directory: Path, known_calculators: set[str] | None = None) -> Referentiel:
    ref = Referentiel()
    src_file = directory / "sources.yaml"
    if src_file.exists():
        for s in (yaml.safe_load(src_file.read_text(encoding="utf-8")) or {}).get("sources", []):
            ref.sources[s["id"]] = s
    else:
        ref.errors.append("referentiel/sources.yaml introuvable")
    seen = {}
    for path in sorted((directory / "rules").rglob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            rule = parse_rule(raw, ref.sources, path, known_calculators)
        except Exception as exc:  # une règle invalide n'empêche pas le chargement des autres
            ref.errors.append(f"{path.relative_to(directory)} : {exc}")
            continue
        key = (rule.rule_id, rule.version)
        if key in seen:
            ref.errors.append(f"{path.name} : version {rule.version} de {rule.rule_id} déjà définie dans {seen[key]}")
            continue
        seen[key] = path.name
        ref.rules.append(rule)
    ref.rules.sort(key=lambda r: (r.rule_id, r.version))
    ref.sha256 = jsonutil.sha256_obj([[r.rule_id, r.version, r.content_sha] for r in ref.rules])
    return ref


def register_referentiel(db: Database, ref: Referentiel) -> None:
    """Enregistre les versions de règles et applique l'immuabilité. Fusionne les validations en base."""
    kept = []
    for r in ref.rules:
        row = db.q1("SELECT content_sha FROM rule_version WHERE rule_id = ? AND version = ?", (r.rule_id, r.version))
        if row is None:
            db.insert("rule_version", rule_id=r.rule_id, version=r.version, content_sha=r.content_sha,
                      content_json=jsonutil.dumps(r.content), file_path=str(r.path) if r.path else None,
                      first_seen=now_iso())
            db.audit("regle_enregistree", "rule_version", f"{r.rule_id} v{r.version}", {"sha256": r.content_sha})
            db.insert("rule_closure", rule_id=r.rule_id, version=r.version,
                      valid_to=r.valid_to.isoformat() if r.valid_to else None, recorded_at=now_iso())
        elif row["content_sha"] != r.content_sha:
            ref.errors.append(
                f"{r.rule_id} v{r.version} : contenu modifié sans changement de version (empreinte enregistrée "
                f"{row['content_sha'][:12]}…, fichier {r.content_sha[:12]}…). Créez une version {r.version + 1}.")
            continue
        if row is not None and row["content_sha"] == r.content_sha:
            last = db.q1("SELECT valid_to FROM rule_closure WHERE rule_id = ? AND version = ? ORDER BY id DESC LIMIT 1",
                         (r.rule_id, r.version))
            current = r.valid_to.isoformat() if r.valid_to else None
            if last is None or last["valid_to"] != current:
                db.insert("rule_closure", rule_id=r.rule_id, version=r.version, valid_to=current, recorded_at=now_iso())
                db.audit("regle_fin_validite", "rule_version", f"{r.rule_id} v{r.version}",
                         {"valid_to": current, "precedente": last["valid_to"] if last else None})
        if not r.validation:
            v = db.q1("SELECT * FROM rule_validation WHERE rule_id = ? AND version = ? AND content_sha = ? "
                      "ORDER BY id DESC LIMIT 1", (r.rule_id, r.version, r.content_sha))
            if v:
                r.validation = {"by": v["validated_by"], "on": v["validated_at"], "comment": v["comment"] or "",
                                "origin": "application"}
        else:
            r.validation = {**r.validation, "origin": "fichier"}
        kept.append(r)
    ref.rules = kept


def validate_rule(db: Database, rule: RuleVersion, validated_by: str, comment: str = "") -> None:
    db.insert("rule_validation", rule_id=rule.rule_id, version=rule.version, content_sha=rule.content_sha,
              validated_by=validated_by, validated_at=now_iso(), comment=comment)
    db.audit("regle_validee", "rule_version", f"{rule.rule_id} v{rule.version}",
             {"sha256": rule.content_sha, "par": validated_by, "commentaire": comment}, user=validated_by)
