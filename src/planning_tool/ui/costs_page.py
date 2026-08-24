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
    for token in ("CHF", "chf", "€", "$"):
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


def format_chf(amount: float) -> str:
    if abs(amount - round(amount)) < 1e-9:
        return f"{int(round(amount)):,} CHF"
    return f"{amount:,.2f} CHF"


def format_signed_chf(amount: float) -> str:
    sign = "+" if amount > 0 else ""
    return f"{sign}{format_chf(amount)}"


def _rate(value: Optional[float]) -> float:
    return 0.0 if value is None else float(value)


def compute_monetised_costs(
    chosen_days: Optional[int],
    original_days: Optional[int],
    chosen_trucks: int,
    original_trucks: int,
    crane_per_day: Optional[float],
    crew_per_day: Optional[float],
    custom_per_day: list[float],
    cost_per_truck: Optional[float],
    occupant_cost_per_day: Optional[float],
    residents: Optional[float],
    area_m2: Optional[float],
    biodiversity_per_m2_day: Optional[float],
) -> dict:
    """Pure cost arithmetic used by the Costs page (and tests)."""
    daily_construction = (
        _rate(crane_per_day)
        + _rate(crew_per_day)
        + sum(_rate(v) for v in custom_per_day)
    )

    def times_days(days: Optional[int], unit: float) -> Optional[float]:
        if days is None:
            return None
        return days * unit

    construction_chosen = times_days(chosen_days, daily_construction)
    construction_original = times_days(original_days, daily_construction)
    batch_chosen = chosen_trucks * _rate(cost_per_truck)
    batch_original = original_trucks * _rate(cost_per_truck)
    occupants_chosen = times_days(
        chosen_days, _rate(occupant_cost_per_day) * _rate(residents)
    )
    occupants_original = times_days(
        original_days, _rate(occupant_cost_per_day) * _rate(residents)
    )
    biodiversity_chosen = times_days(
        chosen_days, _rate(area_m2) * _rate(biodiversity_per_m2_day)
    )
    biodiversity_original = times_days(
        original_days, _rate(area_m2) * _rate(biodiversity_per_m2_day)
    )

    def add_total(*parts: Optional[float]) -> float:
        return sum(0.0 if part is None else part for part in parts)

    return {
        "construction_chosen": construction_chosen,
        "construction_original": construction_original,
        "batch_chosen": batch_chosen,
        "batch_original": batch_original,
        "occupants_chosen": occupants_chosen,
        "occupants_original": occupants_original,
        "biodiversity_chosen": biodiversity_chosen,
        "biodiversity_original": biodiversity_original,
        "total_chosen": add_total(
            construction_chosen, batch_chosen, occupants_chosen, biodiversity_chosen
        ),
        "total_original": add_total(
            construction_original,
            batch_original,
            occupants_original,
            biodiversity_original,
        ),
    }


def _money_text(amount: Optional[float]) -> str:
    return "—" if amount is None else format_chf(amount)


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

    def set_delta(self, chosen_total: Optional[float], original_total: Optional[float]):
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
        self.delta_label.setText(f"{format_signed_chf(change)} vs original")
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
        residents: Optional[float] = None,
        area_m2: Optional[float] = None,
    ):
        self.total_label.setText(_money_text(total))
        day_txt = _days_text(days)
        self._rows["construction"][0].setText(day_txt)
        self._rows["construction"][1].setText(_money_text(construction))
        self._rows["batch"][0].setText(f"{trucks} trucks")
        self._rows["batch"][1].setText(_money_text(batch))
        occupant_qty = day_txt
        if residents is not None:
            occupant_qty = f"{day_txt} · {residents:g} residents"
        self._rows["occupants"][0].setText(occupant_qty)
        self._rows["occupants"][1].setText(_money_text(occupants))
        bio_qty = day_txt
        if area_m2 is not None:
            bio_qty = f"{day_txt} · {area_m2:g} m²"
        self._rows["biodiversity"][0].setText(bio_qty)
        self._rows["biodiversity"][1].setText(_money_text(biodiversity))
        self._rows["total"][1].setText(_money_text(total))


class CostsPage(QWidget):
    """Compare monetised costs of a chosen schedule against Version 0."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.engine = None
        self.project_id = None
        self.main_window = None
        self.version_id_map = {}
        self.original_version_id = None
        self.custom_term_rows: list[dict] = []
        self.chosen_days: Optional[int] = None
        self.original_days: Optional[int] = None
        self.chosen_trucks = 0
        self.original_trucks = 0
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
            "Compare monetised costs of a chosen schedule against the original plan "
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 13px; color: #6B7280;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        layout.addWidget(self._build_rates_card())

        self.chosen_panel = _CostPanel("Chosen schedule")
        self.chosen_version_combo = QComboBox()
        self.chosen_version_combo.setStyleSheet(COMBO_STYLE)
        self.chosen_version_combo.currentIndexChanged.connect(self._on_chosen_version_changed)
        self.chosen_panel.add_selector(self.chosen_version_combo)
        layout.addWidget(self.chosen_panel)

        self.original_panel = _CostPanel(
            "Original schedule",
        )
        layout.addWidget(self.original_panel)
        layout.addStretch(1)

        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-size: 12px; font-weight: 600; color: #334155;")
        return label

    def _add_input_row(
        self, parent_layout: QVBoxLayout, caption: str, placeholder: str
    ) -> QLineEdit:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(caption)
        label.setStyleSheet("font-size: 12px; color: #475569;")
        label.setMinimumWidth(170)
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setStyleSheet(INPUT_STYLE)
        field.textChanged.connect(self._refresh_cost_display)
        row.addWidget(label)
        row.addWidget(field, 1)
        parent_layout.addLayout(row)
        return field

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

        heading = QLabel("Unit rates")
        heading.setStyleSheet("font-size: 16px; font-weight: 600; color: #0F172A;")
        note = QLabel(
            "The same rates are applied to both schedules. "
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 12px; color: #64748B;")
        layout.addWidget(heading)
        layout.addWidget(note)

        columns = QHBoxLayout()
        columns.setSpacing(24)

        construction = QVBoxLayout()
        construction.setSpacing(8)
        construction.addWidget(self._section_label("1. Construction"))
        self.crane_cost_input = self._add_input_row(
            construction, "Crane cost / day", "CHF / day"
        )
        self.crew_cost_input = self._add_input_row(
            construction, "Working crew cost / day", "CHF / day"
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
            other, "Cost per truck", "CHF / truck"
        )
        other.addWidget(self._section_label("3. Disruption to occupants"))
        self.occupant_cost_input = self._add_input_row(
            other, "Cost / resident / day", "CHF / person / day"
        )
        self.residents_input = self._add_input_row(
            other, "Nearby residents", "number of people"
        )
        other.addWidget(self._section_label("4. Biodiversity"))
        self.area_input = self._add_input_row(other, "Occupied area", "m²")
        self.biodiversity_price_input = self._add_input_row(
            other, "Price / m² / day", "CHF / m² / day"
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
        rate_edit.setPlaceholderText("CHF / day")
        rate_edit.setStyleSheet(INPUT_STYLE)
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
        row.addWidget(remove_btn)

        self.custom_terms_layout.addWidget(row_widget)
        self.custom_term_rows.append(
            {"widget": row_widget, "name": name_edit, "rate": rate_edit}
        )
        self._refresh_cost_display()

    def _remove_custom_term(self, row_widget: QWidget):
        self.custom_term_rows = [
            row for row in self.custom_term_rows if row["widget"] is not row_widget
        ]
        self.custom_terms_layout.removeWidget(row_widget)
        row_widget.deleteLater()
        self._refresh_cost_display()

    def load_version_list(self, engine, project_id: Optional[int]):
        """Populate the chosen-schedule combo and refresh both cost panels."""
        self.engine = engine
        self.project_id = project_id
        self.original_version_id = None
        self.version_id_map = {}

        self.chosen_version_combo.blockSignals(True)
        self.chosen_version_combo.clear()

        if project_id is None or engine is None:
            self.chosen_version_combo.blockSignals(False)
            self._set_quantities(None, None, 0, 0)
            self.chosen_panel.set_heading("Chosen schedule")
            self.original_panel.set_heading("Original schedule (Version 0)")
            return

        from planning_tool.datamanager import ScheduleDataManager

        mgr = ScheduleDataManager(engine)
        versions_table = mgr.optimization_versions_table_name(project_id)
        inspector = inspect(engine)
        if versions_table not in inspector.get_table_names():
            self.chosen_version_combo.blockSignals(False)
            self._set_quantities(None, None, 0, 0)
            return

        try:
            query = (
                f'SELECT version_id, version_number FROM "{versions_table}" '
                f"ORDER BY version_id DESC"
            )
            versions_df = pd.read_sql(text(query), engine)
            for _, row in versions_df.iterrows():
                version_id = int(row["version_id"])
                version_number = row["version_number"]
                index = self.chosen_version_combo.count()
                self.chosen_version_combo.addItem(f"Version {version_number}")
                self.version_id_map[index] = version_id
                if int(version_number) == 0:
                    self.original_version_id = version_id
            if self.chosen_version_combo.count() > 0:
                self.chosen_version_combo.setCurrentIndex(0)
        except Exception as exc:
            print(f"Error loading Costs version list: {exc}")

        self.chosen_version_combo.blockSignals(False)
        self._reload_schedules()

    def _on_chosen_version_changed(self, _index: int = 0):
        self._reload_schedules()

    def _reload_schedules(self):
        from planning_tool.ui.pages import ComparisonPage

        if self.engine is None or self.project_id is None:
            self._set_quantities(None, None, 0, 0)
            return

        settings = {}
        if self.main_window:
            settings = self.main_window._get_active_settings() or {}

        chosen_id = self.version_id_map.get(self.chosen_version_combo.currentIndex())
        chosen_df, chosen_label, chosen_start = self._load_version_dataframe(
            chosen_id, role="chosen"
        )
        original_df, original_label, original_start = self._load_version_dataframe(
            self.original_version_id, role="original"
        )
        if self.original_version_id is None:
            original_label = "Original schedule (Version 0 — not found)"

        project_start = chosen_start or original_start
        start_date = ComparisonPage._parse_project_start_date(project_start, settings)

        self.chosen_panel.set_heading(chosen_label)
        self.original_panel.set_heading(original_label)

        chosen_days, chosen_trucks = self._quantities_from_df(
            chosen_df, settings, start_date
        )
        original_days, original_trucks = self._quantities_from_df(
            original_df, settings, start_date
        )
        self._set_quantities(chosen_days, original_days, chosen_trucks, original_trucks)

    def _load_version_dataframe(self, version_id: Optional[int], role: str = "chosen"):
        empty_label = (
            "Chosen schedule" if role == "chosen" else "Original schedule (Version 0)"
        )
        empty = (pd.DataFrame(), empty_label, None)
        if version_id is None or self.engine is None or self.project_id is None:
            return empty

        from planning_tool.datamanager import ScheduleDataManager

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
                if role == "original":
                    label = f"Original schedule (Version {version_number})"
                else:
                    label = f"Chosen schedule (Version {version_number})"
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

    def _quantities_from_df(self, solution_df: pd.DataFrame, settings, start_date):
        from planning_tool.ui.pages import ComparisonPage

        trucks = 0
        if not solution_df.empty:
            transport_start = solution_df.get("Transport_Start")
            if transport_start is not None:
                trucks = len(transport_start.dropna().unique())
        days = ComparisonPage._count_handover_working_days(
            self, solution_df, settings, start_date
        )
        return days, trucks

    def _set_quantities(
        self,
        chosen_days: Optional[int],
        original_days: Optional[int],
        chosen_trucks: int,
        original_trucks: int,
    ):
        self.chosen_days = chosen_days
        self.original_days = original_days
        self.chosen_trucks = chosen_trucks
        self.original_trucks = original_trucks
        self._refresh_cost_display()

    def _collect_custom_rates(self) -> list[float]:
        rates = []
        for row in self.custom_term_rows:
            parsed = parse_non_negative_number(row["rate"].text())
            if parsed is not None:
                rates.append(parsed)
        return rates

    def _refresh_cost_display(self):
        if not hasattr(self, "original_panel"):
            return

        residents = parse_non_negative_number(self.residents_input.text())
        area_m2 = parse_non_negative_number(self.area_input.text())
        costs = compute_monetised_costs(
            chosen_days=self.chosen_days,
            original_days=self.original_days,
            chosen_trucks=self.chosen_trucks,
            original_trucks=self.original_trucks,
            crane_per_day=parse_non_negative_number(self.crane_cost_input.text()),
            crew_per_day=parse_non_negative_number(self.crew_cost_input.text()),
            custom_per_day=self._collect_custom_rates(),
            cost_per_truck=parse_non_negative_number(self.cost_per_truck_input.text()),
            occupant_cost_per_day=parse_non_negative_number(
                self.occupant_cost_input.text()
            ),
            residents=residents,
            area_m2=area_m2,
            biodiversity_per_m2_day=parse_non_negative_number(
                self.biodiversity_price_input.text()
            ),
        )
        self.chosen_panel.set_breakdown(
            days=self.chosen_days,
            trucks=self.chosen_trucks,
            construction=costs["construction_chosen"],
            batch=costs["batch_chosen"],
            occupants=costs["occupants_chosen"],
            biodiversity=costs["biodiversity_chosen"],
            total=costs["total_chosen"],
            residents=residents,
            area_m2=area_m2,
        )
        self.chosen_panel.set_delta(costs["total_chosen"], costs["total_original"])
        self.original_panel.set_breakdown(
            days=self.original_days,
            trucks=self.original_trucks,
            construction=costs["construction_original"],
            batch=costs["batch_original"],
            occupants=costs["occupants_original"],
            biodiversity=costs["biodiversity_original"],
            total=costs["total_original"],
            residents=residents,
            area_m2=area_m2,
        )
