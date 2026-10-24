# Deposit Slip Tracker (Windows)

A desktop app for keeping bank deposit slips. Each slip's photo is stored alongside a spreadsheet-style record: deposit date and time, bank ref #, handwritten note, occasion, contributors, CAD and USD cash, FX rate, total and denominations. The app reads each photo and fills in what it can, and you check it before saving.

There are two ways to read photos, chosen in Settings:

| | Offline OCR (default) | Claude (optional) |
|---|---|---|
| Cost / setup | Free, nothing to set up, works without internet | Needs an Anthropic API key; a few cents per slip |
| Fills in | Bank-printed fields: date, time, ref #, CAD/USD cash, FX rate, denominations; plus the account when a keyword such as "QH" or "Najwa" is legible | Everything, including the handwritten note, occasion and contributors |
| Privacy | Photos never leave the computer | Photos are sent to the Claude API |

## Getting the app

**Option A: download the built .exe.** Every push that touches `deposit-slip-tracker/` runs the *Deposit Slip Tracker (Windows build)* GitHub Action. Open the run under the repo's **Actions** tab and download the `DepositSlipTracker-windows` artifact. It contains a single `DepositSlipTracker.exe` that needs no install. Windows SmartScreen may warn the first time because the exe is unsigned. Click *More info → Run anyway*.

**Option B: build it yourself.** Install Python 3.11+ from python.org, then double-click `build_windows.bat`. The exe appears in `dist\`.

**Option C: run from source.** Run `pip install -r requirements.txt`, then `python run.py`.

## First run

1. Open **⚙ Settings**.
   - **Read slips with**: *Offline OCR* is the default and needs nothing else. Choose *Claude* only if you also want handwriting read; it needs an **Anthropic API key** from console.anthropic.com.
   - **Data folder**: defaults to `Documents\DepositSlips`. If several people should share one set of records, point it at a OneDrive or Dropbox folder. Only one person should edit at a time.
   - **Accounts**: preset to *Mohammedi Scheme / QH*, *Najwa Income* and *Silat Income*. The keywords (for example "QH, Qardan Hasana") help Claude decide which account a slip belongs to.
2. Optional: use **⬆ Import from Excel…** to load the existing `Deposit_Slips_Jun-Oct_2026.xlsx`. Imported rows have no photo yet. Open a row with **Edit** and click **Attach / replace image…** to add one.

## Daily use

- Click **➕ Add slip images…**, or drag photos onto the window. You can select several at once. All of them are read in the background, then each one is shown next to its filled-in form. With offline OCR, type in the handwritten note, occasion and contributors. Compare every field with the photo, correct anything wrong, and click **Save**, or **Skip** to leave a slip out. Everything the OCR read is listed under *Text found on slip*, so you can copy from it.
- Anything to double-check goes in **Remarks / to check**, shown in red in the table. Examples: denominations that don't add up to the cash amount, a total that doesn't match CAD + converted USD, a date that could be read either way (05/10), or a field that couldn't be read.
- If a slip's ref # is already saved, the app warns you before saving it again.
- Click a row to see its photo on the right. Double-click a row to edit it.
- Filter by account or search the notes. The status bar shows totals for whatever is visible.
- **⬇ Export to Excel…** writes the same workbook layout as before: a *Summary* sheet plus one sheet per account, with live `SUM` formulas, a *Remarks* column and an *Open image* link for each slip. If a filter is active, only the visible slips are exported.

## Where things are stored

```
<Data folder>\
  deposits.db           all records (SQLite)
  images\2026-10\...     a copy of every slip photo (originals are left where they were)
  deleted\              photos of slips you deleted, kept just in case
```
Settings and the API key are kept in `%APPDATA%\DepositSlipTracker\settings.json`. Back up the data folder to back up everything.

## How the reading works

- **Offline OCR** (`deposit_tracker/ocr_reader.py`): [RapidOCR](https://github.com/RapidAI/RapidOCR) finds the text on the photo, with phone rotation corrected and HEIC supported. Pattern rules then pick out the fields: ref numbers like `00738562/5 - ZTAF`, dates such as `2026/06/19`, `Oct 7, 2026` or `12-SEP-26`, and times. They also find amounts on lines labelled CASH, USD, RATE/EXCH, CONVERTED/EQUIV and TOTAL, and note counts like `23 x $5.00` or `$5 x 23`. The rules were tuned on mock-up slips. If a field is regularly missed on your real slips, adjust the patterns at the top of `ocr_reader.py`. The text listed under *Text found on slip* shows what the OCR saw.
- **Claude** (`deposit_tracker/extractor.py`): sends the photo, resized to 2400 px, to the Claude API with a JSON schema, so the reply always has the same fields. The model is set in Settings (default `claude-opus-5-5`).
