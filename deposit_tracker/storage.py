"""Settings, the SQLite database and the image folder.

Everything the app keeps lives in one data folder (default: Documents/DepositSlips):
    deposits.db      - the slip records
    images/YYYY-MM/  - a copy of every slip image
The folder can be moved to OneDrive/Dropbox to share it between computers.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime
from pathlib import Path

APP_NAME = "DepositSlipTracker"


def _config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _default_data_dir() -> Path:
    return Path.home() / "Documents" / "DepositSlips"


DEFAULT_ACCOUNTS = [
    {
        "name": "Mohammedi Scheme / QH",
        "sheet": "Mohammedi-QH",
        "keywords": "QH, Qardan Hasana, Mohammadi scheme, Mohammedi scheme",
    },
    {"name": "Najwa Income", "sheet": "Najwa", "keywords": "Najwa"},
    {"name": "Silat Income", "sheet": "Silat", "keywords": "Silat, Silatul Imam"},
]


@dataclass
class Settings:
    data_dir: str = str(_default_data_dir())
    api_key: str = ""
    model: str = "claude-opus-5-5"
    organization: str = "Taheri Qarda"
    branch: str = "TD 2831 Strandherd & Greenbank, Nepean"
    accounts: list[dict] = field(default_factory=lambda: [dict(a) for a in DEFAULT_ACCOUNTS])

    @classmethod
    def load(cls) -> "Settings":
        path = _config_dir() / "settings.json"
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        path = _config_dir() / "settings.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def effective_api_key(self) -> str:
        return self.api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")

    def account_names(self) -> list[str]:
        return [a["name"] for a in self.accounts]


# Column order matches the Excel sheets this app replaces.
@dataclass
class Slip:
    id: int | None = None
    account: str = ""
    deposit_date: str = ""  # ISO yyyy-mm-dd
    deposit_time: str = ""  # HH:MM
    ref_no: str = ""
    occasion: str = ""
    note: str = ""
    contributors: int | None = None
    cad_cash: float | None = None
    usd_cash: float | None = None
    usd_converted: float | None = None
    fx_rate: float | None = None
    denominations: str = ""
    branch: str = ""
    remarks: str = ""
    image_path: str = ""  # relative to the data folder
    extracted_json: str = ""
    created_at: str = ""

    @property
    def total(self) -> float:
        return (self.cad_cash or 0) + (self.usd_converted or 0)

    def fill_usd_conversion(self) -> None:
        """Work out the converted USD amount when the slip shows only cash and rate."""
        if self.usd_cash and self.fx_rate and not self.usd_converted:
            self.usd_converted = round(self.usd_cash * self.fx_rate, 2)


SLIP_COLUMNS = [f.name for f in fields(Slip) if f.name != "id"]


class Database:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.images_dir = self.data_dir / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.data_dir / "deposits.db")
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS slips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account TEXT NOT NULL DEFAULT '',
                deposit_date TEXT NOT NULL DEFAULT '',
                deposit_time TEXT NOT NULL DEFAULT '',
                ref_no TEXT NOT NULL DEFAULT '',
                occasion TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                contributors INTEGER,
                cad_cash REAL,
                usd_cash REAL,
                usd_converted REAL,
                fx_rate REAL,
                denominations TEXT NOT NULL DEFAULT '',
                branch TEXT NOT NULL DEFAULT '',
                remarks TEXT NOT NULL DEFAULT '',
                image_path TEXT NOT NULL DEFAULT '',
                extracted_json TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- slips -------------------------------------------------------------

    def all_slips(self) -> list[Slip]:
        rows = self.conn.execute(
            "SELECT * FROM slips ORDER BY deposit_date, deposit_time, id"
        ).fetchall()
        return [Slip(**dict(r)) for r in rows]

    def get(self, slip_id: int) -> Slip | None:
        row = self.conn.execute("SELECT * FROM slips WHERE id = ?", (slip_id,)).fetchone()
        return Slip(**dict(row)) if row else None

    def save(self, slip: Slip) -> Slip:
        slip.fill_usd_conversion()
        if not slip.created_at:
            slip.created_at = datetime.now().isoformat(timespec="seconds")
        values = [getattr(slip, c) for c in SLIP_COLUMNS]
        if slip.id is None:
            cur = self.conn.execute(
                f"INSERT INTO slips ({', '.join(SLIP_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in SLIP_COLUMNS)})",
                values,
            )
            slip.id = cur.lastrowid
        else:
            self.conn.execute(
                f"UPDATE slips SET {', '.join(c + ' = ?' for c in SLIP_COLUMNS)} WHERE id = ?",
                values + [slip.id],
            )
        self.conn.commit()
        return slip

    def delete(self, slip_id: int) -> None:
        slip = self.get(slip_id)
        self.conn.execute("DELETE FROM slips WHERE id = ?", (slip_id,))
        self.conn.commit()
        if slip and slip.image_path:
            # Keep the image in a trash folder rather than destroying evidence of a deposit.
            src = self.data_dir / slip.image_path
            if src.exists():
                trash = self.data_dir / "deleted"
                trash.mkdir(exist_ok=True)
                shutil.move(str(src), str(trash / src.name))

    def find_by_ref(self, ref_no: str, exclude_id: int | None = None) -> Slip | None:
        ref = normalize_ref(ref_no)
        if not ref:
            return None
        for slip in self.all_slips():
            if slip.id != exclude_id and normalize_ref(slip.ref_no) == ref:
                return slip
        return None

    # --- images ------------------------------------------------------------

    def store_image(self, source: str | Path) -> str:
        """Copy an image into the data folder; return its path relative to the folder."""
        source = Path(source)
        month_dir = self.images_dir / date.today().strftime("%Y-%m")
        month_dir.mkdir(parents=True, exist_ok=True)
        dest = month_dir / f"{date.today():%Y%m%d}_{uuid.uuid4().hex[:8]}{source.suffix.lower()}"
        shutil.copy2(source, dest)
        return dest.relative_to(self.data_dir).as_posix()

    def image_abspath(self, slip: Slip) -> Path | None:
        if not slip.image_path:
            return None
        path = self.data_dir / slip.image_path
        return path if path.exists() else None


def normalize_ref(ref: str) -> str:
    return "".join(ch for ch in (ref or "").upper() if ch.isalnum())
