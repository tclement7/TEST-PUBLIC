from __future__ import annotations

from dataclasses import dataclass, field

BLOCKING = "bloquante"
WARNING = "avertissement"
INFO = "info"


@dataclass
class Anomaly:
    severity: str
    code: str
    message: str
    field: str | None = None
    row_ref: str | None = None
    raw_value: str | None = None


@dataclass
class NormalizedTable:
    file_type: str
    records: list[dict]
    raw_rows: list[dict]
    column_map: dict[str, str]
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    anomalies: list[Anomaly] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(a.severity == BLOCKING for a in self.anomalies)
