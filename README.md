# Deposit Slip Tracker (Windows)

A desktop app for keeping bank deposit slips. Each slip's photo is stored alongside a spreadsheet-style record: deposit date and time, bank ref #, handwritten note, occasion, contributors, CAD and USD cash, FX rate, total and denominations. Claude reads each photo and fills the record in, and you check it before saving.

## Getting the app

**Option A: download the built .exe.** Every push that touches `deposit-slip-tracker/` runs the *Deposit Slip Tracker (Windows build)* GitHub Action. Open the run under the repo's **Actions** tab and download the `DepositSlipTracker-windows` artifact. It contains a single `DepositSlipTracker.exe` that needs no install. Windows SmartScreen may warn the first time because the exe is unsigned. Click *More info → Run anyway*.

**Option B: build it yourself.** Install Python 3.11+ from python.org, then double-click `build_windows.bat`. The exe appears in `dist\`.

**Option C: run from source.** Run `pip install -r requirements.txt`, then `python run.py`.

## First run

1. Open **⚙ Settings**.
   - **Anthropic API key**: create one at console.anthropic.com. Without a key the app still works, but you type each slip in by hand.
   - **Data folder**: defaults to `Documents\DepositSlips`. If several people should share one set of records, point it at a OneDrive or Dropbox folder. Only one person should edit at a time.
   - **Accounts**: preset to *Mohammedi Scheme / QH*, *Najwa Income* and *Silat Income*. The keywords (for example "QH, Qardan Hasana") help Claude decide which account a slip belongs to.
2. Optional: use **⬆ Import from Excel…** to load the existing `Deposit_Slips_Jun-Oct_2026.xlsx`. Imported rows have no photo yet. Open a row with **Edit** and click **Attach / replace image…** to add one.

## Daily use

- Click **➕ Add slip images…**, or drag photos onto the window. You can select several at once. Claude reads all of them in the background, then shows each one next to its filled-in form. Compare every field with the photo, correct anything wrong, and click **Save**, or **Skip** to leave a slip out.
- Anything Claude was unsure about goes in **Remarks / to check**, shown in red in the table. Examples: an illegible amount, denominations that don't add up, foreign currency not included, or one slip covering two collections.
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

`deposit_tracker/extractor.py` sends the photo (resized to 2400 px, with phone rotation corrected; HEIC is supported) to the Claude API. It uses a JSON schema so the reply always has the same fields. The prompt describes the TD slip layout and your account keywords. The model is set in Settings (default `claude-opus-5-5`). Each slip costs a few cents of API usage.
