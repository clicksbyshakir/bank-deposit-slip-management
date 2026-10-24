"""Offline slip reading with RapidOCR (no API key, nothing leaves the computer).

OCR finds the printed text; the parser below then picks out the bank fields
(date, time, ref #, CAD/USD cash, FX rate, denominations). Handwriting is not
read reliably, so the note, occasion and contributors are left for the user.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .storage import Settings, Slip

MAX_SIDE = 2000
BILLS = {1, 2, 5, 10, 20, 50, 100}
MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}

_engine = None
_lock = threading.Lock()


def _ocr(image_path: Path) -> list[tuple[list, str, float]]:
    global _engine
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        # RapidOCR works on OpenCV-style BGR arrays.
        arr = np.ascontiguousarray(np.array(img)[:, :, ::-1])
    with _lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
        result, _ = _engine(arr)
    return result or []


@dataclass
class _Box:
    text: str
    x: float
    cy: float
    h: float


def _group_lines(result) -> list[str]:
    """Merge OCR boxes that sit on the same row into one line, left to right."""
    boxes = []
    for pts, text, _score in result:
        ys = [p[1] for p in pts]
        xs = [p[0] for p in pts]
        boxes.append(_Box(text.strip(), min(xs), (min(ys) + max(ys)) / 2, max(ys) - min(ys)))
    boxes.sort(key=lambda b: b.cy)
    rows: list[list[_Box]] = []
    for b in boxes:
        if rows and abs(rows[-1][-1].cy - b.cy) < 0.5 * max(b.h, rows[-1][-1].h):
            rows[-1].append(b)
        else:
            rows.append([b])
    return ["  ".join(b.text for b in sorted(r, key=lambda b: b.x)) for r in rows]


# --- field parsing -------------------------------------------------------------------

MONEY = re.compile(r"(?<![\d.])(\d{1,3}(?:[,\s]\d{3})*|\d+)[.,](\d{2})(?![\d])")
REF = re.compile(r"(\d{6,10})\s*/\s*(\d{1,3})\s*[-–—~]?\s*([A-Z]{3,5})\b")
TIME = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)(?::[0-5]\d)?\s*([AP])?\.?M?\b", re.I)
FX = re.compile(r"(?<![\d.])([0-2]\.\d{3,6})(?![\d])")
DENOM = re.compile(
    r"(?<![\d.])(\d{1,4})\s*[xX×*@]\s*(US\s*\$|U\.?S\.?\s*\$?|\$)?\s*(\d{1,3})(?:[.,]00)?(?![\d])")
DENOM_REV = re.compile(
    r"(US\s*\$|\$)\s*(\d{1,3})(?:[.,]00)?\s*[xX×*]\s*(\d{1,4})(?![\d])")
CONTRIB = re.compile(r"(\d{1,3})\s*(?:contrib|people|ppl|persons|members)", re.I)


def _fix_letters(text: str) -> str:
    """OCR often reads O as 0 inside words (T0TAL, 0ct); undo that for keyword matching."""
    return re.sub(r"(?<=[A-Z])0(?=[A-Z])|\b0(?=[A-Z]{2})|(?<=[A-Z]{2})0\b", "O", text.upper())


def _money(line: str) -> list[float]:
    return [float(a.replace(",", "").replace(" ", "") + "." + b) for a, b in MONEY.findall(line)]


def _is_usd(line: str) -> bool:
    return bool(re.search(r"\bUSD\b|US\s*\$|U\.S\.|\bUS\s+CASH|\bUS\s+DOLLAR", line, re.I))


def _parse_date(text: str) -> tuple[str, str]:
    """Return (iso date, remark)."""
    up = _fix_letters(text)
    m = re.search(r"(20\d\d)[/\-. ](\d{1,2})[/\-. ](\d{1,2})(?!\d)", up)
    if m:
        y, mo, d = map(int, m.groups())
        return _iso(y, mo, d), ""
    month_alt = "|".join(MONTHS)
    m = re.search(rf"\b({month_alt})[A-Z]*\.?[\s\-/]*(\d{{1,2}}),?[\s\-/]*(20\d\d|\d\d)\b", up)
    if m:
        return _iso(_year(m.group(3)), MONTHS[m.group(1)], int(m.group(2))), ""
    m = re.search(rf"\b(\d{{1,2}})[\s\-/]*({month_alt})[A-Z]*\.?,?[\s\-/]*(20\d\d|\d\d)\b", up)
    if m:
        return _iso(_year(m.group(3)), MONTHS[m.group(2)], int(m.group(1))), ""
    m = re.search(r"(?<!\d)(\d{1,2})[/\-.](\d{1,2})[/\-.](20\d\d|\d\d)(?!\d)", up)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        if a > 12:
            return _iso(y, b, a), ""
        if b > 12:
            return _iso(y, a, b), ""
        return _iso(y, b, a), f"Date '{m.group(0)}' read as day/month — check it."
    return "", ""


def _year(y: str) -> int:
    return int(y) if len(y) == 4 else 2000 + int(y)


def _iso(y: int, m: int, d: int) -> str:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return ""


def _parse_time(text: str) -> str:
    for line in text.splitlines():
        if REF.search(line.upper()):
            line = REF.sub("", line.upper())
        m = TIME.search(line)
        if m:
            h, mi, ap = int(m.group(1)), m.group(2), (m.group(3) or "").upper()
            if ap == "P" and h < 12:
                h += 12
            if ap == "A" and h == 12:
                h = 0
            return f"{h:02d}:{mi}"
    return ""


def parse_lines(lines: list[str], settings: Settings) -> Slip:
    text = "\n".join(lines)
    up = text.upper()
    slip = Slip(branch=settings.branch)
    remarks: list[str] = []

    m = REF.search(up)
    if m:
        slip.ref_no = f"{m.group(1)}/{m.group(2)} - {m.group(3)}"
    slip.deposit_date, date_remark = _parse_date(text)
    if date_remark:
        remarks.append(date_remark)
    slip.deposit_time = _parse_time(text)

    cad_denoms: dict[int, int] = {}
    usd_denoms: dict[int, int] = {}
    total = None
    section_usd = False
    for line in lines:
        u = _fix_letters(line)
        line_usd = _is_usd(u)
        if re.search(r"\bCASH\b|\bCAD\b|CDN", u) and not line_usd and "CONV" not in u:
            section_usd = False
        if line_usd and not re.search(r"CONV|EQUIV|RATE|EXCH", u):
            section_usd = True

        # "$20 x 2" (value first) must be taken before "2 x $20" (count first) can misread it.
        found = [(int(c), int(v), cur) for cur, v, c in DENOM_REV.findall(u)]
        found += [(int(c), int(v), cur) for c, cur, v in DENOM.findall(DENOM_REV.sub(" ", u))]
        found = [(c, v, cur) for c, v, cur in found if v in BILLS and c > 0]
        if found:
            for count, value, cur in found:
                bucket = usd_denoms if ("US" in cur.replace(" ", "") or section_usd) else cad_denoms
                bucket[value] = bucket.get(value, 0) + count
            continue

        amounts = _money(u)
        if re.search(r"RATE|EXCH|FX\b", u):
            fx = FX.search(u)
            if fx:
                slip.fx_rate = float(fx.group(1))
            continue
        if not amounts:
            continue
        if re.search(r"CONV|EQUIV|CDN\s*VAL|CAD\s*VAL", u):
            slip.usd_converted = amounts[-1]
        elif "TOTAL" in u:
            total = max(amounts)
        elif line_usd and slip.usd_cash is None:
            slip.usd_cash = amounts[-1]
        elif "CASH" in u and slip.cad_cash is None:
            slip.cad_cash = amounts[-1]

    if slip.fx_rate is None:
        fx = FX.search(re.sub(MONEY, " ", up))
        if fx and 1.0 < float(fx.group(1)) < 2.0:
            slip.fx_rate = float(fx.group(1))

    cad_sum = sum(c * v for v, c in cad_denoms.items())
    usd_sum = sum(c * v for v, c in usd_denoms.items())
    if slip.usd_cash is None and usd_sum:
        slip.usd_cash = float(usd_sum)
    slip.fill_usd_conversion()
    if slip.fx_rate is None and slip.usd_cash and slip.usd_converted:
        slip.fx_rate = round(slip.usd_converted / slip.usd_cash, 4)
    if slip.cad_cash is None and total is not None:
        slip.cad_cash = round(total - (slip.usd_converted or 0), 2)
    if slip.cad_cash is None and cad_sum:
        slip.cad_cash = float(cad_sum)

    groups = []
    if cad_denoms:
        groups.append(", ".join(f"{c}×${v}" for v, c in sorted(cad_denoms.items())))
    if usd_denoms:
        groups.append("USD " + ", ".join(f"{c}×${v}" for v, c in sorted(usd_denoms.items())))
    slip.denominations = "; ".join(groups)

    # Cross-checks a bookkeeper would want to see.
    if cad_sum and slip.cad_cash is not None and abs(cad_sum - slip.cad_cash) > 0.005:
        remarks.append(f"Denominations add up to ${cad_sum:,.2f} but CAD cash reads ${slip.cad_cash:,.2f}.")
    if usd_sum and slip.usd_cash is not None and abs(usd_sum - slip.usd_cash) > 0.005:
        remarks.append(f"USD notes add up to US${usd_sum:,.2f} but USD cash reads US${slip.usd_cash:,.2f}.")
    if total is not None and slip.cad_cash is not None and abs(slip.total - total) > 0.01:
        remarks.append(f"Slip total reads ${total:,.2f} but CAD + converted USD is ${slip.total:,.2f}.")
    if slip.usd_cash and not slip.usd_converted:
        remarks.append("USD cash found but no exchange rate or converted amount.")

    m = CONTRIB.search(text)
    if m:
        slip.contributors = int(m.group(1))

    matches = [a["name"] for a in settings.accounts if _account_matches(a, text)]
    if len(matches) == 1:
        slip.account = matches[0]

    missing = [label for label, value in (("date", slip.deposit_date), ("ref #", slip.ref_no),
                                          ("amount", slip.cad_cash if slip.cad_cash is not None
                                           else slip.usd_cash)) if not value]
    if missing:
        remarks.append("Could not read: " + ", ".join(missing) + ".")
    slip.remarks = " ".join(remarks)
    return slip


def _account_matches(account: dict, text: str) -> bool:
    words = [account["name"]] + [k.strip() for k in account.get("keywords", "").split(",")]
    return any(w and re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", text, re.I) for w in words)


def read_slip(image_path: str | Path, settings: Settings) -> tuple[Slip, list[str]]:
    lines = _group_lines(_ocr(Path(image_path)))
    return parse_lines(lines, settings), lines
