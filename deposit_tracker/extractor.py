"""Read a deposit slip photo with Claude and return the fields for one record."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import anthropic
from PIL import Image, ImageOps

try:  # iPhone photos (.heic) - optional
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

from .storage import Settings, Slip

MAX_SIDE = 2400  # px; keeps handwriting legible while staying well under the API image limit

NUM = {"type": ["number", "null"]}
STR = {"type": "string"}

SCHEMA = {
    "type": "object",
    "properties": {
        "account": STR,
        "deposit_date": STR,
        "deposit_time": STR,
        "ref_no": STR,
        "occasion": STR,
        "note": STR,
        "contributors": {"type": ["integer", "null"]},
        "cad_cash": NUM,
        "usd_cash": NUM,
        "usd_converted": NUM,
        "fx_rate": NUM,
        "denominations": STR,
        "branch": STR,
        "remarks": STR,
    },
    "required": [
        "account", "deposit_date", "deposit_time", "ref_no", "occasion", "note",
        "contributors", "cad_cash", "usd_cash", "usd_converted", "fx_rate",
        "denominations", "branch", "remarks",
    ],
    "additionalProperties": False,
}

PROMPT = """This is a photo of a bank deposit slip or deposit receipt for {org}. \
Transcribe it into the fields of the JSON schema.

Field rules:
- account: which account the deposit belongs to. Choose exactly one of these names, \
using the handwritten note and the keywords: {accounts}. \
If nothing matches, return "Unknown".
- deposit_date: the bank's date for the deposit as YYYY-MM-DD (the printed/stamped date, \
not a date in the handwritten note).
- deposit_time: the bank's time as 24-hour HH:MM, or "" if not shown.
- ref_no: the bank reference / transaction number exactly as printed \
(for TD it looks like "00738562/5 - ZTAF").
- note: the handwritten note, transcribed as written.
- occasion: the occasion or collection date the note refers to, short \
(e.g. "Fri 17 Jul 2026", "10th Moharram 1448", "Imam Zaman Salgira").
- contributors: the number of contributors if written on the slip, otherwise null.
- cad_cash: total Canadian cash deposited, in dollars.
- usd_cash: US cash in US dollars, or null if none.
- usd_converted: the Canadian-dollar value the bank credited for the US cash, or null.
- fx_rate: the exchange rate printed for the US cash, or null.
- denominations: note counts like "23×$5, 6×$10, 4×$20; USD 1×$1". "" if not shown.
- branch: the branch name/address if printed, else "".
- remarks: anything a bookkeeper should double-check: illegible parts, totals that do not \
add up to the denominations, foreign currency not included, crossed-out amounts, or \
a slip that seems to combine several collections. "" if nothing.

Use "" or null for anything you cannot read rather than guessing."""


class ExtractionError(Exception):
    pass


def _encode_image(path: Path) -> tuple[str, str]:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)  # phone photos are often stored sideways
        img = img.convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
    return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode("ascii")


def extract_slip(image_path: str | Path, settings: Settings) -> tuple[Slip, str]:
    """Return a Slip filled from the image, plus the raw JSON Claude produced."""
    api_key = settings.effective_api_key()
    if not api_key:
        raise ExtractionError("No Anthropic API key set. Add one under Settings.")

    media_type, data = _encode_image(Path(image_path))
    accounts = "; ".join(
        f'"{a["name"]}" (keywords: {a.get("keywords", "")})' for a in settings.accounts
    )
    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.beta.messages.create(
            model=settings.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                    {"type": "text", "text": PROMPT.format(org=settings.organization, accounts=accounts)},
                ],
            }],
        )
    except anthropic.AuthenticationError as exc:
        raise ExtractionError("The Anthropic API key was rejected. Check it under Settings.") from exc
    except anthropic.RateLimitError as exc:
        raise ExtractionError("Rate limited by the Anthropic API. Wait a minute and retry.") from exc
    except anthropic.APIStatusError as exc:
        raise ExtractionError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise ExtractionError("Could not reach the Anthropic API. Check the internet connection.") from exc

    if response.stop_reason == "refusal":
        raise ExtractionError("Claude declined to read this image. Enter the details by hand.")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("The response was cut off. Try again.")

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise ExtractionError("Claude returned an unreadable answer. Try again.") from exc

    slip = Slip(**{k: v for k, v in raw.items() if k in Slip.__dataclass_fields__})
    for key in ("account", "deposit_date", "deposit_time", "ref_no", "occasion", "note",
                "denominations", "branch", "remarks"):
        setattr(slip, key, getattr(slip, key) or "")
    if slip.account not in settings.account_names():
        slip.account = ""
    if not slip.branch:
        slip.branch = settings.branch
    slip.fill_usd_conversion()
    return slip, text
