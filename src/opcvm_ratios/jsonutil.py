"""Sérialisation JSON canonique (tri des clés, décimales en texte) pour hachage et stockage."""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path


def _default(o):
    if isinstance(o, Decimal):
        return format(o, "f")
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(f"Type non sérialisable : {type(o)!r}")


def dumps(obj, pretty: bool = False) -> str:
    return json.dumps(obj, default=_default, sort_keys=True, ensure_ascii=False,
                      indent=2 if pretty else None, separators=None if pretty else (",", ":"))


def loads(s: str):
    return json.loads(s) if s else None


def sha256_obj(obj) -> str:
    return hashlib.sha256(dumps(obj).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
