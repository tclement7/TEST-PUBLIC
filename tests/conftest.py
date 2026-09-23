import shutil
from pathlib import Path

import pytest
import yaml

from opcvm_ratios.settings import load_settings

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"


@pytest.fixture
def settings_factory(tmp_path):
    """Crée un paramétrage isolé (dossier source, base, archives dans tmp_path)."""
    def make(copy_sources=True, issuers=True, markets=True):
        src = tmp_path / "source"
        src.mkdir(exist_ok=True)
        if copy_sources:
            for f in (FIX / "fonds_test").glob("ff*.csv"):
                shutil.copy2(f, src / f.name)
        cfg = {
            "source_dir": str(src), "archive_dir": str(tmp_path / "archive"), "db_path": str(tmp_path / "app.db"),
            "exports_dir": str(tmp_path / "exports"), "logs_dir": str(tmp_path / "logs"),
            "mappings_dir": str(ROOT / "config" / "mappings"), "funds_dir": str(FIX / "funds"),
            "referentiel_dir": str(ROOT / "referentiel"),
            "issuers_file": str(FIX / "fonds_test" / "emetteurs.csv") if issuers else str(tmp_path / "absent.csv"),
            "markets_file": str(FIX / "fonds_test" / "marches.csv") if markets else str(tmp_path / "absent2.csv"),
            "min_file_age_seconds": 0,
        }
        (tmp_path / "config").mkdir(exist_ok=True)
        p = tmp_path / "config" / "settings.yaml"
        p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        return load_settings(p)
    return make


@pytest.fixture
def expected():
    return yaml.safe_load((FIX / "fonds_test" / "expected.yaml").read_text(encoding="utf-8"))
