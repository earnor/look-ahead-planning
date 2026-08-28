"""
Dialog Windows

This module contains all dialog windows used in the application.
"""
from datetime import datetime
from pathlib import Path
import threading
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QDoubleSpinBox, QSpinBox, QDateTimeEdit, QLineEdit, QDialogButtonBox,
    QFormLayout,
)
from PyQt6.QtCore import QDateTime, QLocale

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings
except ImportError:
    QWebEngineView = None
    QWebEngineSettings = None


class DelayInputDialog(QDialog):
    """Dialog for inputting delay information"""
    def __init__(self, module_id: str, phase: str, parent=None, status_lookup=None):
        super().__init__(parent)
        self.module_id = module_id
        self.phase = phase
        self.status_lookup = status_lookup
        self.setWindowTitle(f"Delay Input - {module_id}")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        module_label = QLabel(f"<b>Module ID:</b> {module_id}")
        layout.addWidget(module_label)

        phase_label = QLabel(f"<b>Phase:</b> {phase}")
        layout.addWidget(phase_label)

        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Delay Type:"))
        self.delay_type_combo = QComboBox()
        self.delay_type_combo.addItems(["DURATION_EXTENSION", "START_POSTPONEMENT"])
        type_layout.addWidget(self.delay_type_combo)
        layout.addLayout(type_layout)

        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("font-size: 11px; color: #6B7280;")
        layout.addWidget(self.hint_label)

        hours_layout = QHBoxLayout()
        hours_layout.addWidget(QLabel("Delay Hours:"))
        self.delay_hours_spin = QDoubleSpinBox()
        self.delay_hours_spin.setMinimum(0.0)
        self.delay_hours_spin.setMaximum(20.0)
        self.delay_hours_spin.setSingleStep(1.0)
        self.delay_hours_spin.setValue(0.0)
        hours_layout.addWidget(self.delay_hours_spin)
        layout.addLayout(hours_layout)

        tau_layout = QHBoxLayout()
        tau_layout.addWidget(QLabel("Detected At Time (τ):"))
        self.tau_datetime = QDateTimeEdit()
        self.tau_datetime.setCalendarPopup(True)
        self.tau_datetime.setDateTime(QDateTime.currentDateTime())
        self.tau_datetime.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.tau_datetime.setLocale(QLocale(QLocale.Language.English, QLocale.Country.Switzerland))
        tau_layout.addWidget(self.tau_datetime)
        layout.addLayout(tau_layout)

        reason_layout = QHBoxLayout()
        reason_layout.addWidget(QLabel("Reason (optional):"))
        self.reason_input = QLineEdit()
        self.reason_input.setPlaceholderText("Enter delay reason...")
        reason_layout.addWidget(self.reason_input)
        layout.addLayout(reason_layout)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.tau_datetime.dateTimeChanged.connect(self._refresh_delay_types)
        self._refresh_delay_types()

    def _detected_datetime(self) -> datetime:
        qdt = self.tau_datetime.dateTime()
        return datetime(
            qdt.date().year(), qdt.date().month(), qdt.date().day(),
            qdt.time().hour(), qdt.time().minute(), qdt.time().second(),
        )

    def _refresh_delay_types(self):
        if self.status_lookup is None:
            return
        allowed, hint = self.status_lookup(self._detected_datetime())
        previous = self.delay_type_combo.currentText()
        self.delay_type_combo.clear()
        self.delay_type_combo.addItems(allowed)
        if previous in allowed:
            self.delay_type_combo.setCurrentText(previous)
        self.hint_label.setText(hint)
        ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setEnabled(bool(allowed))

    def get_delay_info(self) -> dict:
        """Return delay information as a dictionary"""
        return {
            "module_id": self.module_id,
            "phase": self.phase,
            "delay_type": self.delay_type_combo.currentText(),
            "delay_hours": self.delay_hours_spin.value(),
            "detected_at_datetime": self.tau_datetime.dateTime().toString("yyyy-MM-dd HH:mm:ss"),
            "reason": self.reason_input.text() or None
        }


class AddModuleDialog(QDialog):
    """Collect a new module's name, durations, and installation precedence."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Module")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. VS-03-01")
        form.addRow("Module name:", self.name_input)

        self.production_spin = self._duration_spin()
        self.transport_spin = self._duration_spin()
        self.installation_spin = self._duration_spin()
        form.addRow("Production duration (h):", self.production_spin)
        form.addRow("Transportation duration (h):", self.transport_spin)
        form.addRow("Installation duration (h):", self.installation_spin)

        self.precedence_input = QLineEdit()
        self.precedence_input.setPlaceholderText("e.g. VS-02-21, VS-02-22")
        form.addRow("Precedence:", self.precedence_input)
        layout.addLayout(form)

        hint = QLabel(
            "Precedence is a comma-separated list of existing module IDs that must "
            "finish installation before this module can start. Leave blank if none."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 11px; color: #6B7280;")
        layout.addWidget(hint)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @staticmethod
    def _duration_spin() -> QSpinBox:
        spin = QSpinBox()
        spin.setMinimum(1)
        spin.setMaximum(999)
        spin.setValue(1)
        return spin

    def accept(self):
        if not self.name_input.text().strip():
            self.name_input.setFocus()
            self.name_input.setPlaceholderText("Module name is required")
            return
        super().accept()

    def get_module_info(self) -> dict:
        return {
            "module_id": self.name_input.text().strip(),
            "production_duration": self.production_spin.value(),
            "transportation_duration": self.transport_spin.value(),
            "installation_duration": self.installation_spin.value(),
            "precedence": self.precedence_input.text().strip() or None,
        }


class ModelViewerDialog(QDialog):
    """Popup ThatOpen viewer for a project's converted fragments model."""

    def __init__(self, frag_file: Path, parent=None, extra_files: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("4D Model")
        self.resize(1100, 760)
        self._httpd = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if QWebEngineView is None or QWebEngineSettings is None:
            message = QLabel(
                "PyQt6-WebEngine is required to show the 3D viewer in this window.\n"
                "Install it with:\n\npip install PyQt6-WebEngine"
            )
            message.setWordWrap(True)
            message.setStyleSheet("padding: 24px; font-size: 13px;")
            layout.addWidget(message)
            return

        from planning_tool.ifc_model import start_viewer_server

        self._httpd, port = start_viewer_server(
            Path(frag_file), extra_files=extra_files
        )
        thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        thread.start()

        self.view = QWebEngineView(self)
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        self.view.setUrl(QUrl(f"http://127.0.0.1:{port}/"))
        layout.addWidget(self.view)

    def closeEvent(self, event):
        self._shutdown_server()
        super().closeEvent(event)

    def done(self, result: int):
        self._shutdown_server()
        super().done(result)

    def _shutdown_server(self):
        if self._httpd is None:
            return
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:
            pass
        self._httpd = None
