"""Minimal PyQt GUI for the Google Form autofill script."""
import concurrent.futures
import copy
import json
import random
import re
import string
import sys
import threading
import time

import requests
from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import form
import generator
import main as form_main


TYPE_NAMES = {
    0: "short answer",
    1: "paragraph",
    2: "multiple choice",
    3: "dropdown",
    4: "checkboxes",
    5: "linear scale",
    7: "grid choice",
    9: "date",
    10: "time",
}


# Google Forms embeds validation rules in FB_PUBLIC_LOAD_DATA_ as
# [vtype, subtype, params, error_msg?]. Codes are reverse-engineered.
def extract_validation_map(raw):
    """Return {entry_id: [vtype, subtype, params, msg?]} for fields that have validation."""
    out = {}
    if not raw or not raw[1] or not raw[1][1]:
        return out
    for entry in raw[1][1]:
        if entry[3] == form.FORM_SESSION_TYPE_ID:
            continue
        for sub in entry[4]:
            if len(sub) >= 5 and sub[4]:
                out[sub[0]] = sub[4][0]
    return out


def random_for_validation(rule):
    """Generate a random value that satisfies the rule, or None if we can't."""
    if not rule or len(rule) < 2:
        return None
    vtype, subtype = rule[0], rule[1]
    params = rule[2] if len(rule) > 2 else None
    try:
        if vtype == 1:  # number
            base = int(float(params[0])) if params else 0
            if subtype == 1:   return str(base + random.randint(1, 9000))      # >
            if subtype == 2:   return str(base + random.randint(0, 9000))      # >=
            if subtype == 3:   return str(base - random.randint(1, 9000))      # <
            if subtype == 4:   return str(base - random.randint(0, 9000))      # <=
            if subtype == 5:   return str(base)                                # ==
            if subtype == 6:   return str(base + random.randint(1, 9000))      # !=
            if subtype == 7 and params and len(params) >= 2:                   # between
                a, b = int(float(params[0])), int(float(params[1]))
                return str(random.randint(min(a, b), max(a, b)))
            return str(random.randint(1, 9999))                                # is number / whole
        if vtype == 2:  # text
            if subtype == 102:                                                 # email
                u = "".join(random.choices(string.ascii_lowercase, k=8))
                return f"{u}@example.com"
            if subtype == 103:                                                 # url
                return f"https://example.com/{random.randint(1, 99999)}"
            if subtype == 100 and params:                                      # contains x
                return f"{params[0]} sample {random.randint(100, 999)}"
            if subtype == 101:                                                 # does not contain
                return f"sample {random.randint(100, 999)}"
        if vtype == 4 and params:  # length
            n = int(float(params[0]))
            if subtype == 202:                                                 # max length
                k = max(1, min(n - 1, 40))
                return "".join(random.choices(string.ascii_letters, k=k))
            if subtype == 203:                                                 # min length
                return "".join(random.choices(string.ascii_letters, k=max(n, 1)))
    except (ValueError, IndexError, TypeError):
        return None
    return None


def smart_random_text(required):
    """Better default than 'Ok!' for required free-text fields without a known validation rule."""
    if not required:
        return ""
    return f"sample {random.randint(1000, 9999)}"


DARK = {
    "bg":        "#0d0d0d",
    "surface":   "#161616",
    "surface2":  "#1f1f1f",
    "border":    "#2a2a2a",
    "border_hi": "#3a3a3a",
    "text":      "#e8e8e8",
    "text_dim":  "#888888",
    "text_dim2": "#5a5a5a",
    "accent":    "#e8e8e8",
    "accent_fg": "#0d0d0d",
}

LIGHT = {
    "bg":        "#fafafa",
    "surface":   "#ffffff",
    "surface2":  "#f4f4f4",
    "border":    "#e2e2e2",
    "border_hi": "#cfcfcf",
    "text":      "#171717",
    "text_dim":  "#737373",
    "text_dim2": "#a3a3a3",
    "accent":    "#171717",
    "accent_fg": "#fafafa",
}


import os
import tempfile

_ICON_DIR = tempfile.mkdtemp(prefix="formautofill_")


def _check_icon(fill, stroke):
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 14 14'>"
        f"<rect x='0' y='0' width='14' height='14' rx='3' fill='{fill}'/>"
        f"<path d='M3.2 7.4 L5.8 10 L10.8 4.8' fill='none' stroke='{stroke}' "
        "stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'/></svg>"
    )
    fname = f"check_{fill.lstrip('#')}_{stroke.lstrip('#')}.svg"
    path = os.path.join(_ICON_DIR, fname)
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(svg)
    return path.replace(os.sep, "/")


def stylesheet(p):
    return f"""
    QWidget {{
        background-color: {p['bg']};
        color: {p['text']};
        font-family: -apple-system, "SF Pro Text", "Inter", "Segoe UI", sans-serif;
        font-size: 13px;
    }}
    QLabel#title {{
        font-size: 18px;
        font-weight: 600;
        letter-spacing: -0.3px;
    }}
    QLabel#subtitle {{
        color: {p['text_dim']};
        font-size: 12px;
    }}
    QLabel#sectionLabel {{
        color: {p['text_dim']};
        font-size: 11px;
        font-weight: 500;
        letter-spacing: 0.6px;
    }}
    QLabel#placeholder {{
        color: {p['text_dim2']};
        font-size: 12px;
        padding: 32px 0;
    }}
    QLabel#progressText {{
        color: {p['text_dim']};
        font-family: "SF Mono", "JetBrains Mono", Menlo, monospace;
        font-size: 11px;
    }}
    QLabel#fieldName {{
        font-size: 13px;
        font-weight: 500;
        color: {p['text']};
    }}
    QLabel#fieldMeta {{
        font-size: 11px;
        color: {p['text_dim']};
    }}
    QLabel#inlineLabel {{
        color: {p['text_dim']};
        font-size: 12px;
    }}
    QLineEdit, QSpinBox {{
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 6px;
        padding: 8px 10px;
        color: {p['text']};
        selection-background-color: {p['border_hi']};
    }}
    QLineEdit:focus, QSpinBox:focus {{
        border-color: {p['border_hi']};
    }}
    QSpinBox {{
        min-width: 64px;
        padding-right: 16px;
    }}
    QSpinBox::up-button, QSpinBox::down-button {{
        background: transparent;
        border: none;
        width: 14px;
    }}
    QSpinBox::up-arrow {{
        image: none;
        width: 0; height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-bottom: 4px solid {p['text_dim']};
    }}
    QSpinBox::down-arrow {{
        image: none;
        width: 0; height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 4px solid {p['text_dim']};
    }}
    QPushButton {{
        background-color: {p['accent']};
        color: {p['accent_fg']};
        border: 1px solid {p['accent']};
        border-radius: 6px;
        padding: 8px 16px;
        font-weight: 600;
    }}
    QPushButton:hover     {{ background-color: {p['text_dim']}; border-color: {p['text_dim']}; }}
    QPushButton:disabled  {{ background-color: {p['surface2']}; color: {p['text_dim']};
                             border-color: {p['border']}; }}
    QPushButton#ghost {{
        background-color: transparent;
        color: {p['text_dim']};
        border: 1px solid {p['border']};
        padding: 5px 10px;
        font-weight: 500;
        font-size: 12px;
    }}
    QPushButton#ghost:hover     {{ color: {p['text']}; border-color: {p['border_hi']}; }}
    QPushButton#ghost:disabled  {{ color: {p['text_dim2']}; border-color: {p['border']}; }}
    QCheckBox {{
        spacing: 8px;
        color: {p['text']};
    }}
    QCheckBox::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {p['border_hi']};
        border-radius: 3px;
        background: {p['surface']};
    }}
    QCheckBox::indicator:hover {{
        border-color: {p['text_dim']};
    }}
    QCheckBox::indicator:checked {{
        border: none;
        background: transparent;
        image: url("{_check_icon(p['accent'], p['accent_fg'])}");
    }}
    QTextEdit {{
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 6px;
        padding: 10px;
        font-family: "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
        font-size: 12px;
        color: {p['text']};
    }}
    QFrame#fieldRow {{
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 6px;
    }}
    QLineEdit#fieldValue {{
        background-color: {p['bg']};
        border: 1px solid {p['border']};
        border-radius: 4px;
        padding: 5px 8px;
        font-size: 12px;
        color: {p['text']};
    }}
    QLineEdit#fieldValue:focus {{
        border-color: {p['border_hi']};
    }}
    QScrollArea {{
        background: transparent;
        border: 1px solid {p['border']};
        border-radius: 6px;
    }}
    QScrollArea > QWidget > QWidget {{
        background: {p['bg']};
    }}
    QProgressBar {{
        background-color: {p['surface2']};
        border: none;
        border-radius: 3px;
        max-height: 6px;
        min-height: 6px;
    }}
    QProgressBar::chunk {{
        background-color: {p['accent']};
        border-radius: 3px;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 4px 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {p['border_hi']};
        border-radius: 3px;
        min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """


class StreamEmitter(QObject):
    text = pyqtSignal(str)

    def write(self, s):
        if s:
            self.text.emit(str(s))

    def flush(self):
        pass


class InspectWorker(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(list, dict)
    error = pyqtSignal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        emitter = StreamEmitter()
        emitter.text.connect(self.log.emit)
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = emitter
        try:
            entries = form.parse_form_entries(self.url, only_required=False)
            if not entries:
                self.error.emit("Failed to parse form. Login may be required.")
                return
            raw = form.get_fb_public_load_data(form.get_form_response_url(self.url))
            validation_map = extract_validation_map(raw)
            self.done.emit(entries, validation_map)
        except Exception as err:
            self.error.emit(str(err))
        finally:
            sys.stdout, sys.stderr = old_out, old_err


HIDDEN_INPUT_RE = re.compile(
    r'<input[^>]+type="hidden"[^>]+name="([^"]+)"[^>]*value="([^"]*)"',
    re.IGNORECASE,
)


def harvest_hidden_fields(view_url):
    """GET the viewform page and return hidden form inputs Google expects on POST
    (fbzx, fvv, partialResponse, pageHistory, submissionTimestamp, *_sentinel, ...).

    Google rejects POSTs that lack these with HTTP 400, even when every entry.* is correct.
    """
    r = requests.get(view_url, timeout=15)
    r.raise_for_status()
    fields = {}
    for name, value in HIDDEN_INPUT_RE.findall(r.text):
        # HTML-decode the value (e.g. &quot; → ")
        value = (value.replace("&quot;", '"').replace("&amp;", "&")
                       .replace("&lt;", "<").replace("&gt;", ">"))
        fields[name] = value
    return fields


class SubmitWorker(QThread):
    progress = pyqtSignal(int, int, int)
    log = pyqtSignal(str)
    finished_all = pyqtSignal(int, int)

    def __init__(self, url, entries, disabled_ids, overrides, count, threads,
                 delay_ms=0, validation_map=None):
        super().__init__()
        self.view_url = url if url.endswith("/viewform") else url.replace(
            "/formResponse", "/viewform"
        )
        self.post_url = form.get_form_response_url(url)
        self.entries = entries
        self.disabled_ids = set(disabled_ids)
        self.overrides = dict(overrides)
        self.count = count
        self.threads = threads
        self.delay = max(0, delay_ms) / 1000.0
        self.validation_map = dict(validation_map or {})
        self.hidden_fields = {}
        self._stop = False
        self._fail_dump_lock = threading.Lock()
        self._fail_dumped = False

    def stop(self):
        self._stop = True

    def _fill(self, type_id, entry_id, options, required=False, entry_name=""):
        if entry_id in self.disabled_ids:
            return ""
        if entry_id in self.overrides:
            val = self.overrides[entry_id]
            if type_id == 4:  # checkboxes — comma-split
                return [p.strip() for p in val.split(",") if p.strip()]
            return val
        # Validation-aware random first
        rule = self.validation_map.get(entry_id)
        if rule:
            v = random_for_validation(rule)
            if v is not None:
                return v
        # Better default for required free text (vs. main.py's 'Ok!')
        if type_id in (0, 1):
            return smart_random_text(required)
        return form_main.fill_random_value(type_id, entry_id, options, required, entry_name)

    def _build_body(self):
        entries = copy.deepcopy(self.entries)
        entries = form.fill_form_entries(entries, self._fill)
        body_str = generator.generate_form_request_dict(entries, with_comment=False)
        data = json.loads(body_str)
        # strip empty values — Google 400s on empty optional keys
        data = {k: v for k, v in data.items() if v not in ("", [], None)}
        # merge anti-bot tokens & sentinels harvested from the viewform page
        for k, v in self.hidden_fields.items():
            data.setdefault(k, v)
        return data

    def _submit_once(self):
        if self._stop:
            return False, "cancelled"
        if self.delay > 0:
            # break the sleep into 100ms slices so Stop responds quickly
            slept = 0.0
            while slept < self.delay:
                if self._stop:
                    return False, "cancelled"
                step = min(0.1, self.delay - slept)
                time.sleep(step)
                slept += step
        try:
            data = self._build_body()
        except Exception as err:
            return False, f"body build error: {err}"
        try:
            r = requests.post(self.post_url, data=data, timeout=15)
            if r.status_code == 200:
                return True, None
            if r.status_code == 400:
                self._maybe_dump_body(data)
                return False, "HTTP 400 (a field value failed validation — set a specific value for it)"
            return False, f"HTTP {r.status_code}"
        except Exception as err:
            return False, str(err)

    def _maybe_dump_body(self, data):
        """On the first failure of a run, emit the request body so the user can see
        exactly what was sent and which field is likely failing validation."""
        with self._fail_dump_lock:
            if self._fail_dumped:
                return
            self._fail_dumped = True
        entry_body = {k: v for k, v in data.items() if k.startswith("entry.")}
        # decorate keys with the field name from self.entries for readability
        id_to_name = {f"entry.{e['id']}": e.get("container_name", "?") for e in self.entries}
        lines = ["  body sent on first failure:"]
        for k, v in entry_body.items():
            lines.append(f"    {k}  ({id_to_name.get(k, '?')}) = {v!r}")
        self.log.emit("\n".join(lines) + "\n")

    def run(self):
        try:
            self.hidden_fields = harvest_hidden_fields(self.view_url)
        except Exception as err:
            self.log.emit(f"Could not fetch form tokens: {err}\n")
            self.finished_all.emit(0, 0)
            return
        if "fbzx" not in self.hidden_fields:
            self.log.emit("Warning: fbzx token not found; submissions may fail.\n")

        done = success = fail = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.threads) as ex:
            futures = {ex.submit(self._submit_once): i for i in range(self.count)}
            try:
                for f in concurrent.futures.as_completed(futures):
                    if self._stop:
                        break
                    idx = futures[f]
                    ok, err = f.result()
                    done += 1
                    if ok:
                        success += 1
                    else:
                        fail += 1
                        self.log.emit(f"[{idx + 1}] failed: {err}\n")
                    self.progress.emit(done, success, fail)
            finally:
                if self._stop:
                    ex.shutdown(wait=False, cancel_futures=True)
        self.finished_all.emit(success, fail)


class FieldRow(QFrame):
    def __init__(self, entry):
        super().__init__()
        self.entry = entry
        self.setObjectName("fieldRow")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)

        self.checkbox = QCheckBox()
        self.checkbox.setChecked(True)
        top.addWidget(self.checkbox, alignment=Qt.AlignmentFlag.AlignTop)

        title = entry.get("container_name", "?")
        if entry.get("name"):
            title = f"{title}  —  {entry['name']}"
        name_label = QLabel(title)
        name_label.setObjectName("fieldName")
        name_label.setWordWrap(True)
        top.addWidget(name_label, stretch=1)

        meta_parts = []
        if entry.get("required"):
            meta_parts.append("required")
        t = entry.get("type")
        if isinstance(t, int):
            meta_parts.append(TYPE_NAMES.get(t, f"type {t}"))
        elif t == "required":
            meta_parts.append("system")
        if meta_parts:
            meta_label = QLabel("  ·  ".join(meta_parts))
            meta_label.setObjectName("fieldMeta")
            top.addWidget(meta_label, alignment=Qt.AlignmentFlag.AlignTop)

        outer.addLayout(top)

        opts = entry.get("options")
        if isinstance(opts, list) and opts:
            preview = ", ".join(str(o) for o in opts[:6])
            if len(opts) > 6:
                preview += f"  (+{len(opts) - 6})"
            opts_label = QLabel(preview)
            opts_label.setObjectName("fieldMeta")
            opts_label.setWordWrap(True)
            outer.addWidget(opts_label)

        self.value_input = QLineEdit()
        self.value_input.setObjectName("fieldValue")
        self.value_input.setPlaceholderText(self._placeholder())
        outer.addWidget(self.value_input)

    def _placeholder(self):
        t = self.entry.get("type")
        opts = self.entry.get("options")
        if isinstance(opts, list) and opts:
            return "leave blank for a random option"
        if t == 4:
            return "comma-separated, or blank for random"
        if t == 9:
            return "YYYY-MM-DD, or blank for today"
        if t == 10:
            return "HH:MM, or blank for now"
        return "specific value, or blank for random"

    def is_selected(self):
        return self.checkbox.isChecked()

    def is_required(self):
        return bool(self.entry.get("required"))

    def set_selected(self, val):
        self.checkbox.setChecked(val)

    def value_override(self):
        v = self.value_input.text().strip()
        return v if v else None


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Form Autofill")
        self.resize(740, 860)
        self.dark = True
        self.entries = []
        self.validation_map = {}
        self.field_rows = []
        self.inspect_worker = None
        self.submit_worker = None

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(32, 28, 32, 28)
        outer.setSpacing(16)

        # Header
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("Form Autofill")
        title.setObjectName("title")
        subtitle = QLabel("Inspect, customize, and bulk-submit Google Forms.")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        self.theme_btn = QPushButton("Light")
        self.theme_btn.setObjectName("ghost")
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.clicked.connect(self.toggle_theme)
        header.addWidget(self.theme_btn, alignment=Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header)

        # URL row
        outer.addWidget(self._section("FORM URL"))
        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://docs.google.com/forms/d/e/.../viewform")
        self.url_input.setClearButtonEnabled(True)
        self.url_input.returnPressed.connect(self.inspect)
        url_row.addWidget(self.url_input, stretch=1)
        self.inspect_btn = QPushButton("Inspect")
        self.inspect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.inspect_btn.clicked.connect(self.inspect)
        url_row.addWidget(self.inspect_btn)
        outer.addLayout(url_row)

        # Fields header
        fields_header = QHBoxLayout()
        fields_header.addWidget(self._section("FIELDS"))
        self.fields_count_label = QLabel("")
        self.fields_count_label.setObjectName("subtitle")
        fields_header.addWidget(self.fields_count_label)
        fields_header.addStretch()
        self.all_btn = QPushButton("All")
        self.all_btn.setObjectName("ghost")
        self.all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.all_btn.clicked.connect(self.select_all)
        self.all_btn.setEnabled(False)
        fields_header.addWidget(self.all_btn)
        self.required_only_btn = QPushButton("Required only")
        self.required_only_btn.setObjectName("ghost")
        self.required_only_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.required_only_btn.clicked.connect(self.select_required_only)
        self.required_only_btn.setEnabled(False)
        fields_header.addWidget(self.required_only_btn)
        outer.addLayout(fields_header)

        # Fields scroll area
        self.fields_scroll = QScrollArea()
        self.fields_scroll.setWidgetResizable(True)
        self.fields_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.fields_container = QWidget()
        self.fields_layout = QVBoxLayout(self.fields_container)
        self.fields_layout.setContentsMargins(10, 10, 10, 10)
        self.fields_layout.setSpacing(6)
        self.fields_layout.addStretch()
        self.fields_scroll.setWidget(self.fields_container)
        self.fields_scroll.setMinimumHeight(180)
        self.fields_scroll.setMaximumHeight(260)
        outer.addWidget(self.fields_scroll)
        self._show_fields_placeholder("Inspect a URL to load its fields.")

        # Submissions
        outer.addWidget(self._section("SUBMISSIONS"))
        sub_row = QHBoxLayout()
        sub_row.setSpacing(10)
        count_l = QLabel("Count")
        count_l.setObjectName("inlineLabel")
        sub_row.addWidget(count_l)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 10000)
        self.count_spin.setValue(1)
        sub_row.addWidget(self.count_spin)
        sub_row.addSpacing(16)
        threads_l = QLabel("Threads")
        threads_l.setObjectName("inlineLabel")
        sub_row.addWidget(threads_l)
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 32)
        self.threads_spin.setValue(4)
        sub_row.addWidget(self.threads_spin)
        sub_row.addSpacing(16)
        delay_l = QLabel("Delay (ms)")
        delay_l.setObjectName("inlineLabel")
        sub_row.addWidget(delay_l)
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 60000)
        self.delay_spin.setSingleStep(100)
        self.delay_spin.setValue(0)
        sub_row.addWidget(self.delay_spin)
        sub_row.addStretch()
        self.submit_btn = QPushButton("Submit")
        self.submit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.submit_btn.clicked.connect(self.on_submit_clicked)
        self.submit_btn.setEnabled(False)
        sub_row.addWidget(self.submit_btn)
        outer.addLayout(sub_row)

        # Progress
        prog_row = QHBoxLayout()
        prog_row.setSpacing(12)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        prog_row.addWidget(self.progress, stretch=1)
        self.progress_text = QLabel("idle")
        self.progress_text.setObjectName("progressText")
        prog_row.addWidget(self.progress_text)
        outer.addLayout(prog_row)

        # Log
        outer.addWidget(self._section("OUTPUT"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Logs will appear here.")
        outer.addWidget(self.log_view, stretch=1)

        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.log_view.clear)

        self.apply_theme()

    # ----- helpers -----
    def _section(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("sectionLabel")
        return lbl

    def _clear_fields_layout(self):
        while self.fields_layout.count():
            item = self.fields_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.field_rows = []

    def _show_fields_placeholder(self, text):
        self._clear_fields_layout()
        ph = QLabel(text)
        ph.setObjectName("placeholder")
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fields_layout.addWidget(ph)
        self.fields_layout.addStretch()

    # ----- theme -----
    def apply_theme(self):
        palette = DARK if self.dark else LIGHT
        self.setStyleSheet(stylesheet(palette))
        self.theme_btn.setText("Light" if self.dark else "Dark")

    def toggle_theme(self):
        self.dark = not self.dark
        self.apply_theme()

    # ----- log -----
    def append_log(self, text):
        cursor = self.log_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()
        # Mirror to terminal for headless visibility
        sys.__stdout__.write(text)
        if not text.endswith("\n"):
            sys.__stdout__.write("")
        sys.__stdout__.flush()

    # ----- inspect -----
    def inspect(self):
        if self.inspect_worker and self.inspect_worker.isRunning():
            return
        url = self.url_input.text().strip()
        if not url:
            self.append_log("Enter a form URL first.\n")
            return
        self.inspect_btn.setEnabled(False)
        self.inspect_btn.setText("Inspecting...")
        self.submit_btn.setEnabled(False)
        self.required_only_btn.setEnabled(False)
        self.all_btn.setEnabled(False)
        self._show_fields_placeholder("Loading fields...")
        self.fields_count_label.setText("")
        self.append_log(f"Inspecting {url}\n")

        self.inspect_worker = InspectWorker(url)
        self.inspect_worker.log.connect(self.append_log)
        self.inspect_worker.done.connect(self.on_inspect_done)
        self.inspect_worker.error.connect(self.on_inspect_error)
        self.inspect_worker.start()

    def on_inspect_done(self, entries, validation_map=None):
        self.entries = entries
        self.validation_map = validation_map or {}
        self._clear_fields_layout()
        for entry in entries:
            row = FieldRow(entry)
            self.fields_layout.addWidget(row)
            self.field_rows.append(row)
        self.fields_layout.addStretch()
        self.fields_count_label.setText(f"  ({len(entries)})")
        validated = sum(1 for e in entries if e.get("id") in self.validation_map)
        suffix = f"  ({validated} with validation rules)" if validated else ""
        self.append_log(f"Found {len(entries)} field(s){suffix}.\n")
        self.inspect_btn.setEnabled(True)
        self.inspect_btn.setText("Inspect")
        self.submit_btn.setEnabled(True)
        self.required_only_btn.setEnabled(True)
        self.all_btn.setEnabled(True)

    def on_inspect_error(self, msg):
        self.append_log(f"Inspect error: {msg}\n")
        self.inspect_btn.setEnabled(True)
        self.inspect_btn.setText("Inspect")
        self._show_fields_placeholder("Inspect a URL to load its fields.")

    # ----- field selection -----
    def select_all(self):
        for row in self.field_rows:
            row.set_selected(True)

    def select_required_only(self):
        for row in self.field_rows:
            row.set_selected(row.is_required())

    # ----- submit -----
    def on_submit_clicked(self):
        if self.submit_worker and self.submit_worker.isRunning():
            self.stop_submission()
        else:
            self.start_submission()

    def start_submission(self):
        if not self.entries:
            self.append_log("Inspect a form first.\n")
            return
        url = self.url_input.text().strip()
        disabled_ids = {r.entry["id"] for r in self.field_rows if not r.is_selected()}
        overrides = {
            r.entry["id"]: r.value_override()
            for r in self.field_rows
            if r.value_override() is not None
        }
        count = self.count_spin.value()
        threads = min(self.threads_spin.value(), count)
        delay_ms = self.delay_spin.value()

        # Pre-submission warning: required free-text fields with no override AND
        # no validation rule we can auto-satisfy.
        risky = []
        for row in self.field_rows:
            if not row.is_selected() or row.value_override() is not None:
                continue
            e = row.entry
            if not (e.get("required") and e.get("type") in (0, 1) and not e.get("options")):
                continue
            rule = self.validation_map.get(e.get("id"))
            if rule and random_for_validation(rule) is not None:
                continue
            risky.append(e.get("container_name", "?"))
        if risky:
            self.append_log(
                "Warning: required text field(s) without override or known validation; "
                "random text may be rejected by Google:\n"
            )
            for name in risky:
                self.append_log(f"  - {name}\n")

        self.progress.setRange(0, count)
        self.progress.setValue(0)
        self.progress_text.setText(f"0 / {count}   OK 0   FAIL 0")

        self.submit_btn.setText("Stop")
        self.inspect_btn.setEnabled(False)
        delay_note = f", {delay_ms}ms delay" if delay_ms > 0 else ""
        self.append_log(f"\nSubmitting {count} time(s) on {threads} thread(s){delay_note}...\n")

        self.submit_worker = SubmitWorker(
            url, self.entries, disabled_ids, overrides, count, threads, delay_ms,
            validation_map=self.validation_map,
        )
        self.submit_worker.progress.connect(self.on_progress)
        self.submit_worker.log.connect(self.append_log)
        self.submit_worker.finished_all.connect(self.on_submit_done)
        self.submit_worker.start()

    def stop_submission(self):
        if not (self.submit_worker and self.submit_worker.isRunning()):
            return
        self.submit_btn.setText("Stopping...")
        self.submit_btn.setEnabled(False)
        self.append_log("Stop requested. Waiting for in-flight requests...\n")
        self.submit_worker.stop()

    def on_progress(self, done, success, fail):
        self.progress.setValue(done)
        total = self.progress.maximum()
        self.progress_text.setText(f"{done} / {total}   OK {success}   FAIL {fail}")

    def on_submit_done(self, success, fail):
        self.submit_btn.setEnabled(True)
        self.submit_btn.setText("Submit")
        self.inspect_btn.setEnabled(True)
        stopped = self.submit_worker is not None and self.submit_worker._stop
        verb = "Stopped" if stopped else "Complete"
        self.append_log(f"{verb}: {success} OK, {fail} failed.\n")

    def closeEvent(self, event):
        if self.submit_worker and self.submit_worker.isRunning():
            self.submit_worker.stop()
            self.submit_worker.wait(1500)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Form Autofill")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
