"""
Costs page: monetised comparison of a chosen schedule against the original plan.

Gantt comparison lives on the Comparison page. This page only shows costs:
the chosen (new) version on top, Version 0 underneath.
Unit rates stay on this page only and are not written to the database.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt
from planning_tool.costs import (
    DEFAULT_BIODIVERSITY_PER_M2,
    DEFAULT_COST_PER_TRUCK,
    DEFAULT_CRANE_PER_DAY,
    DEFAULT_CREW_PER_DAY,
    DEFAULT_OCCUPANT_PER_HOUSEHOLD_DAY,
    CostRates,
    ScheduleQuantities,
    compute_monetised_costs,
    crew_count,
    merge_settings,
    parse_project_start_date,
    quantities_from_solution,
)
from planning_tool.datamanager import ScheduleDataManager
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import inspect, text

INPUT_STYLE = """
    QLineEdit {
        border: 1px solid #CBD5E1;
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 13px;
        background: #FFFFFF;
    }
"""

COMBO_STYLE = """
    QComboBox {
        border: 1px solid #D1D5DB;
        border-radius: 6px;
        padding: 6px 12px;
        font-size: 13px;
        background: #FFFFFF;
        min-width: 200px;
    }
"""

CURRENCY_COMBO_STYLE = """
    QComboBox {
        border: 1px solid #D1D5DB;
        border-radius: 6px;
        padding: 6px 12px;
        font-size: 13px;
        background: #FFFFFF;
        min-width: 96px;
    }
"""

UNIT_LABEL_STYLE = "font-size: 12px; color: #64748B;"

DEFAULT_CURRENCY = "CHF"
CURRENCY_CHOICES = (("CHF", "CHF"), ("EUR", "EUR"))

BUTTON_STYLE = """
    QPushButton {
        background: #FFFFFF;
        color: #1D4ED8;
        border: 1px solid #BFDBFE;
        border-radius: 6px;
        padding: 6px 12px;
        font-size: 12px;
        font-weight: 500;
    }
    QPushButton:hover {
        background: #EFF6FF;
    }
"""

REMOVE_BUTTON_STYLE = """
    QPushButton {
        background: #FFFFFF;
        color: #B91C1C;
        border: 1px solid #FECACA;
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 12px;
    }
    QPushButton:hover {
        background: #FEF2F2;
    }
"""


def parse_non_negative_number(raw: str) -> Optional[float]:
    text = (raw or "").strip().replace("'", "").replace(",", "")
    for token in ("CHF", "chf", "EURO", "euro", "EUR", "eur", "€", "$"):
        text = text.replace(token, "")
    text = text.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def format_money(amount: float, currency: str = DEFAULT_CURRENCY) -> str:
    if abs(amount - round(amount)) < 1e-9:
        return f"{int(round(amount)):,} {currency}"
    return f"{amount:,.2f} {currency}"


def format_signed_money(amount: float, currency: str = DEFAULT_CURRENCY) -> str:
    sign = "+" if amount > 0 else ""
    return f"{sign}{format_money(amount, currency)}"


def _money_text(amount: Optional[float], currency: str = DEFAULT_CURRENCY) -> str:
    return "—" if amount is None else format_money(amount, currency)


def _days_text(days: Optional[int]) -> str:
    return "—" if days is None else f"{days} working days"


class _CostPanel(QFrame):
    """One schedule's monetised breakdown (chosen on top, original below)."""

    def __init__(self, heading: str, caption: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("CostPanel")
        self.setStyleSheet("""
            QFrame#CostPanel {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 12px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.heading_label = QLabel(heading)
        self.heading_label.setStyleSheet(
            "font-size: 16px; font-weight: 600; color: #111827;"
        )
        self.caption_label = QLabel(caption)
        self.caption_label.setStyleSheet("font-size: 12px; color: #6B7280;")
        self.caption_label.setWordWrap(True)
        self.caption_label.setVisible(bool(caption))
        titles.addWidget(self.heading_label)
        titles.addWidget(self.caption_label)
        header.addLayout(titles, 1)

        totals = QVBoxLayout()
        totals.setSpacing(2)
        totals.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.total_label = QLabel("—")
        self.total_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.total_label.setStyleSheet(
            "font-size: 22px; font-weight: 600; color: #111827;"
        )
        self.delta_label = QLabel("")
        self.delta_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.delta_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #6B7280;")
        self.delta_label.setVisible(False)
        totals.addWidget(self.total_label)
        totals.addWidget(self.delta_label)
        header.addLayout(totals)
        layout.addLayout(header)

        self.selector_host = QHBoxLayout()
        self.selector_host.setContentsMargins(0, 0, 0, 0)
        self.selector_host.setSpacing(8)
        layout.addLayout(self.selector_host)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        self._rows = {}
        for row, key, name in (
            (0, "construction", "Construction cost"),
            (1, "batch", "Batch cost"),
            (2, "occupants", "Disruption to occupants"),
            (3, "biodiversity", "Biodiversity cost"),
        ):
            name_lbl = QLabel(name)
            name_lbl.setStyleSheet("font-size: 13px; font-weight: 500; color: #374151;")
            qty_lbl = QLabel("—")
            qty_lbl.setStyleSheet("font-size: 12px; color: #6B7280;")
            amount_lbl = QLabel("—")
            amount_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            amount_lbl.setStyleSheet("font-size: 14px; font-weight: 600; color: #111827;")
            grid.addWidget(name_lbl, row, 0)
            grid.addWidget(qty_lbl, row, 1)
            grid.addWidget(amount_lbl, row, 2)
            self._rows[key] = (qty_lbl, amount_lbl)

        total_name = QLabel("Total costs")
        total_name.setStyleSheet("font-size: 13px; font-weight: 600; color: #111827;")
        total_qty = QLabel("")
        total_amount = QLabel("—")
        total_amount.setAlignment(Qt.AlignmentFlag.AlignRight)
        total_amount.setStyleSheet("font-size: 15px; font-weight: 600; color: #111827;")
        grid.addWidget(total_name, 4, 0)
        grid.addWidget(total_qty, 4, 1)
        grid.addWidget(total_amount, 4, 2)
        self._rows["total"] = (total_qty, total_amount)
        layout.addLayout(grid)

    def add_selector(self, widget: QWidget):
        label = QLabel("Schedule:")
        label.setStyleSheet("font-size: 13px; font-weight: 500; color: #374151;")
        self.selector_host.addWidget(label)
        self.selector_host.addWidget(widget)
        self.selector_host.addStretch(1)

    def set_heading(self, text: str):
        self.heading_label.setText(text)

    def set_delta(
        self,
        chosen_total: Optional[float],
        original_total: Optional[float],
        currency: str = DEFAULT_CURRENCY,
    ):
        if chosen_total is None or original_total is None:
            self.delta_label.setVisible(False)
            return
        change = chosen_total - original_total
        if abs(change) < 1e-9:
            color = "#6B7280"
        elif change > 0:
            color = "#DC2626"
        else:
            color = "#10B981"
        self.delta_label.setText(f"{format_signed_money(change, currency)} vs lower")
        self.delta_label.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {color};"
        )
        self.delta_label.setVisible(True)

    def set_breakdown(
        self,
        days: Optional[int],
        trucks: int,
        construction: Optional[float],
        batch: Optional[float],
        occupants: Optional[float],
        biodiversity: Optional[float],
        total: Optional[float],
        households: Optional[float] = None,
        area_m2: Optional[float] = None,
        crews: Optional[int] = None,
        currency: str = DEFAULT_CURRENCY,
    ):
        self.total_label.setText(_money_text(total, currency))
        day_txt = _days_text(days)
        construction_qty = day_txt
        if crews is not None:
            crew_label = "crew" if crews == 1 else "crews"
            construction_qty = f"{day_txt} · {crews} {crew_label}"
        self._rows["construction"][0].setText(construction_qty)
        self._rows["construction"][1].setText(_money_text(construction, currency))
        self._rows["batch"][0].setText(f"{trucks} trucks")
        self._rows["batch"][1].setText(_money_text(batch, currency))
        occupant_qty = day_txt
        if households is not None:
            occupant_qty = f"{day_txt} · {households:g} households"
        self._rows["occupants"][0].setText(occupant_qty)
        self._rows["occupants"][1].setText(_money_text(occupants, currency))
        bio_qty = "—"
        if area_m2 is not None:
            bio_qty = f"{area_m2:g} m²"
        self._rows["biodiversity"][0].setText(bio_qty)
        self._rows["biodiversity"][1].setText(_money_text(biodiversity, currency))
        self._rows["total"][1].setText(_money_text(total, currency))


class CostsPage(QWidget):
    """Compare monetised costs of two selected schedules."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.engine = None
        self.project_id = None
        self.main_window = None
        self.version_id_map = {}
        self.custom_term_rows: list[dict] = []
        self._currency_unit_labels: list[tuple[QLabel, str]] = []
        self.chosen_qty = ScheduleQuantities()
        self.original_qty = ScheduleQuantities()
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 12, 4)
        layout.setSpacing(16)

        title = QLabel("Costs")
        title.setStyleSheet("font-size: 24px; font-weight: 600; color: #111827;")
        subtitle = QLabel(
            "Compare monetised costs of two schedules. "
            "The upper panel shows the change relative to the lower one."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 13px; color: #6B7280;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        layout.addWidget(self._build_rates_card())

        self.chosen_panel = _CostPanel("Chosen schedule")
        self.chosen_version_combo = QComboBox()
        self.chosen_version_combo.setStyleSheet(COMBO_STYLE)
        self.chosen_version_combo.currentIndexChanged.connect(self._on_version_selection_changed)
        self.chosen_panel.add_selector(self.chosen_version_combo)
        layout.addWidget(self.chosen_panel)

        self.original_panel = _CostPanel("Lower schedule")
        self.original_version_combo = QComboBox()
        self.original_version_combo.setStyleSheet(COMBO_STYLE)
        self.original_version_combo.currentIndexChanged.connect(self._on_version_selection_changed)
        self.original_panel.add_selector(self.original_version_combo)
        layout.addWidget(self.original_panel)
        layout.addStretch(1)

        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-size: 12px; font-weight: 600; color: #334155;")
        return label

    def _add_input_row(
        self,
        parent_layout: QVBoxLayout,
        caption: str,
        unit: str,
        default: Optional[str] = None,
        currency_template: Optional[str] = None,
        tooltip: Optional[str] = None,
    ) -> QLineEdit:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(caption)
        label.setStyleSheet("font-size: 12px; color: #475569;")
        label.setMinimumWidth(170)
        if tooltip:
            label.setToolTip(tooltip)
            label.setCursor(Qt.CursorShape.WhatsThisCursor)
        field = QLineEdit()
        if default is not None:
            field.setText(default)
        field.setStyleSheet(INPUT_STYLE)
        field.textChanged.connect(self._refresh_cost_display)
        unit_lbl = QLabel(unit)
        unit_lbl.setStyleSheet(UNIT_LABEL_STYLE)
        unit_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(label)
        row.addWidget(field, 1)
        row.addWidget(unit_lbl)
        parent_layout.addLayout(row)
        if currency_template:
            self._register_currency_unit(unit_lbl, currency_template)
        return field

    def _register_currency_unit(self, label: QLabel, template: str):
        self._currency_unit_labels.append((label, template))
        label.setText(template.format(currency=self._current_currency()))

    def _current_currency(self) -> str:
        if not hasattr(self, "currency_combo"):
            return DEFAULT_CURRENCY
        data = self.currency_combo.currentData()
        return str(data) if data else DEFAULT_CURRENCY

    def _on_currency_changed(self, _index: int = 0):
        currency = self._current_currency()
        alive: list[tuple[QLabel, str]] = []
        for label, template in self._currency_unit_labels:
            try:
                label.setText(template.format(currency=currency))
            except RuntimeError:
                continue
            alive.append((label, template))
        self._currency_unit_labels = alive
        self._refresh_cost_display()

    def _build_rates_card(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("RatesCard")
        frame.setStyleSheet("""
            QFrame#RatesCard {
                background: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 12px;
            }
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        heading_row = QHBoxLayout()
        heading_row.setSpacing(12)
        heading = QLabel("Unit rates")
        heading.setStyleSheet("font-size: 16px; font-weight: 600; color: #0F172A;")
        heading_row.addWidget(heading, 1)
        currency_caption = QLabel("Currency")
        currency_caption.setStyleSheet("font-size: 12px; font-weight: 600; color: #334155;")
        self.currency_combo = QComboBox()
        self.currency_combo.setStyleSheet(CURRENCY_COMBO_STYLE)
        for label, code in CURRENCY_CHOICES:
            self.currency_combo.addItem(label, code)
        self.currency_combo.setCurrentIndex(0)
        self.currency_combo.currentIndexChanged.connect(self._on_currency_changed)
        heading_row.addWidget(currency_caption)
        heading_row.addWidget(self.currency_combo)
        note = QLabel(
            "The same unit rates are applied to both schedules. "
            "Working-crew counts come from the Project Variables used when each version was calculated."
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 12px; color: #64748B;")
        layout.addLayout(heading_row)
        layout.addWidget(note)

        columns = QHBoxLayout()
        columns.setSpacing(24)

        construction = QVBoxLayout()
        construction.setSpacing(8)
        construction.addWidget(self._section_label("1. Construction"))
        self.crane_cost_input = self._add_input_row(
            construction,
            "Crane cost",
            "CHF / day",
            default=str(int(DEFAULT_CRANE_PER_DAY)),
            currency_template="{currency} / day",
        )
        self.crew_cost_input = self._add_input_row(
            construction,
            "Working crew cost",
            "CHF / crew / day",
            default=str(int(DEFAULT_CREW_PER_DAY)),
            currency_template="{currency} / crew / day",
            tooltip="one crew = one foreman + three workers",
        )
        custom_title = QLabel("Additional daily cost terms")
        custom_title.setStyleSheet("font-size: 12px; font-weight: 600; color: #334155;")
        construction.addWidget(custom_title)
        self.custom_terms_container = QWidget()
        self.custom_terms_layout = QVBoxLayout(self.custom_terms_container)
        self.custom_terms_layout.setContentsMargins(0, 0, 0, 0)
        self.custom_terms_layout.setSpacing(6)
        construction.addWidget(self.custom_terms_container)
        add_btn = QPushButton("Add cost term")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setStyleSheet(BUTTON_STYLE)
        add_btn.clicked.connect(self._add_custom_term)
        construction.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignLeft)
        construction.addStretch(1)

        other = QVBoxLayout()
        other.setSpacing(8)
        other.addWidget(self._section_label("2. Batch"))
        self.cost_per_truck_input = self._add_input_row(
            other,
            "Cost per truck",
            "CHF / truck",
            default=str(int(DEFAULT_COST_PER_TRUCK)),
            currency_template="{currency} / truck",
        )
        other.addWidget(self._section_label("3. Disruption to occupants"))
        self.occupant_cost_input = self._add_input_row(
            other,
            "Occupant cost",
            "CHF / household / day",
            default=str(int(DEFAULT_OCCUPANT_PER_HOUSEHOLD_DAY)),
            currency_template="{currency} / household / day",
        )
        self.residents_input = self._add_input_row(
            other, "Nearby households", "households"
        )
        other.addWidget(self._section_label("4. Biodiversity"))
        self.area_input = self._add_input_row(other, "Occupied area", "m²")
        self.biodiversity_price_input = self._add_input_row(
            other,
            "Biodiversity restore price",
            "CHF / m²",
            default=str(int(DEFAULT_BIODIVERSITY_PER_M2)),
            currency_template="{currency} / m²",
        )
        other.addStretch(1)

        columns.addLayout(construction, 1)
        columns.addLayout(other, 1)
        layout.addLayout(columns)
        return frame

    def _add_custom_term(self):
        row_widget = QWidget()
        row_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText("Term name")
        name_edit.setStyleSheet(INPUT_STYLE)
        rate_edit = QLineEdit()
        rate_edit.setStyleSheet(INPUT_STYLE)
        unit_lbl = QLabel()
        unit_lbl.setStyleSheet(UNIT_LABEL_STYLE)
        remove_btn = QPushButton("Remove")
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.setStyleSheet(REMOVE_BUTTON_STYLE)

        name_edit.textChanged.connect(self._refresh_cost_display)
        rate_edit.textChanged.connect(self._refresh_cost_display)
        remove_btn.clicked.connect(
            lambda _checked=False, w=row_widget: self._remove_custom_term(w)
        )

        row.addWidget(name_edit, 2)
        row.addWidget(rate_edit, 2)
        row.addWidget(unit_lbl)
        row.addWidget(remove_btn)

        self.custom_terms_layout.addWidget(row_widget)
        self.custom_term_rows.append(
            {"widget": row_widget, "name": name_edit, "rate": rate_edit, "unit": unit_lbl}
        )
        self._register_currency_unit(unit_lbl, "{currency} / day")
        self._refresh_cost_display()

    def _remove_custom_term(self, row_widget: QWidget):
        removed_units = {
            row.get("unit")
            for row in self.custom_term_rows
            if row["widget"] is row_widget
        }
        self.custom_term_rows = [
            row for row in self.custom_term_rows if row["widget"] is not row_widget
        ]
        self._currency_unit_labels = [
            item for item in self._currency_unit_labels if item[0] not in removed_units
        ]
        self.custom_terms_layout.removeWidget(row_widget)
        row_widget.deleteLater()
        self._refresh_cost_display()

    def load_version_list(self, engine, project_id: Optional[int]):
        """Populate both version combos and refresh the cost panels."""
        self.engine = engine
        self.project_id = project_id
        self.version_id_map = {}

        self.chosen_version_combo.blockSignals(True)
        self.original_version_combo.blockSignals(True)
        self.chosen_version_combo.clear()
        self.original_version_combo.clear()

        if project_id is None or engine is None:
            self.chosen_version_combo.blockSignals(False)
            self.original_version_combo.blockSignals(False)
            self._set_quantities(ScheduleQuantities(), ScheduleQuantities())
            self.chosen_panel.set_heading("Chosen schedule")
            self.original_panel.set_heading("Lower schedule")
            return

        mgr = ScheduleDataManager(engine)
        mgr.ensure_delay_and_version_tables(project_id)
        versions_table = mgr.optimization_versions_table_name(project_id)
        inspector = inspect(engine)
        if versions_table not in inspector.get_table_names():
            self.chosen_version_combo.blockSignals(False)
            self.original_version_combo.blockSignals(False)
            self._set_quantities(ScheduleQuantities(), ScheduleQuantities())
            return

        try:
            query = (
                f'SELECT version_id, version_number FROM "{versions_table}" '
                f"ORDER BY version_id DESC"
            )
            versions_df = pd.read_sql(text(query), engine)
            version_0_index = None
            for _, row in versions_df.iterrows():
                version_id = int(row["version_id"])
                version_number = row["version_number"]
                index = self.chosen_version_combo.count()
                label = f"Version {version_number}"
                self.chosen_version_combo.addItem(label)
                self.original_version_combo.addItem(label)
                self.version_id_map[index] = version_id
                if int(version_number) == 0:
                    version_0_index = index
            if self.chosen_version_combo.count() > 0:
                self.chosen_version_combo.setCurrentIndex(0)
                lower_index = (
                    version_0_index
                    if version_0_index is not None
                    else self.original_version_combo.count() - 1
                )
                self.original_version_combo.setCurrentIndex(lower_index)
        except Exception as exc:
            print(f"Error loading Costs version list: {exc}")

        self.chosen_version_combo.blockSignals(False)
        self.original_version_combo.blockSignals(False)
        self._reload_schedules()

    def _on_version_selection_changed(self, _index: int = 0):
        self._reload_schedules()

    def _selected_version_id(self, combo: QComboBox) -> Optional[int]:
        return self.version_id_map.get(combo.currentIndex())

    def _reload_schedules(self):
        if self.engine is None or self.project_id is None:
            self._set_quantities(ScheduleQuantities(), ScheduleQuantities())
            return

        chosen_id = self._selected_version_id(self.chosen_version_combo)
        lower_id = self._selected_version_id(self.original_version_combo)
        chosen_df, chosen_label, chosen_start = self._load_version_dataframe(
            chosen_id, role="chosen"
        )
        lower_df, lower_label, lower_start = self._load_version_dataframe(
            lower_id, role="lower"
        )

        self.chosen_panel.set_heading(chosen_label)
        self.original_panel.set_heading(lower_label)
        self._set_quantities(
            self._quantities_for_version(chosen_id, chosen_df, chosen_start),
            self._quantities_for_version(lower_id, lower_df, lower_start),
        )

    def _load_version_dataframe(self, version_id: Optional[int], role: str = "chosen"):
        empty_label = (
            "Chosen schedule" if role == "chosen" else "Lower schedule"
        )
        empty = (pd.DataFrame(), empty_label, None)
        if version_id is None or self.engine is None or self.project_id is None:
            return empty

        mgr = ScheduleDataManager(self.engine)
        solution_table = mgr.solution_table_name(self.project_id)
        versions_table = mgr.optimization_versions_table_name(self.project_id)
        inspector = inspect(self.engine)
        if solution_table not in inspector.get_table_names():
            return empty

        try:
            available_df = pd.read_sql(
                text(
                    f'SELECT DISTINCT version_id FROM "{solution_table}" '
                    f"WHERE version_id IS NOT NULL"
                ),
                self.engine,
            )
            available_ids = set(
                available_df["version_id"].dropna().astype(int).tolist()
            )
        except Exception:
            available_ids = set()

        if version_id not in available_ids:
            return pd.DataFrame(), f"Version {version_id} (No data)", None

        query = (
            f'SELECT * FROM "{solution_table}" WHERE version_id = :version_id '
            f"ORDER BY Production_Start ASC"
        )
        df = pd.read_sql(text(query), self.engine, params={"version_id": version_id})
        label = f"Version {version_id}"
        start_datetime = None
        if versions_table in inspector.get_table_names():
            v_result = pd.read_sql(
                text(
                    f'SELECT version_number, project_start_datetime, base_version_id '
                    f'FROM "{versions_table}" WHERE version_id = :version_id'
                ),
                self.engine,
                params={"version_id": version_id},
            )
            if not v_result.empty:
                version_number = v_result.iloc[0]["version_number"]
                if role == "chosen":
                    label = f"Chosen schedule (Version {version_number})"
                else:
                    label = f"Lower schedule (Version {version_number})"
                if pd.notna(v_result.iloc[0]["project_start_datetime"]):
                    start_datetime = v_result.iloc[0]["project_start_datetime"]
                base_version_id = v_result.iloc[0].get("base_version_id")
                if pd.notna(base_version_id) and self.main_window:
                    df_base = pd.read_sql(
                        text(
                            f'SELECT * FROM "{solution_table}" '
                            f"WHERE version_id = :version_id ORDER BY Production_Start ASC"
                        ),
                        self.engine,
                        params={"version_id": int(base_version_id)},
                    )
                    df = self.main_window._merge_solution_for_display(df, df_base)
        return df, label, start_datetime

    def _live_settings(self) -> dict:
        if self.main_window:
            return self.main_window._get_active_settings() or {}
        return {}

    def _build_slots(self):
        if not self.main_window:
            return None
        return self.main_window._build_working_calendar_slots

    def _quantities_for_version(
        self,
        version_id: Optional[int],
        solution_df: pd.DataFrame,
        start_datetime: Optional[str],
    ) -> ScheduleQuantities:
        stored = self._load_version_settings(version_id)
        settings = merge_settings(self._live_settings(), stored)
        start_date = parse_project_start_date(start_datetime, settings)
        qty = quantities_from_solution(
            solution_df, settings, start_date, self._build_slots()
        )
        # Crew count is per-version. Never use the live Settings page, or
        # editing Settings for a later Calculate would rewrite Version 0's costs.
        qty.crews = crew_count(stored, default=1)
        return qty

    def _load_version_settings(self, version_id: Optional[int]) -> dict:
        if version_id is None or self.engine is None or self.project_id is None:
            return {}
        mgr = ScheduleDataManager(self.engine)
        versions_table = mgr.optimization_versions_table_name(self.project_id)
        inspector = inspect(self.engine)
        if versions_table not in inspector.get_table_names():
            return {}
        columns = [col["name"] for col in inspector.get_columns(versions_table)]
        if "settings_json" not in columns:
            return {}
        try:
            result = pd.read_sql(
                text(
                    f'SELECT settings_json FROM "{versions_table}" '
                    f"WHERE version_id = :version_id"
                ),
                self.engine,
                params={"version_id": version_id},
            )
        except Exception:
            return {}
        if result.empty:
            return {}
        return ScheduleDataManager.parse_calculate_settings(
            result.iloc[0]["settings_json"]
        )

    def _set_quantities(
        self,
        chosen: ScheduleQuantities,
        original: ScheduleQuantities,
    ):
        self.chosen_qty = chosen
        self.original_qty = original
        self._refresh_cost_display()

    def _collect_custom_rates(self) -> list[float]:
        rates = []
        for row in self.custom_term_rows:
            parsed = parse_non_negative_number(row["rate"].text())
            if parsed is not None:
                rates.append(parsed)
        return rates

    def _current_rates(self) -> CostRates:
        return CostRates(
            crane_per_day=parse_non_negative_number(self.crane_cost_input.text()),
            crew_per_day=parse_non_negative_number(self.crew_cost_input.text()),
            cost_per_truck=parse_non_negative_number(self.cost_per_truck_input.text()),
            occupant_per_household_day=parse_non_negative_number(
                self.occupant_cost_input.text()
            ),
            biodiversity_per_m2=parse_non_negative_number(
                self.biodiversity_price_input.text()
            ),
            extra_per_day=self._collect_custom_rates(),
        )

    def _refresh_cost_display(self):
        if not hasattr(self, "original_panel"):
            return

        households = parse_non_negative_number(self.residents_input.text())
        area_m2 = parse_non_negative_number(self.area_input.text())
        chosen = getattr(self, "chosen_qty", ScheduleQuantities())
        original = getattr(self, "original_qty", ScheduleQuantities())
        currency = self._current_currency()
        costs = compute_monetised_costs(
            chosen, original, self._current_rates(), households, area_m2
        )
        self.chosen_panel.set_breakdown(
            days=chosen.working_days,
            trucks=chosen.trucks,
            construction=costs["construction_chosen"],
            batch=costs["batch_chosen"],
            occupants=costs["occupants_chosen"],
            biodiversity=costs["biodiversity_chosen"],
            total=costs["total_chosen"],
            households=households,
            area_m2=area_m2,
            crews=chosen.crews,
            currency=currency,
        )
        self.chosen_panel.set_delta(
            costs["total_chosen"], costs["total_original"], currency
        )
        self.original_panel.set_breakdown(
            days=original.working_days,
            trucks=original.trucks,
            construction=costs["construction_original"],
            batch=costs["batch_original"],
            occupants=costs["occupants_original"],
            biodiversity=costs["biodiversity_original"],
            total=costs["total_original"],
            households=households,
            area_m2=area_m2,
            crews=original.crews,
            currency=currency,
        )
