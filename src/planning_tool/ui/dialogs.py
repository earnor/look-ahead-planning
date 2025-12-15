"""
Dialog Windows

This module contains all dialog windows used in the application.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QDoubleSpinBox, QDateTimeEdit, QLineEdit, QDialogButtonBox
)
from PyQt6.QtCore import QDateTime, QLocale


class DelayInputDialog(QDialog):
    """Dialog for inputting delay information"""
    def __init__(self, module_id: str, phase: str, parent=None):
        super().__init__(parent)
        self.module_id = module_id
        self.phase = phase
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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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

