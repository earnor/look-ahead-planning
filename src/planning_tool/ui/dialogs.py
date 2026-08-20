"""
Dialog Windows

This module contains all dialog windows used in the application.
"""
from datetime import datetime
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QDoubleSpinBox, QDateTimeEdit, QLineEdit, QDialogButtonBox
)
from PyQt6.QtCore import QDateTime, QLocale


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
