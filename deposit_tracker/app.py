"""Windows desktop UI (PySide6)."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QDate, QObject, QRunnable, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QImageReader, QPixmap, QTransform
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
)

from . import __version__
from .excel_io import export_workbook, import_workbook
from .extractor import ExtractionError, extract_slip
from .storage import Database, Settings, Slip

IMAGE_FILTER = "Images (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.heic *.heif)"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}

COLUMNS = [
    ("Account", "account"), ("Deposit Date", "deposit_date"), ("Time", "deposit_time"),
    ("Ref #", "ref_no"), ("Occasion", "occasion"), ("Handwritten Note", "note"),
    ("Contributors", "contributors"), ("CAD Cash", "cad_cash"), ("USD Cash", "usd_cash"),
    ("USD→CAD", "usd_converted"), ("FX Rate", "fx_rate"), ("Total (CAD)", "total"),
    ("Denominations", "denominations"), ("Remarks", "remarks"),
]
MONEY_FIELDS = {"cad_cash", "usd_cash", "usd_converted", "total"}


def load_pixmap(path: Path) -> QPixmap:
    """Load an image, honouring phone EXIF rotation; HEIC goes through Pillow."""
    if path.suffix.lower() in (".heic", ".heif"):
        from PIL import Image, ImageOps
        from PIL.ImageQt import ImageQt

        with Image.open(path) as img:
            return QPixmap.fromImage(ImageQt(ImageOps.exif_transpose(img).convert("RGB")))
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    return QPixmap.fromImage(reader.read())


def fmt_money(value) -> str:
    return "" if value is None else f"{value:,.2f}"


# --- background extraction -----------------------------------------------------

class ExtractJob(QObject):
    finished = Signal(object, str)
    failed = Signal(str)

    def __init__(self, image: Path, settings: Settings):
        super().__init__()
        self.image, self.settings = image, settings
        self.result: tuple[Slip, str] | None = None
        self.error: str | None = None
        self.done = False

    def run(self) -> None:
        try:
            self.result = extract_slip(self.image, self.settings)
        except ExtractionError as exc:
            self.error = str(exc)
        except Exception as exc:  # unreadable file etc. - show it, don't crash the app
            self.error = f"Could not read the image: {exc}"
        self.done = True
        if self.result:
            self.finished.emit(*self.result)
        else:
            self.failed.emit(self.error)


class _Runner(QRunnable):
    def __init__(self, job: ExtractJob):
        super().__init__()
        self.job = job

    def run(self) -> None:
        self.job.run()


def start_job(image: Path, settings: Settings) -> ExtractJob:
    job = ExtractJob(image, settings)
    QThreadPool.globalInstance().start(_Runner(job))
    return job


# --- image viewer --------------------------------------------------------------

class ImageView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._path: Path | None = None
        self._zoom = 0.0  # 0 = fit to window
        self._rotation = 0

        self.label = QLabel("No image", alignment=Qt.AlignCenter)
        self.scroll = QScrollArea(widgetResizable=False)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.label)

        bar = QHBoxLayout()
        for text, slot in (("Fit", self.fit), ("−", lambda: self.zoom(0.8)),
                           ("+", lambda: self.zoom(1.25)), ("⟳ Rotate", self.rotate),
                           ("Open…", self.open_external)):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        bar.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.scroll)

    def set_image(self, path: Path | None) -> None:
        self._path, self._rotation, self._zoom = path, 0, 0.0
        self._pixmap = load_pixmap(path) if path and path.exists() else QPixmap()
        self._render()

    def fit(self):
        self._zoom = 0.0
        self._render()

    def zoom(self, factor: float):
        if self._zoom == 0.0:
            self._zoom = self._current_scale()
        self._zoom = max(0.05, min(self._zoom * factor, 8))
        self._render()

    def rotate(self):
        self._rotation = (self._rotation + 90) % 360
        self._render()

    def open_external(self):
        if self._path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._path)))

    def _rotated(self) -> QPixmap:
        if self._rotation:
            return self._pixmap.transformed(QTransform().rotate(self._rotation), Qt.SmoothTransformation)
        return self._pixmap

    def _current_scale(self) -> float:
        pm = self._rotated()
        if pm.isNull():
            return 1.0
        vp = self.scroll.viewport().size()
        return min(vp.width() / pm.width(), vp.height() / pm.height())

    def _render(self):
        if self._pixmap.isNull():
            self.label.setText("No image")
            self.label.setPixmap(QPixmap())
            self.label.adjustSize()
            return
        pm = self._rotated()
        scale = self._zoom or self._current_scale()
        scaled = pm.scaled(int(pm.width() * scale), int(pm.height() * scale),
                           Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.label.setPixmap(scaled)
        self.label.resize(scaled.size())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._zoom == 0.0:
            self._render()


# --- add / edit dialog ----------------------------------------------------------

class SlipDialog(QDialog):
    """Shows the slip image next to an editable form. Used for new and existing slips."""

    def __init__(self, parent, db: Database, settings: Settings, slip: Slip,
                 new_image: Path | None = None, job: ExtractJob | None = None,
                 position: str = ""):
        super().__init__(parent)
        self.db, self.settings, self.slip = db, settings, slip
        self.new_image, self.job = new_image, job
        self.setWindowTitle(("Add deposit slip" if slip.id is None else "Edit deposit slip")
                            + (f"  ({position})" if position else ""))
        self.resize(1300, 820)

        self.viewer = ImageView()
        self.viewer.set_image(new_image or db.image_abspath(slip))

        self.status = QLabel()
        self.status.setWordWrap(True)

        self.account = QComboBox()
        self.account.addItem("")
        self.account.addItems(settings.account_names())
        self.date = QDateEdit(calendarPopup=True, displayFormat="yyyy-MM-dd")
        self.time = QLineEdit(placeholderText="HH:MM")
        self.ref = QLineEdit()
        self.occasion = QLineEdit()
        self.note = QPlainTextEdit()
        self.note.setFixedHeight(60)
        self.contributors = QLineEdit()
        self.cad = QLineEdit()
        self.usd = QLineEdit()
        self.usd_conv = QLineEdit()
        self.fx = QLineEdit()
        self.total = QLabel()
        self.total.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.denoms = QLineEdit(placeholderText="e.g. 23×$5, 6×$10; USD 1×$20")
        self.branch = QLineEdit()
        self.remarks = QPlainTextEdit()
        self.remarks.setFixedHeight(60)
        for w in (self.cad, self.usd_conv):
            w.textChanged.connect(self._update_total)
        for w in (self.usd, self.fx):
            w.textChanged.connect(self._auto_convert)

        form = QFormLayout()
        form.addRow("Account", self.account)
        form.addRow("Deposit date (bank)", self.date)
        form.addRow("Time", self.time)
        form.addRow("Bank ref #", self.ref)
        form.addRow("Occasion / collection date", self.occasion)
        form.addRow("Handwritten note", self.note)
        form.addRow("Contributors", self.contributors)
        form.addRow("CAD cash ($)", self.cad)
        form.addRow("USD cash (US$)", self.usd)
        form.addRow("FX rate", self.fx)
        form.addRow("USD converted ($ CAD)", self.usd_conv)
        form.addRow("Total deposited ($ CAD)", self.total)
        form.addRow("Denominations", self.denoms)
        form.addRow("Branch", self.branch)
        form.addRow("Remarks / to check", self.remarks)

        self.read_btn = QPushButton("Read slip with Claude")
        self.read_btn.clicked.connect(self._run_extraction)
        self.read_btn.setEnabled(self.viewer._path is not None)
        attach_btn = QPushButton("Attach / replace image…")
        attach_btn.clicked.connect(self._attach_image)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.read_btn)
        btn_row.addWidget(attach_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        if position:
            buttons.button(QDialogButtonBox.Cancel).setText("Skip")

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(self.status)
        rl.addLayout(form)
        rl.addLayout(btn_row)
        rl.addStretch()
        rl.addWidget(buttons)

        split = QSplitter()
        split.addWidget(self.viewer)
        split.addWidget(right)
        split.setSizes([750, 550])
        QVBoxLayout(self).addWidget(split)

        self._load(slip)
        if job:
            self._attach_job(job)

    # form <-> slip

    def _load(self, s: Slip) -> None:
        self.account.setCurrentText(s.account)
        d = QDate.fromString(s.deposit_date, "yyyy-MM-dd")
        self.date.setDate(d if d.isValid() else QDate.currentDate())
        self.time.setText(s.deposit_time)
        self.ref.setText(s.ref_no)
        self.occasion.setText(s.occasion)
        self.note.setPlainText(s.note)
        self.contributors.setText("" if s.contributors is None else str(s.contributors))
        self.cad.setText(_num_text(s.cad_cash))
        self.usd.setText(_num_text(s.usd_cash))
        self.fx.setText(_num_text(s.fx_rate))
        self.usd_conv.setText(_num_text(s.usd_converted))
        self.denoms.setText(s.denominations)
        self.branch.setText(s.branch or self.settings.branch)
        self.remarks.setPlainText(s.remarks)
        self._update_total()

    def _collect(self) -> Slip:
        s = self.slip
        s.account = self.account.currentText()
        s.deposit_date = self.date.date().toString("yyyy-MM-dd")
        s.deposit_time = self.time.text().strip()
        s.ref_no = self.ref.text().strip()
        s.occasion = self.occasion.text().strip()
        s.note = self.note.toPlainText().strip()
        c = _parse_num(self.contributors.text())
        s.contributors = int(c) if c is not None else None
        s.cad_cash = _parse_num(self.cad.text())
        s.usd_cash = _parse_num(self.usd.text())
        s.fx_rate = _parse_num(self.fx.text())
        s.usd_converted = _parse_num(self.usd_conv.text())
        s.denominations = self.denoms.text().strip()
        s.branch = self.branch.text().strip()
        s.remarks = self.remarks.toPlainText().strip()
        return s

    def _update_total(self):
        total = (_parse_num(self.cad.text()) or 0) + (_parse_num(self.usd_conv.text()) or 0)
        self.total.setText(f"$ {total:,.2f}")

    def _auto_convert(self):
        usd, fx = _parse_num(self.usd.text()), _parse_num(self.fx.text())
        if usd and fx:
            self.usd_conv.setText(f"{usd * fx:.2f}")

    # extraction

    def _attach_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose the slip image", "", IMAGE_FILTER)
        if path:
            self.new_image = Path(path)
            self.viewer.set_image(self.new_image)
            self.read_btn.setEnabled(True)

    def _run_extraction(self):
        path = self.new_image or self.db.image_abspath(self.slip)
        if path:
            self._attach_job(start_job(path, self.settings))

    def _attach_job(self, job: ExtractJob):
        self.job = job
        self._job_handled = False
        self.read_btn.setEnabled(False)
        self.status.setText("⏳ Claude is reading the slip… you can start typing meanwhile.")
        self.status.setStyleSheet("color: #555;")
        # Bound methods of this dialog are delivered on the UI thread (queued connection).
        job.finished.connect(self._on_job_signal)
        job.failed.connect(self._on_job_signal)
        if job.done:  # finished before we connected
            self._job_done(job)

    def _on_job_signal(self, *_):
        if self.job is not None:
            self._job_done(self.job)

    def _job_done(self, job: ExtractJob):
        if job is not self.job or self._job_handled:
            return
        self._job_handled = True
        self.read_btn.setEnabled(True)
        if job.result is None:
            self.status.setText(f"⚠ {job.error}")
            self.status.setStyleSheet("color: #b00020;")
            return
        extracted, raw = job.result
        extracted.id, extracted.image_path = self.slip.id, self.slip.image_path
        extracted.created_at = self.slip.created_at
        extracted.extracted_json = raw
        self.slip = extracted
        self._load(extracted)
        msg = "✔ Filled in from the image. Check every field against the slip before saving."
        if extracted.remarks:
            msg += "\nClaude flagged something to check — see Remarks."
        self.status.setText(msg)
        self.status.setStyleSheet("color: #1b5e20;")

    def _save(self):
        s = self._collect()
        if not s.account:
            QMessageBox.warning(self, "Account missing", "Pick which account this deposit went to.")
            return
        if s.cad_cash is None and s.usd_cash is None:
            if QMessageBox.question(self, "No amount", "No amount entered. Save anyway?") != QMessageBox.Yes:
                return
        dup = self.db.find_by_ref(s.ref_no, exclude_id=s.id)
        if dup and QMessageBox.question(
            self, "Possible duplicate",
            f"A slip with ref # {dup.ref_no} is already saved ({dup.deposit_date}, "
            f"{dup.account}, ${dup.total:,.2f}).\n\nSave this one too?",
        ) != QMessageBox.Yes:
            return
        if self.new_image:
            s.image_path = self.db.store_image(self.new_image)
        self.db.save(s)
        self.accept()


def _parse_num(text: str):
    text = (text or "").replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _num_text(value) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


# --- settings -------------------------------------------------------------------

class SettingsDialog(QDialog):
    def __init__(self, parent, settings: Settings):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.resize(760, 520)

        self.data_dir = QLineEdit(settings.data_dir)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.data_dir)
        dir_row.addWidget(browse)

        self.api_key = QLineEdit(settings.api_key, echoMode=QLineEdit.Password,
                                 placeholderText="sk-ant-…  (or set ANTHROPIC_API_KEY)")
        self.model = QLineEdit(settings.model)
        self.org = QLineEdit(settings.organization)
        self.branch = QLineEdit(settings.branch)

        self.accounts = QTableWidget(0, 3)
        self.accounts.setHorizontalHeaderLabels(["Account name", "Excel sheet name", "Keywords on slips"])
        self.accounts.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for acc in settings.accounts:
            self._add_account_row(acc)
        add = QPushButton("Add account")
        add.clicked.connect(lambda: self._add_account_row({"name": "", "sheet": "", "keywords": ""}))
        remove = QPushButton("Remove selected")
        remove.clicked.connect(lambda: self.accounts.removeRow(self.accounts.currentRow()))
        acc_btns = QHBoxLayout()
        acc_btns.addWidget(add)
        acc_btns.addWidget(remove)
        acc_btns.addStretch()

        form = QFormLayout()
        form.addRow("Data folder", dir_row)
        form.addRow("Anthropic API key", self.api_key)
        form.addRow("Claude model", self.model)
        form.addRow("Organization", self.org)
        form.addRow("Default bank branch", self.branch)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Accounts (keywords help Claude assign each slip):"))
        layout.addWidget(self.accounts)
        layout.addLayout(acc_btns)
        layout.addWidget(buttons)

    def _add_account_row(self, acc: dict):
        r = self.accounts.rowCount()
        self.accounts.insertRow(r)
        for c, key in enumerate(("name", "sheet", "keywords")):
            self.accounts.setItem(r, c, QTableWidgetItem(acc.get(key, "")))

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Choose data folder", self.data_dir.text())
        if path:
            self.data_dir.setText(path)

    def _save(self):
        accounts = []
        for r in range(self.accounts.rowCount()):
            cells = [(self.accounts.item(r, c).text().strip() if self.accounts.item(r, c) else "")
                     for c in range(3)]
            if cells[0]:
                accounts.append({"name": cells[0], "sheet": cells[1] or cells[0], "keywords": cells[2]})
        s = self.settings
        s.data_dir = self.data_dir.text().strip() or s.data_dir
        s.api_key = self.api_key.text().strip()
        s.model = self.model.text().strip() or "claude-opus-5-5"
        s.organization = self.org.text().strip()
        s.branch = self.branch.text().strip()
        s.accounts = accounts
        s.save()
        self.accept()


# --- main window ------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.db = Database(self.settings.data_dir)
        self.slips: list[Slip] = []
        self.setWindowTitle(f"Deposit Slip Tracker {__version__}")
        self.resize(1500, 860)
        self.setAcceptDrops(True)

        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)
        for text, slot in (("➕ Add slip images…", self.add_images),
                           ("✎ Add without image", self.add_manual),
                           ("Edit", self.edit_selected), ("Delete", self.delete_selected),
                           (None, None),
                           ("⬇ Export to Excel…", self.export_excel),
                           ("⬆ Import from Excel…", self.import_excel),
                           (None, None),
                           ("⚙ Settings", self.open_settings)):
            if text is None:
                tb.addSeparator()
                continue
            act = QAction(text, self)
            act.triggered.connect(slot)
            tb.addAction(act)

        self.filter_account = QComboBox()
        self.filter_account.currentIndexChanged.connect(self.refresh_table)
        self.search = QLineEdit(placeholderText="Search notes, ref #, occasion…")
        self.search.textChanged.connect(self.refresh_table)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Account:"))
        filters.addWidget(self.filter_account)
        filters.addWidget(self.search, 1)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c[0] for c in COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._show_selected_image)
        self.table.doubleClicked.connect(lambda *_: self.edit_selected())
        self.table.sortByColumn(1, Qt.AscendingOrder)  # by deposit date

        self.preview = ImageView()

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addLayout(filters)
        ll.addWidget(self.table)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.preview)
        split.setSizes([1000, 500])
        self.setCentralWidget(split)

        self.totals = QLabel()
        self.statusBar().addPermanentWidget(self.totals)
        self.reload()

        if not self.settings.effective_api_key():
            self.statusBar().showMessage(
                "Tip: add an Anthropic API key in Settings so slips are read automatically.", 15000)

    # data

    def reload(self):
        current = self.filter_account.currentText()
        self.filter_account.blockSignals(True)
        self.filter_account.clear()
        self.filter_account.addItem("All accounts")
        names = list(self.settings.account_names())
        self.slips = self.db.all_slips()
        names += sorted({s.account for s in self.slips if s.account and s.account not in names})
        self.filter_account.addItems(names)
        self.filter_account.setCurrentText(current or "All accounts")
        self.filter_account.blockSignals(False)
        self.refresh_table()

    def visible_slips(self) -> list[Slip]:
        acct = self.filter_account.currentText()
        q = self.search.text().strip().lower()
        out = []
        for s in self.slips:
            if acct and acct != "All accounts" and s.account != acct:
                continue
            if q and q not in " ".join([s.account, s.ref_no, s.occasion, s.note, s.remarks,
                                        s.deposit_date, s.denominations]).lower():
                continue
            out.append(s)
        return out

    def refresh_table(self):
        rows = self.visible_slips()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, s in enumerate(rows):
            for c, (_, attr) in enumerate(COLUMNS):
                value = s.total if attr == "total" else getattr(s, attr)
                item = QTableWidgetItem()
                if attr in MONEY_FIELDS or attr in ("fx_rate", "contributors"):
                    item.setData(Qt.DisplayRole, value if value is not None else None)
                    if attr in MONEY_FIELDS and value is not None:
                        item.setData(Qt.DisplayRole, round(value, 2))
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    item.setText(value or "")
                if c == 0:
                    item.setData(Qt.UserRole, s.id)
                if attr == "remarks" and value:
                    item.setForeground(Qt.darkRed)
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        for c, (_, attr) in enumerate(COLUMNS):
            if attr in ("note", "remarks", "denominations", "occasion"):
                self.table.setColumnWidth(c, min(self.table.columnWidth(c), 260))

        cad = sum(s.cad_cash or 0 for s in rows)
        usd = sum(s.usd_converted or 0 for s in rows)
        self.totals.setText(f"{len(rows)} slips   |   CAD cash ${cad:,.2f}   |   "
                            f"USD→CAD ${usd:,.2f}   |   Total ${cad + usd:,.2f}  ")

    def selected_slip(self) -> Slip | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return self.db.get(item.data(Qt.UserRole)) if item else None

    def _show_selected_image(self):
        slip = self.selected_slip()
        self.preview.set_image(self.db.image_abspath(slip) if slip else None)

    # actions

    def add_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Choose deposit slip images", "", IMAGE_FILTER)
        self.process_images([Path(f) for f in files])

    def process_images(self, files: list[Path]):
        if not files:
            return
        # Start reading every slip in the background straight away, then review them one by one.
        have_key = bool(self.settings.effective_api_key())
        jobs = [start_job(f, self.settings) if have_key else None for f in files]
        saved = 0
        for i, (path, job) in enumerate(zip(files, jobs), start=1):
            dlg = SlipDialog(self, self.db, self.settings, Slip(branch=self.settings.branch),
                             new_image=path, job=job,
                             position=f"{i} of {len(files)}" if len(files) > 1 else "")
            if dlg.exec() == QDialog.Accepted:
                saved += 1
                self.reload()
        self.statusBar().showMessage(f"Saved {saved} of {len(files)} slip(s).", 8000)

    def add_manual(self):
        dlg = SlipDialog(self, self.db, self.settings, Slip(branch=self.settings.branch))
        if dlg.exec() == QDialog.Accepted:
            self.reload()

    def edit_selected(self):
        slip = self.selected_slip()
        if not slip:
            return
        dlg = SlipDialog(self, self.db, self.settings, slip)
        if slip.image_path and not self.db.image_abspath(slip):
            dlg.status.setText("⚠ The image file for this slip is missing from the data folder.")
        if dlg.exec() == QDialog.Accepted:
            self.reload()

    def delete_selected(self):
        slip = self.selected_slip()
        if not slip:
            return
        if QMessageBox.question(
            self, "Delete slip",
            f"Delete the {slip.deposit_date} slip ({slip.account}, ${slip.total:,.2f})?\n"
            "Its image is moved to the 'deleted' folder inside the data folder.",
        ) == QMessageBox.Yes:
            self.db.delete(slip.id)
            self.reload()

    def export_excel(self):
        default = str(Path(self.settings.data_dir) / "Deposit_Slips.xlsx")
        path, _ = QFileDialog.getSaveFileName(self, "Export to Excel", default, "Excel (*.xlsx)")
        if not path:
            return
        only_visible = (self.filter_account.currentText() != "All accounts" or self.search.text())
        slips = self.visible_slips() if only_visible else None
        try:
            export_workbook(path, self.db, self.settings, slips)
        except PermissionError:
            QMessageBox.warning(self, "Export failed", "Close the file in Excel first, then try again.")
            return
        if QMessageBox.question(self, "Exported", f"Saved to\n{path}\n\nOpen it now?") == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def import_excel(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import from Excel", "", "Excel (*.xlsx)")
        if not path:
            return
        try:
            imported, skipped = import_workbook(path, self.db, self.settings)
        except Exception as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        self.reload()
        QMessageBox.information(self, "Import finished",
                                f"Imported {imported} slip(s); skipped {skipped} already saved.\n\n"
                                "Imported rows have no image yet. To attach the photo, open a "
                                "row with Edit and click 'Attach / replace image…'.")

    def open_settings(self):
        old_dir = self.settings.data_dir
        if SettingsDialog(self, self.settings).exec() != QDialog.Accepted:
            return
        if self.settings.data_dir != old_dir:
            self.db.close()
            self.db = Database(self.settings.data_dir)
        self.reload()

    # drag & drop images onto the window

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        files = [Path(u.toLocalFile()) for u in event.mimeData().urls()]
        self.process_images([f for f in files if f.suffix.lower() in IMAGE_EXTS])


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Deposit Slip Tracker")
    icon = Path(__file__).with_name("icon.ico")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
