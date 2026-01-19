"""
Page Components

This module contains all main page widgets for the application:
- DashboardPage: Main dashboard with KPIs and tables
- SchedulePage: Module schedule display and management
- UploadPage: File upload interface
- SettingsPage: Project settings configuration
- ComparisonPage: Schedule comparison page with Gantt charts and metrics
"""
from functools import reduce
from PyQt6.QtCore import Qt, pyqtSignal, QDate
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QLineEdit, QComboBox,
    QHBoxLayout, QVBoxLayout, QGridLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QSizePolicy, QSpacerItem, QFileDialog, QMessageBox,
    QSplitter, QCheckBox, QGroupBox, QScrollArea, QInputDialog,
    QDateTimeEdit, QTimeEdit, QDialog
)
from PyQt6.QtCore import QDateTime, QTime, QLocale
from pathlib import Path
from sqlalchemy import create_engine
from planning_tool.datamanager import ScheduleDataManager
from planning_tool.ui.widgets import KpiCard, Card, FileDropArea, Chip
from planning_tool.ui.components import DashboardTable, StatusCell
from planning_tool.ui.dialogs import DelayInputDialog
import pandas as pd
import matplotlib
matplotlib.use('Qt5Agg')  # Use Qt5Agg backend for PyQt6
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime, date, time, timedelta
from typing import Optional, Union, List


class DashboardPage(QWidget):
    pageRequested = pyqtSignal(str)  # Signal to request page navigation
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        """Build the dashboard UI according to the design"""
        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(16)

        # Title: Project Overview
        title_label = QLabel("Project Overview")
        title_label.setStyleSheet("font-size: 24px; font-weight: 600; color: #111827; margin-bottom: 8px;")
        subtitle_label = QLabel("Key metrics and schedule status")
        subtitle_label.setStyleSheet("font-size: 14px; color: #6B7280; margin-bottom: 16px;")
        
        title_layout = QVBoxLayout()
        title_layout.setSpacing(4)
        title_layout.addWidget(title_label)
        title_layout.addWidget(subtitle_label)
        main_layout.addLayout(title_layout)

        # Top row: 3 key metrics cards
        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        
        # Planned vs Actual - neutral styling
        self.card_planned_vs_actual = KpiCard(
            "Planned vs Actual",
            "N/A",
            "",
            "",
            accent_color=""  # No accent color for neutral metric
        )
        
        # Critical Tasks - orange accent for warning
        self.card_critical_tasks = KpiCard(
            "Critical Tasks",
            "0",
            "Requiring attention",
            "",
            accent_color="#F59E0B"  # Orange accent
        )
        
        # Start Date - neutral styling
        self.card_start_date = KpiCard(
            "Start Date",
            "N/A",
            "",
            "",
            accent_color=""  # No accent color for neutral metric
        )
        
        top_row.addWidget(self.card_planned_vs_actual)
        top_row.addWidget(self.card_critical_tasks)
        top_row.addWidget(self.card_start_date)
        main_layout.addLayout(top_row)

        # Middle row: 3 status cards
        middle_row = QHBoxLayout()
        middle_row.setSpacing(12)
        
        # Forecast Completion - neutral styling
        self.card_forecast_completion = KpiCard(
            "Forecast Completion",
            "N/A",
            "",
            "",
            accent_color=""
        )
        
        # Factory Storage Modules - neutral styling
        self.card_factory_storage = KpiCard(
            "Factory Storage Modules",
            "0",
            "Ready for transport",
            "",
            accent_color=""
        )
        
        # Site Storage Modules - neutral styling
        self.card_site_storage = KpiCard(
            "Site Storage Modules",
            "0",
            "Awaiting installation",
            "",
            accent_color=""
        )
        
        middle_row.addWidget(self.card_forecast_completion)
        middle_row.addWidget(self.card_factory_storage)
        middle_row.addWidget(self.card_site_storage)
        main_layout.addLayout(middle_row)

        # Bottom section: What's Late This Week table
        self.table = DashboardTable()
        # Connect table's pageRequested signal to this page's signal
        self.table.pageRequested.connect(self.pageRequested.emit)
        main_layout.addWidget(self.table, 1)  # Stretch factor for table


class UploadPage(QWidget):
    projectCreated = pyqtSignal(int, str)  # (project_id, project_name)
    
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.dm = ScheduleDataManager(self.engine)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(60)

        # ------------------ Card 1: Schedule Data Import ------------------
        card1 = Card("Schedule Data Import", trailing_widget=None)
        drop1 = FileDropArea(
            title="Schedule Data Import",
            exts=[".csv"],
        )
        drop1.fileSelected.connect(self.on_create_project_from_csv)

        card1.body.addWidget(drop1)

        # required chips
        req_wrap = QFrame()
        req_lay = QHBoxLayout(req_wrap)
        req_lay.setContentsMargins(0,0,0,0); req_lay.setSpacing(8)
        req_lay.addWidget(QLabel("<b>Required Fields in Upload</b>"))
        for t in ["Module ID", "Installation Duration", "Production Duration", "Transportation Duration", "Installation Precedence"]:
            req_lay.addWidget(Chip(t))
        req_lay.addStretch(1)
        card1.body.addWidget(req_wrap)

        # optional note
        opt = QLabel(
             "<div style='background:#EFF6FF; border:1px solid #DBEAFE; color:#1E3A8A; "
            "border-radius:10px; padding:8px; font-size:20px;'>"
            "Optional: Add something we would like to mention about schedule upload here."
            "</div>"
        )
        card1.body.addWidget(opt)

        # ------------------ Card 2: 3D Building Model Upload ---------------
        card2 = Card("3D Building Model Upload")
        drop2 = FileDropArea(
            title="3D Building Model Upload",
            exts=[".rvt"],
        )
        drop2.fileSelected.connect(self.on_model_files)
        card2.body.addWidget(drop2)

        hint = QLabel(
            "<div style='background:#EFF6FF; border:1px solid #DBEAFE; color:#1E3A8A; "
            "border-radius:10px; padding:8px; font-size:20px;'>"
            "Ensure your model includes element IDs that can be linked to tasks in your schedule. "
            "</div>"
        )
        card2.body.addWidget(hint)

        # add cards to root
        root.addWidget(card1)
        root.addWidget(card2)
        root.addStretch(1)

    # ---------------- callbacks ----------------

    def on_model_files(self, path: str):
        # TODO: 在这里解析IFC/GLTF等或触发后端处理
        print("[Model Upload] selected:", path)

    # ---------------- Process Our Data ----------------
    REQUIRED_COLS = {
        "Module ID", "Installation Duration", "Production Duration", "Transportation Duration", "Installation Precedence"
    }

    def on_create_project_from_csv(self, path: str = None):
        # If path is not provided (e.g., called from elsewhere), open file dialog
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Select CSV", "", "CSV Files (*.csv)")
            if not path: 
                return
        
        # Now ask for project name
        name, ok = QInputDialog.getText(self, "New Project", "Project name:")
        if not ok or not name.strip(): 
            return
        
        try:
            pid = self.dm.create_project_from_csv(name.strip(), path)
            QMessageBox.information(self, "Created", f"Project '{name}' (ID={pid}) ready.")
            # Emit signal to notify MainWindow to update project combo
            self.projectCreated.emit(pid, name.strip())
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))


class SchedulePage(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Schedule")
        self.resize(1240, 760)
        self._all_rows_data = []  # Store all rows for filtering
        self.engine = None  # Database engine (set by MainWindow)
        self.project_id = None  # Current project ID (set by MainWindow)
        self.version_id_map = {}  # Map combobox index to version_id
        self.main_window = None  # Reference to MainWindow (set by MainWindow)
        self._apply_style()
        self._build_ui()

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow { background: #ffffff; }
            QLabel.title { font-size: 14px; font-weight: 600; color: #111; }
            QLabel.section { font-size: 12px; color:#111; font-weight:600; }
            QGroupBox { border: none; margin-top: 8px; }
            QPushButton {
                background: #f1f3f5; border: 1px solid #e5e7eb; border-radius: 8px;
                padding: 6px 10px; font-weight: 500;
            }
            QPushButton:hover { background:#e9ecef; }
            QPushButton.primary {
                background:#0ea5e9; color:white; border: none;
            }
            QComboBox, QLineEdit {
                border:1px solid #e5e7eb; border-radius:8px; padding:6px 8px; background:#fff;
            }
            QTableWidget {
                gridline-color: #E5E7EB; 
                selection-background-color: #DBEAFE;
                selection-color: #0d0d0d; 
                font-size: 13px;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
                background: #FFFFFF;
            }
            QHeaderView::section {
                background: #F9FAFB; 
                padding: 10px 8px; 
                border: none; 
                border-bottom: 1px solid #E5E7EB;
                border-right: 1px solid #E5E7EB;
                font-weight: 600;
                font-size: 12px;
                color: #374151;
            }
            QHeaderView::section:first {
                border-left: none;
            }
            QHeaderView::section:last {
                border-right: none;
            }
            QTableWidget::item {
                border-right: 1px solid #E5E7EB;
                border-bottom: 1px solid #E5E7EB;
            }
            QTableWidget::item:selected {
                background: #DBEAFE;
            }
            QCheckBox { font-size: 13px; }
            QScrollArea { border: none; }
            #SidePanel { background:#fafafa; border-right:1px solid #e5e7eb; }
            #Toolbar { background:#ffffff; border-bottom:1px solid #e5e7eb; }
            #ScenarioBtn { padding:6px 10px; border-radius:8px; }
            #ScenarioBtn[active=\"true\"] { background:#111827; color:white; }
        """)

    def _build_ui(self):
        splitter = QSplitter()
        splitter.setHandleWidth(1)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_main())

        # 初始尺寸（左窄右宽）
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 1080])  # 侧边栏宽度从300减少到200

        # 关键：把 splitter 放进本控件的布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    # ----- Sidebar -----
    def _build_sidebar(self) -> QWidget:
        side = QWidget(); side.setObjectName("SidePanel")
        side.setMinimumWidth(180)  # 设置最小宽度，防止侧边栏太窄
        side.setMaximumWidth(350)  # 设置最大宽度
        layout = QVBoxLayout(side); layout.setContentsMargins(12, 12, 12, 12); layout.setSpacing(10)

        hdr = QHBoxLayout()
        t = QLabel("Filters"); t.setProperty("class", "title")
        clear = QPushButton("Clear All"); clear.clicked.connect(self._clear_all_filters)
        clear.setToolTip("Clear all filters")
        hdr.addWidget(t); hdr.addStretch(1); hdr.addWidget(clear)
        layout.addLayout(hdr)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        body = QWidget(); v = QVBoxLayout(body); v.setContentsMargins(0,0,0,0); v.setSpacing(16)

        sections = [
            ("Status", ["Completed","In Progress","Delayed","Upcoming"]),
        ]
        self._filter_boxes: list[QCheckBox] = []
        self._status_filter_map = {}  # Map status text to checkbox
        for title, items in sections:
            box = QGroupBox()
            grid = QGridLayout(box); grid.setHorizontalSpacing(8); grid.setVerticalSpacing(6)
            grid.addWidget(QLabel(title, parent=box), 0, 0, 1, 2, alignment=Qt.AlignmentFlag.AlignLeft)
            r = 1; c = 0
            for it in items:
                cb = QCheckBox(it); cb.setChecked(True)
                self._filter_boxes.append(cb)
                self._status_filter_map[it] = cb  # Map status to checkbox
                cb.stateChanged.connect(self._apply_status_filter)  # Connect to filter function
                grid.addWidget(cb, r, c)
                r += 1
            v.addWidget(box)

        v.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        return side

    def _clear_all_filters(self):
        for cb in self._filter_boxes:
            cb.setChecked(False)
        self._apply_status_filter()  # Apply filter after clearing

    # ----- Main -----
    def _build_main(self) -> QWidget:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)
        
        # Title: Module Schedule
        title = QLabel("Module Schedule")
        title.setStyleSheet("font-size: 20px; font-weight: 600; color: #111827;")
        v.addWidget(title)
        
        # Toolbar with action buttons
        v.addWidget(self._build_toolbar())
        
        # Table
        v.addWidget(self._build_table(), 1)
        
        # Bottom info section
        v.addWidget(self._build_info_section())
        
        return wrap

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        
        # Version selection combobox
        version_label = QLabel("Version:")
        version_label.setStyleSheet("font-size: 13px; font-weight: 500; color: #374151;")
        h.addWidget(version_label)
        
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(150)
        self.version_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #E5E7EB;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 13px;
                background: #FFFFFF;
                color: #374151;
            }
            QComboBox:hover {
                border-color: #D1D5DB;
            }
            QComboBox::drop-down {
                border: none;
                padding-right: 8px;
            }
        """)
        self.version_combo.currentIndexChanged.connect(self._on_version_changed)
        h.addWidget(self.version_combo)
        
        h.addStretch(1)
        
        # Action buttons with icons (using emoji as placeholders)
        btn_4d = QPushButton("📦 4D Model")
        btn_add = QPushButton("➕ Add Module")
        btn_calculate = QPushButton("☁️ Calculate")
        btn_export = QPushButton("⬇️ Export")
        
        # Apply consistent button styling
        button_style = """
            QPushButton {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 500;
                color: #374151;
            }
            QPushButton:hover {
                background: #F9FAFB;
                border-color: #D1D5DB;
            }
            QPushButton:pressed {
                background: #F3F4F6;
            }
        """
        
        for btn in (btn_4d, btn_add, btn_calculate, btn_export):
            btn.setStyleSheet(button_style)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            h.addWidget(btn)

        # connect calculate button to a handler in parent window via signal/slot later
        # we expose it as an attribute so MainWindow can wire it
        self.btn_calculate = btn_calculate
        self.btn_export = btn_export
        
        return bar

    def _build_table(self) -> QTableWidget:
        # Module Schedule table with 11 columns
        table = QTableWidget(0, 11)
        table.setHorizontalHeaderLabels([
            "Module ID",
            "Fabrication Start Time",
            "Fabrication Duration (h)",
            "Transport Start Time",
            "Transport Duration (h)",
            "Installation Start Time",
            "Installation Duration (h)",
            "Status",
            "Fab. Delay (h)",
            "Trans. Delay (h)",
            "Inst. Delay (h)"
        ])
        
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # Set column widths
        col_widths = [120, 180, 160, 180, 160, 180, 160, 120, 120, 120, 120]
        for i, w in enumerate(col_widths):
            header.resizeSection(i, w)
        
        table.verticalHeader().setVisible(False)
        table.setShowGrid(True)
        table.setGridStyle(Qt.PenStyle.SolidLine)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # Enable double-click editing for Delay columns (columns 8, 9, 10)
        table.cellDoubleClicked.connect(self._on_delay_cell_double_clicked)

        # store for later population
        self.table = table
        return table
    
    def _on_delay_cell_double_clicked(self, row: int, col: int):
        """Handle double-click on Delay columns"""
        # Delay columns are at indices 8, 9, 10
        delay_columns = {8: "FABRICATION", 9: "TRANSPORT", 10: "INSTALLATION"}
        
        if col not in delay_columns:
            return
        
        # Get module ID from the row
        module_id_item = self.table.item(row, 0)
        if not module_id_item:
            return
        
        module_id = module_id_item.text()
        phase = delay_columns[col]
        
        # Show delay input dialog
        dialog = DelayInputDialog(module_id, phase, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            delay_info = dialog.get_delay_info()
            # Update the delay cell
            delay_hours = delay_info["delay_hours"]
            self.table.setItem(row, col, QTableWidgetItem(str(delay_hours)))
            
            # Save delay to database immediately (Phase 5.1)
            # Call MainWindow method to handle the save
            if hasattr(self, 'main_window') and self.main_window:
                self.main_window.save_delay_to_db(delay_info)
            else:
                QMessageBox.warning(self, "Error", "Cannot save delay: MainWindow reference not found.")

    def populate_rows(self, rows: list[dict]):
        """
        Populate schedule table with rows, each row is a dict with keys:
        Module ID, Fabrication Start Time, Fabrication Duration (h),
        Transport Start Time, Transport Duration (h),
        Installation Start Time, Installation Duration (h), Status,
        Fab. Delay (h), Trans. Delay (h), Inst. Delay (h)
        """
        if not hasattr(self, "table"):
            return
        
        # Save all rows data for filtering
        self._all_rows_data = rows.copy()
        
        # Apply filter to populate table
        self._apply_status_filter()
    
    def _apply_status_filter(self):
        """Apply status filter based on checked checkboxes"""
        if not hasattr(self, "table") or not self._all_rows_data:
            return
        
        table = self.table
        table.setRowCount(0)
        
        # Get selected statuses
        selected_statuses = set()
        for status, cb in self._status_filter_map.items():
            if cb.isChecked():
                selected_statuses.add(status)
        
        # If no status is selected, show nothing
        if not selected_statuses:
            return
        
        # Filter and populate rows
        filtered_rows = [
            row for row in self._all_rows_data
            if row.get("Status", "") in selected_statuses
        ]
        
        for r, row in enumerate(filtered_rows):
            table.insertRow(r)
            values = [
                row.get("Module ID", ""),
                row.get("Fabrication Start Time", ""),
                row.get("Fabrication Duration (h)", ""),
                row.get("Transport Start Time", ""),
                row.get("Transport Duration (h)", ""),
                row.get("Installation Start Time", ""),
                row.get("Installation Duration (h)", ""),
                row.get("Status", "Upcoming"),
                row.get("Fab. Delay (h)", "0"),
                row.get("Trans. Delay (h)", "0"),
                row.get("Inst. Delay (h)", "0"),
            ]
            for c, val in enumerate(values):
                if c == 7:  # status cell
                    table.setCellWidget(r, c, StatusCell(str(val)))
                else:
                    item = QTableWidgetItem(str(val))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    table.setItem(r, c, item)

            # Highlight rows that have pending delays
            if row.get("_has_delay", False):
                highlight = QColor("#FEF3C7")
                for c in range(table.columnCount()):
                    item = table.item(r, c)
                    if item:
                        item.setBackground(highlight)
                status_widget = table.cellWidget(r, 7)
                if status_widget:
                    status_widget.setStyleSheet(status_widget.styleSheet() + "background: #FEF3C7; border-radius: 4px;")

    def load_version_list(self, engine, project_id: int, auto_load: bool = True):
        """
        Load version list from database and populate combobox.
        This method should be called from MainWindow when SchedulePage is shown or project changes.
        
        Args:
            engine: Database engine
            project_id: Project ID
            auto_load: If True, automatically load the latest version after populating the list
        """
        print(f"[DEBUG SchedulePage] load_version_list called: project_id={project_id}, auto_load={auto_load}")
        from sqlalchemy import inspect, text
        from planning_tool.datamanager import ScheduleDataManager
        
        self.engine = engine
        self.project_id = project_id
        
        print(f"[DEBUG SchedulePage] engine: {engine}, project_id: {project_id}")
        
        if project_id is None:
            print(f"[DEBUG SchedulePage] project_id is None, clearing combobox")
            self.version_combo.clear()
            return
        
        mgr = ScheduleDataManager(engine)
        versions_table = mgr.optimization_versions_table_name(project_id)
        print(f"[DEBUG SchedulePage] versions_table: {versions_table}")
        
        inspector = inspect(engine)
        table_names = inspector.get_table_names()
        print(f"[DEBUG SchedulePage] Available tables: {table_names}")
        
        if versions_table not in table_names:
            print(f"[DEBUG SchedulePage] versions_table {versions_table} not found in database")
            self.version_combo.clear()
            return
        
        try:
            # Disconnect signal temporarily to avoid triggering load during population
            self.version_combo.blockSignals(True)
            
            # Load versions from database
            query = f'SELECT version_id, version_number FROM "{versions_table}" ORDER BY version_id DESC'
            print(f"[DEBUG SchedulePage] Executing query: {query}")
            versions_df = pd.read_sql(text(query), engine)
            print(f"[DEBUG SchedulePage] Loaded versions:\n{versions_df}")
            
            # Check if version 0 exists
            has_version_0 = False
            version_0_id = None
            if not versions_df.empty:
                version_0_rows = versions_df[versions_df['version_number'] == 0]
                if not version_0_rows.empty:
                    has_version_0 = True
                    version_0_id = int(version_0_rows.iloc[0]['version_id'])
                    print(f"[DEBUG SchedulePage] Found version 0 with version_id={version_0_id}")
            
            # Check if there are NULL version_id records in solution table
            solution_table = mgr.solution_table_name(project_id)
            has_null_data = False
            if solution_table in table_names:
                null_count_query = f'SELECT COUNT(*) as count FROM "{solution_table}" WHERE version_id IS NULL'
                null_count_df = pd.read_sql(text(null_count_query), engine)
                null_count = null_count_df.iloc[0]['count'] if not null_count_df.empty else 0
                has_null_data = (null_count > 0)
                print(f"[DEBUG SchedulePage] NULL version_id records in solution table: {null_count}")
            
            # Store current selection before clearing
            current_selected_version_id = None
            if self.version_combo.currentIndex() >= 0:
                current_selected_version_id = self.version_id_map.get(self.version_combo.currentIndex())
            
            # Clear and populate combobox
            self.version_combo.clear()
            self.version_id_map = {}
            
            if not versions_df.empty:
                for _, row in versions_df.iterrows():
                    version_id = int(row['version_id'])
                    version_number = row['version_number']
                    display_text = f"Version {version_number}"
                    
                    index = self.version_combo.count()
                    self.version_combo.addItem(display_text)
                    self.version_id_map[index] = version_id
                
                # Try to restore previous selection, otherwise select latest version
                if current_selected_version_id is not None:
                    # Find the index of the previously selected version
                    found_index = None
                    for idx, vid in self.version_id_map.items():
                        if vid == current_selected_version_id:
                            found_index = idx
                            break
                    if found_index is not None:
                        self.version_combo.setCurrentIndex(found_index)
                    elif len(versions_df) >= 1:
                        self.version_combo.setCurrentIndex(0)
                elif len(versions_df) >= 1:
                    self.version_combo.setCurrentIndex(0)
            
            self.version_combo.blockSignals(False)
            
            # Trigger load for the selected version after signals are unblocked (if auto_load is True)
            if auto_load and not versions_df.empty and len(versions_df) >= 1:
                self._on_version_changed()
            
        except Exception as e:
            print(f"Error loading version list: {e}")
            self.version_combo.clear()
            self.version_combo.blockSignals(False)
    
    def _on_version_changed(self):
        """Handle version selection change and load schedule data for selected version"""
        print(f"[DEBUG SchedulePage] _on_version_changed called")
        print(f"[DEBUG SchedulePage] engine: {self.engine}, project_id: {self.project_id}")
        
        if self.engine is None or self.project_id is None:
            print(f"[DEBUG SchedulePage] Engine or project_id is None, returning")
            return
        
        # Get selected version_id
        current_index = self.version_combo.currentIndex()
        print(f"[DEBUG SchedulePage] current_index: {current_index}")
        print(f"[DEBUG SchedulePage] version_id_map: {self.version_id_map}")
        
        if current_index < 0:
            print(f"[DEBUG SchedulePage] Invalid current_index, returning")
            return
        
        version_id = self.version_id_map.get(current_index)
        print(f"[DEBUG SchedulePage] version_id: {version_id}")
        
        if version_id is None:
            print(f"[DEBUG SchedulePage] version_id is None, returning")
            return
        
        # Load schedule data for this version
        if self.main_window:
            print(f"[DEBUG SchedulePage] Calling main_window.load_schedule_by_version({self.project_id}, {version_id})")
            self.main_window.load_schedule_by_version(self.project_id, version_id)
        else:
            print(f"[DEBUG SchedulePage] main_window is None, cannot load schedule")

    def _build_info_section(self) -> QWidget:
        """Build the bottom info section with rules"""
        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(8)
        
        # Info text
        info_text = QLabel(
            "Info: Start times and statuses are automatically recalculated using the Calculate button. "
            "Delay fields represent real-world deviations."
        )
        info_text.setStyleSheet("font-size: 12px; color: #6B7280;")
        info_text.setWordWrap(True)
        info_layout.addWidget(info_text)
        
        # Status calculation rules
        rules_label = QLabel("Status Calculation Rules:")
        rules_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #374151; margin-top: 8px;")
        info_layout.addWidget(rules_label)
        
        rules_text = QLabel(
            "• If current time ≥ installation end → <span style='color: #065F46; font-weight: 500;'>Completed</span><br/>"
            "• If current time ≥ fabrication start and &lt; installation end → <span style='color: #1E40AF; font-weight: 500;'>In Progress</span><br/>"
            "• If delay &gt; 0 → <span style='color: #991B1B; font-weight: 500;'>Delayed</span><br/>"
            "• Else → <span style='color: #374151; font-weight: 500;'>Upcoming</span>"
        )
        rules_text.setStyleSheet("font-size: 12px; color: #6B7280;")
        rules_text.setTextFormat(Qt.TextFormat.RichText)
        rules_text.setWordWrap(True)
        info_layout.addWidget(rules_text)
        
        return info_widget


class SettingsPage(QWidget):
    """Settings page for configuring project parameters"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
    
    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(24)
        
        # Project Timeline Section
        layout.addWidget(self._build_project_timeline())
        
        # Working Calendar Section
        layout.addWidget(self._build_working_calendar())
        
        # Project Resources Section
        layout.addWidget(self._build_project_resources())
        
        layout.addStretch(1)
        
        # Save Settings Button
        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("SaveSettingsBtn")
        save_btn.setMinimumHeight(48)
        save_btn.setStyleSheet("""
            QPushButton#SaveSettingsBtn {
                background: #1F2937;
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                padding: 12px 24px;
                font-size: 15px;
                font-weight: 600;
            }
            QPushButton#SaveSettingsBtn:hover {
                background: #374151;
            }
            QPushButton#SaveSettingsBtn:pressed {
                background: #111827;
            }
        """)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        save_btn.clicked.connect(self._save_settings)
        
        scroll.setWidget(content)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)
    
    def _build_section_card(self, title: str) -> QFrame:
        """Helper to create a section card with title"""
        card = QFrame()
        card.setObjectName("SettingsCard")
        card.setStyleSheet("""
            QFrame#SettingsCard {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 12px;
                padding: 20px;
            }
        """)
        
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)
        
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 18px; font-weight: 600; color: #111827;")
        layout.addWidget(title_label)
        
        return card, layout
    
    def _build_project_timeline(self) -> QFrame:
        """Build Project Timeline section"""
        card, layout = self._build_section_card("Project Timeline")
        
        # Project Start Date & Time
        start_group = QVBoxLayout()
        start_label = QLabel("Project Start Date & Time <span style='color: red;'>*</span>")
        start_label.setTextFormat(Qt.TextFormat.RichText)
        start_label.setStyleSheet("font-size: 13px; font-weight: 500; color: #374151; margin-bottom: 6px;")
        start_group.addWidget(start_label)
        
        start_layout = QHBoxLayout()
        self.start_datetime = QDateTimeEdit()
        self.start_datetime.setLocale(QLocale(QLocale.Language.English, QLocale.Country.Switzerland))
        # calendar should start from 2025 instead of 2000
        default_start = QDate(2025, 1, 1)
        self.start_datetime.setMinimumDate(default_start)
        self.start_datetime.setDate(default_start)
        self.start_datetime.setCalendarPopup(True)
        self.start_datetime.setDisplayFormat("MM/dd/yyyy")
        self.start_datetime.setSpecialValueText("mm/dd/yyyy")
        self.start_datetime.setStyleSheet("""
            QDateTimeEdit {
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
                background: #FFFFFF;
            }
            QDateTimeEdit:hover {
                border-color: #9CA3AF;
            }
            QDateTimeEdit::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 30px;
                border-left: 1px solid #D1D5DB;
            }
        """)
        start_layout.addWidget(self.start_datetime)
        start_layout.addStretch(1)
        start_group.addLayout(start_layout)
        layout.addLayout(start_group)
        
        # Target Completion Date
        target_group = QVBoxLayout()
        target_label = QLabel("Target Completion Date")
        target_label.setStyleSheet("font-size: 13px; font-weight: 500; color: #374151; margin-bottom: 6px;")
        target_group.addWidget(target_label)
        
        target_layout = QHBoxLayout()
        self.target_datetime = QDateTimeEdit()
        self.target_datetime.setLocale(QLocale(QLocale.Language.English, QLocale.Country.Switzerland))
        # calendar should also start from 2025 here
        default_target = QDate(2025, 1, 1)
        self.target_datetime.setMinimumDate(default_target)
        self.target_datetime.setDate(default_target)
        self.target_datetime.setCalendarPopup(True)
        self.target_datetime.setDisplayFormat("MM/dd/yyyy")
        self.target_datetime.setSpecialValueText("mm/dd/yyyy")
        self.target_datetime.setStyleSheet("""
            QDateTimeEdit {
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
                background: #FFFFFF;
                color: #9CA3AF;
            }
            QDateTimeEdit:hover {
                border-color: #9CA3AF;
            }
            QDateTimeEdit::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 30px;
                border-left: 1px solid #D1D5DB;
            }
        """)
        target_layout.addWidget(self.target_datetime)
        target_layout.addStretch(1)
        target_group.addLayout(target_layout)
        layout.addLayout(target_group)
        
        # Current Simulation Time
        sim_group = QHBoxLayout()
        sim_label = QLabel("Current Simulation Time")
        sim_label.setStyleSheet("font-size: 13px; font-weight: 500; color: #374151;")
        sim_group.addWidget(sim_label)
        sim_group.addStretch(1)
        
        # Toggle switch (using CheckBox styled as toggle)
        self.use_system_time = QCheckBox("Use System Time")
        self.use_system_time.setChecked(True)
        self.use_system_time.setStyleSheet("""
            QCheckBox {
                font-size: 13px;
                color: #374151;
            }
            QCheckBox::indicator {
                width: 44px;
                height: 24px;
                border-radius: 12px;
                background: #D1D5DB;
            }
            QCheckBox::indicator:checked {
                background: #3B82F6;
            }
            QCheckBox::indicator:checked {
                image: none;
            }
        """)
        sim_group.addWidget(self.use_system_time)
        layout.addLayout(sim_group)
        
        return card
    
    def _build_working_calendar(self) -> QFrame:
        """Build Working Calendar section"""
        card, layout = self._build_section_card("Working Calendar")
        
        # Working Days
        days_group = QVBoxLayout()
        days_label = QLabel("Working Days")
        days_label.setStyleSheet("font-size: 13px; font-weight: 500; color: #374151; margin-bottom: 8px;")
        days_group.addWidget(days_label)
        
        days_layout = QHBoxLayout()
        self.working_days = {}
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for day in days:
            btn = QPushButton(day)
            btn.setCheckable(True)
            btn.setChecked(day in ["Mon", "Tue", "Wed", "Thu", "Fri"])
            self.working_days[day] = btn
            btn.setMinimumSize(50, 36)
            btn.setStyleSheet("""
                QPushButton {
                    border: 1px solid #D1D5DB;
                    border-radius: 6px;
                    font-size: 12px;
                    font-weight: 500;
                    background: #FFFFFF;
                    color: #6B7280;
                }
                QPushButton:checked {
                    background: #3B82F6;
                    color: #FFFFFF;
                    border-color: #3B82F6;
                }
                QPushButton:hover {
                    border-color: #9CA3AF;
                }
            """)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            days_layout.addWidget(btn)
        days_group.addLayout(days_layout)
        layout.addLayout(days_group)
        
        # Daily Working Hours
        hours_group = QVBoxLayout()
        hours_label = QLabel("Daily Working Hours")
        hours_label.setStyleSheet("font-size: 13px; font-weight: 500; color: #374151; margin-bottom: 8px;")
        hours_group.addWidget(hours_label)
        
        hours_layout = QHBoxLayout()
        start_time_layout = QHBoxLayout()
        start_label = QLabel("Start Time:")
        start_label.setStyleSheet("font-size: 12px; color: #6B7280;")
        start_time_layout.addWidget(start_label)
        self.work_start_time = QTimeEdit()
        self.work_start_time.setLocale(QLocale(QLocale.Language.English, QLocale.Country.Switzerland))
        self.work_start_time.setTime(QTime(8, 0))
        self.work_start_time.setDisplayFormat("hh:mm AP")
        self.work_start_time.setStyleSheet("""
            QTimeEdit {
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
                background: #FFFFFF;
            }
        """)
        start_time_layout.addWidget(self.work_start_time)
        hours_layout.addLayout(start_time_layout)
        
        hours_layout.addSpacing(20)
        
        end_time_layout = QHBoxLayout()
        end_label = QLabel("End Time:")
        end_label.setStyleSheet("font-size: 12px; color: #6B7280;")
        end_time_layout.addWidget(end_label)
        self.work_end_time = QTimeEdit()
        self.work_end_time.setLocale(QLocale(QLocale.Language.English, QLocale.Country.Switzerland))
        self.work_end_time.setTime(QTime(17, 0))
        self.work_end_time.setDisplayFormat("hh:mm AP")
        self.work_end_time.setStyleSheet("""
            QTimeEdit {
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
                background: #FFFFFF;
            }
        """)
        end_time_layout.addWidget(self.work_end_time)
        hours_layout.addLayout(end_time_layout)
        hours_layout.addStretch(1)
        hours_group.addLayout(hours_layout)
        layout.addLayout(hours_group)
        
        # Optional Break Window
        break_group = QVBoxLayout()
        break_label = QLabel("Optional Break Window")
        break_label.setStyleSheet("font-size: 13px; font-weight: 500; color: #374151; margin-bottom: 8px;")
        break_group.addWidget(break_label)
        
        break_layout = QHBoxLayout()
        break_start_layout = QHBoxLayout()
        break_start_label = QLabel("Break Start:")
        break_start_label.setStyleSheet("font-size: 12px; color: #6B7280;")
        break_start_layout.addWidget(break_start_label)
        self.break_start_time = QTimeEdit()
        self.break_start_time.setLocale(QLocale(QLocale.Language.English, QLocale.Country.Switzerland))
        self.break_start_time.setTime(QTime(12, 0))
        self.break_start_time.setDisplayFormat("hh:mm AP")
        self.break_start_time.setStyleSheet("""
            QTimeEdit {
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
                background: #FFFFFF;
            }
        """)
        break_start_layout.addWidget(self.break_start_time)
        break_layout.addLayout(break_start_layout)
        
        break_layout.addSpacing(20)
        
        break_end_layout = QHBoxLayout()
        break_end_label = QLabel("Break End:")
        break_end_label.setStyleSheet("font-size: 12px; color: #6B7280;")
        break_end_layout.addWidget(break_end_label)
        self.break_end_time = QTimeEdit()
        self.break_end_time.setLocale(QLocale(QLocale.Language.English, QLocale.Country.Switzerland))
        self.break_end_time.setTime(QTime(13, 0))
        self.break_end_time.setDisplayFormat("hh:mm AP")
        self.break_end_time.setStyleSheet("""
            QTimeEdit {
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
                background: #FFFFFF;
            }
        """)
        break_end_layout.addWidget(self.break_end_time)
        break_layout.addLayout(break_end_layout)
        break_layout.addStretch(1)
        break_group.addLayout(break_layout)
        layout.addLayout(break_group)
        
        return card
    
    def _build_project_resources(self) -> QFrame:
        """Build Project Resources section"""
        card, layout = self._build_section_card("Project Resources")
        
        # ---------- First row: resources (machines & crews) ----------
        first_group = QGridLayout()
        first_group.setHorizontalSpacing(24)
        first_group.setVerticalSpacing(12)

        # Prefabrication Workbenches (Machines)
        machine_group = QVBoxLayout()
        machine_input_layout = QHBoxLayout()
        self.machine_count = QLineEdit("6")
        self.machine_count.setMaximumWidth(100)
        self.machine_count.setStyleSheet("""
            QLineEdit {
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
                background: #FFFFFF;
            }
        """)
        machine_input_layout.addWidget(self.machine_count)
        machine_input_layout.addStretch(1)
        machine_group.addLayout(machine_input_layout)
        
        machine_desc = QLabel("Number of machines available for prefabrication work")
        machine_desc.setStyleSheet("font-size: 12px; color: #6B7280; margin-top: 4px;")
        machine_group.addWidget(machine_desc)
        first_group.addLayout(machine_group, 0, 0)
        
        # Installation Crew Number
        crew_group = QVBoxLayout()
        crew_input_layout = QHBoxLayout()
        self.crew_count = QLineEdit("2")
        self.crew_count.setMaximumWidth(100)
        self.crew_count.setStyleSheet("""
            QLineEdit {
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
                background: #FFFFFF;
            }
        """)
        crew_input_layout.addWidget(self.crew_count)
        crew_input_layout.addStretch(1)
        crew_group.addLayout(crew_input_layout)
        
        crew_desc = QLabel("Number of crews available for onsite installation")
        crew_desc.setStyleSheet("font-size: 12px; color: #6B7280; margin-top: 4px;")
        crew_group.addWidget(crew_desc)
        first_group.addLayout(crew_group, 0, 1)

        layout.addLayout(first_group)
        layout.addSpacing(12)
        
        # ---------- Storage Capacities in grid ----------
        storage_grid = QGridLayout()
        storage_grid.setHorizontalSpacing(24)
        storage_grid.setVerticalSpacing(8)

        storage_label = QLabel("Storage Capacities")
        storage_label.setStyleSheet("font-size: 13px; font-weight: 500; color: #374151;")
        storage_grid.addWidget(storage_label, 0, 0, 1, 2)
        
        # Onsite storage
        onsite_layout = QHBoxLayout()
        site_label = QLabel("Onsite Storage:")
        site_label.setStyleSheet("font-size: 12px; color: #6B7280;")
        onsite_layout.addWidget(site_label)
        self.site_storage = QLineEdit("5")
        self.site_storage.setMaximumWidth(100)
        self.site_storage.setStyleSheet("""
            QLineEdit {
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
                background: #FFFFFF;
            }
        """)
        onsite_layout.addWidget(self.site_storage)
        onsite_layout.addStretch(1)
        storage_grid.addLayout(onsite_layout, 1, 0)

        # Factory storage
        factory_layout = QHBoxLayout()
        factory_label = QLabel("Factory Storage:")
        factory_label.setStyleSheet("font-size: 12px; color: #6B7280;")
        factory_layout.addWidget(factory_label)
        self.factory_storage = QLineEdit("5")
        self.factory_storage.setMaximumWidth(100)
        self.factory_storage.setStyleSheet("""
            QLineEdit {
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
                background: #FFFFFF;
            }
        """)
        factory_layout.addWidget(self.factory_storage)
        factory_layout.addStretch(1)
        storage_grid.addLayout(factory_layout, 1, 1)

        layout.addLayout(storage_grid)
        layout.addSpacing(12)
        
        # ---------- Cost Parameters in compact grid ----------
        cost_group = QGridLayout()
        cost_group.setHorizontalSpacing(24)
        cost_group.setVerticalSpacing(8)
        cost_label = QLabel("Cost Parameters")
        cost_label.setStyleSheet("font-size: 13px; font-weight: 500; color: #374151;")
        cost_group.addWidget(cost_label, 0, 0, 1, 2)
        
        cost_params = [
            ("Order Batch Cost (OC):", "order_cost", "0.1"),
            ("Penalty Cost per Unit Time (C_I):", "penalty_cost", "0.7"),
            ("Factory Inventory Cost (C_F):", "factory_inv_cost", "0.1"),
            ("Onsite Inventory Cost (C_O):", "onsite_inv_cost", "0.1"),
        ]
        
        self.cost_inputs = {}
        for idx, (label_text, key, default) in enumerate(cost_params):
            row = 1 + idx // 2
            col = idx % 2
            cost_input_layout = QHBoxLayout()
            cost_label_widget = QLabel(label_text)
            cost_label_widget.setStyleSheet("font-size: 12px; color: #6B7280;")
            cost_input_layout.addWidget(cost_label_widget)
            cost_input = QLineEdit(default)
            cost_input.setMaximumWidth(150)
            cost_input.setStyleSheet("""
                QLineEdit {
                    border: 1px solid #D1D5DB;
                    border-radius: 6px;
                    padding: 8px 12px;
                    font-size: 13px;
                    background: #FFFFFF;
                }
            """)
            self.cost_inputs[key] = cost_input
            cost_input_layout.addWidget(cost_input)
            cost_input_layout.addStretch(1)
            cost_group.addLayout(cost_input_layout, row, col)
        
        layout.addLayout(cost_group)
        
        return card
    
    def _save_settings(self):
        return {
            "start_datetime": self.start_datetime.text(),
            "target_datetime": self.target_datetime.text(),
            "working_days": self.get_working_days_map(),
            "work_start_time": self.work_start_time.text(),
            "work_end_time": self.work_end_time.text(),
            "break_start_time": self.break_start_time.text(),
            "break_end_time": self.break_end_time.text(),
            "machine_count": self.machine_count.text(),
            "crew_count": self.crew_count.text(),
            "site_storage": self.site_storage.text(),
            "factory_storage": self.factory_storage.text(),
            "order_cost": self.cost_inputs.get("order_cost").text() if "order_cost" in self.cost_inputs else "",
            "penalty_cost": self.cost_inputs.get("penalty_cost").text() if "penalty_cost" in self.cost_inputs else "",
            "factory_inv_cost": self.cost_inputs.get("factory_inv_cost").text() if "factory_inv_cost" in self.cost_inputs else "",
            "onsite_inv_cost": self.cost_inputs.get("onsite_inv_cost").text() if "onsite_inv_cost" in self.cost_inputs else "",
        }

    def get_working_days_map(self) -> dict[str, bool]:
        return {day: btn.isChecked() for day, btn in self.working_days.items()}


class ComparisonPage(QWidget):
    """Schedule comparison page with Gantt charts and metrics comparison"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        """Build the comparison page UI"""
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(16)

        # Left section: Gantt Chart Comparison
        left_section = self._build_gantt_section()
        main_layout.addWidget(left_section, 3)  # Takes 3/4 of the space

        # Right section: Metrics Comparison Sidebar
        right_section = self._build_metrics_section()
        main_layout.addWidget(right_section, 1)  # Takes 1/4 of the space

    def _build_gantt_section(self) -> QWidget:
        """Build the left section with Gantt chart comparison"""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # Title
        title = QLabel("Schedule Gantt Comparison")
        title.setStyleSheet("font-size: 24px; font-weight: 600; color: #111827;")
        layout.addWidget(title)

        # Version selection
        version_layout = QHBoxLayout()
        version_layout.setSpacing(16)

        upper_version_layout = QVBoxLayout()
        upper_version_label = QLabel("Upper Version:")
        upper_version_label.setStyleSheet("font-size: 13px; font-weight: 500; color: #374151;")
        self.upper_version_combo = QComboBox()
        self.upper_version_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
                background: #FFFFFF;
                min-width: 200px;
            }
        """)
        # Don't auto-trigger on combobox change, user will click Compare button
        upper_version_layout.addWidget(upper_version_label)
        upper_version_layout.addWidget(self.upper_version_combo)

        lower_version_layout = QVBoxLayout()
        lower_version_label = QLabel("Lower Version:")
        lower_version_label.setStyleSheet("font-size: 13px; font-weight: 500; color: #374151;")
        self.lower_version_combo = QComboBox()
        self.lower_version_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #D1D5DB;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
                background: #FFFFFF;
                min-width: 200px;
            }
        """)
        # Don't auto-trigger on combobox change, user will click Compare button
        lower_version_layout.addWidget(lower_version_label)
        lower_version_layout.addWidget(self.lower_version_combo)
        
        # Store engine and project_id for data loading
        self.engine = None
        self.project_id = None
        # Store version_id mapping (combo index -> version_id)
        self.version_id_map = {}
        # Reference to MainWindow (set by MainWindow)
        self.main_window = None

        version_layout.addLayout(upper_version_layout)
        version_layout.addStretch(1)
        version_layout.addLayout(lower_version_layout)
        
        # Compare button
        self.compare_btn = QPushButton("Compare")
        self.compare_btn.setStyleSheet("""
            QPushButton {
                background: #3B82F6;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 8px 24px;
                font-size: 13px;
                font-weight: 500;
                min-width: 100px;
            }
            QPushButton:hover {
                background: #2563EB;
            }
            QPushButton:pressed {
                background: #1D4ED8;
            }
            QPushButton:disabled {
                background: #D1D5DB;
                color: #9CA3AF;
            }
        """)
        self.compare_btn.clicked.connect(self._on_version_changed)
        version_layout.addWidget(self.compare_btn)
        
        layout.addLayout(version_layout)

        # Legend
        legend_layout = QHBoxLayout()
        legend_layout.setSpacing(16)
        
        legend_items = [
            ("Production", "#3B82F6"),        # Blue
            ("Factory Storage", "#9CA3AF"),   # Gray
            ("Transport", "#F59E0B"),         # Orange
            ("Site Storage", "#E5E7EB"),      # Light Gray
            ("Installation", "#10B981")       # Green
        ]
        
        for label, color in legend_items:
            legend_item = QHBoxLayout()
            legend_item.setSpacing(8)
            
            color_box = QLabel()
            color_box.setFixedSize(20, 20)
            color_box.setStyleSheet(f"background: {color}; border-radius: 4px;")
            
            legend_label = QLabel(label)
            legend_label.setStyleSheet("font-size: 12px; color: #374151;")
            
            legend_item.addWidget(color_box)
            legend_item.addWidget(legend_label)
            legend_layout.addLayout(legend_item)
        
        legend_layout.addStretch(1)
        layout.addLayout(legend_layout)

        # Gantt charts container (two separate charts)
        gantt_container = QFrame()
        gantt_container.setObjectName("GanttContainer")
        gantt_container.setStyleSheet("""
            QFrame#GanttContainer {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
            }
        """)
        gantt_layout = QVBoxLayout(gantt_container)
        gantt_layout.setContentsMargins(16, 16, 16, 16)
        gantt_layout.setSpacing(16)
        
        # Upper Gantt chart
        upper_gantt_frame = QFrame()
        upper_gantt_frame.setFrameShape(QFrame.Shape.Box)
        upper_gantt_frame.setStyleSheet("border: 1px solid #E5E7EB; border-radius: 4px;")
        upper_gantt_layout = QVBoxLayout(upper_gantt_frame)
        upper_gantt_layout.setContentsMargins(8, 8, 8, 8)
        upper_label = QLabel("Upper Version")
        upper_label.setStyleSheet("font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 4px;")
        upper_gantt_layout.addWidget(upper_label)
        self.upper_gantt_canvas = self._create_gantt_canvas()
        # Initialize with empty state
        self._draw_gantt_chart(self.upper_gantt_canvas, pd.DataFrame(), "Upper Version")
        upper_gantt_layout.addWidget(self.upper_gantt_canvas)
        gantt_layout.addWidget(upper_gantt_frame, 1)
        
        # Lower Gantt chart
        lower_gantt_frame = QFrame()
        lower_gantt_frame.setFrameShape(QFrame.Shape.Box)
        lower_gantt_frame.setStyleSheet("border: 1px solid #E5E7EB; border-radius: 4px;")
        lower_gantt_layout = QVBoxLayout(lower_gantt_frame)
        lower_gantt_layout.setContentsMargins(8, 8, 8, 8)
        lower_label = QLabel("Lower Version")
        lower_label.setStyleSheet("font-size: 14px; font-weight: 500; color: #374151; margin-bottom: 4px;")
        lower_gantt_layout.addWidget(lower_label)
        self.lower_gantt_canvas = self._create_gantt_canvas()
        # Initialize with empty state
        self._draw_gantt_chart(self.lower_gantt_canvas, pd.DataFrame(), "Lower Version")
        lower_gantt_layout.addWidget(self.lower_gantt_canvas)
        gantt_layout.addWidget(lower_gantt_frame, 1)
        
        layout.addWidget(gantt_container, 1)

        return container

    def _build_metrics_section(self) -> QWidget:
        """Build the right sidebar with metrics comparison"""
        container = QFrame()
        container.setObjectName("MetricsSidebar")
        container.setStyleSheet("""
            QFrame#MetricsSidebar {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 12px;
            }
        """)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        # Title
        title = QLabel("Metrics Comparison")
        title.setStyleSheet("font-size: 20px; font-weight: 600; color: #111827;")
        subtitle = QLabel("Comparing key metrics between selected versions")
        subtitle.setStyleSheet("font-size: 13px; color: #6B7280; margin-bottom: 8px;")
        
        title_layout = QVBoxLayout()
        title_layout.setSpacing(4)
        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        layout.addLayout(title_layout)

        # KPI Comparison Cards - store references for later updates
        metric_names = [
            "Construction Hours",
            "Factory Storage Module Hours",
            "Site Storage Module Hours",
            "Transport Bunch Number"
        ]
        
        self.metric_cards = {}  # Store metric cards by name for later updates
        for metric_name in metric_names:
            # Initialize with empty/default values
            metric = {
                "name": metric_name,
                "v1_value": "N/A",
                "v2_value": "N/A",
                "change": "N/A",
                "change_percent": "N/A",
                "trend": "neutral"
            }
            metric_card = self._create_metric_card(metric)
            self.metric_cards[metric_name] = metric_card
            layout.addWidget(metric_card)

        layout.addStretch(1)

        return container

    def _create_metric_card(self, metric: dict) -> QFrame:
        """Create a metric comparison card with updatable labels"""
        card = QFrame()
        card.setObjectName("MetricCard")
        card.setStyleSheet("""
            QFrame#MetricCard {
                background: #F9FAFB;
                border: 1px solid #E5E7EB;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Metric name
        name_label = QLabel(metric["name"])
        name_label.setStyleSheet("font-size: 13px; font-weight: 500; color: #374151;")
        layout.addWidget(name_label)

        # Values row
        values_layout = QHBoxLayout()
        values_layout.setSpacing(12)

        v1_layout = QVBoxLayout()
        v1_label = QLabel("Upper:")
        v1_label.setStyleSheet("font-size: 11px; color: #6B7280;")
        v1_value = QLabel(metric["v1_value"])
        v1_value.setStyleSheet("font-size: 14px; font-weight: 600; color: #111827;")
        v1_value.setObjectName("v1_value")  # Set object name for later access
        v1_layout.addWidget(v1_label)
        v1_layout.addWidget(v1_value)

        v2_layout = QVBoxLayout()
        v2_label = QLabel("Lower:")
        v2_label.setStyleSheet("font-size: 11px; color: #6B7280;")
        v2_value = QLabel(metric["v2_value"])
        v2_value.setStyleSheet("font-size: 14px; font-weight: 600; color: #111827;")
        v2_value.setObjectName("v2_value")  # Set object name for later access
        v2_layout.addWidget(v2_label)
        v2_layout.addWidget(v2_value)

        values_layout.addLayout(v1_layout)
        values_layout.addLayout(v2_layout)
        values_layout.addStretch(1)
        layout.addLayout(values_layout)

        # Change row
        change_layout = QHBoxLayout()
        change_label = QLabel(f"Change: {metric['change']} ({metric['change_percent']})")
        change_label.setStyleSheet("font-size: 12px; color: #DC2626; font-weight: 500;")
        change_label.setObjectName("change_label")  # Set object name for later access
        
        # Trend icon
        trend_icon = QLabel("↗")
        trend_icon.setStyleSheet("font-size: 14px; color: #DC2626;")
        trend_icon.setObjectName("trend_icon")  # Set object name for later access
        
        change_layout.addWidget(change_label)
        change_layout.addWidget(trend_icon)
        change_layout.addStretch(1)
        layout.addLayout(change_layout)

        # Store references to updatable widgets in the card
        card.v1_value_label = v1_value
        card.v2_value_label = v2_value
        card.change_label = change_label
        card.trend_icon = trend_icon

        return card
    
    def _calculate_metrics(self, solution_df: pd.DataFrame) -> dict:
        """
        Calculate metrics from solution dataframe.
        
        Returns a dictionary with:
        - construction_days: Total construction duration (days)
        - factory_storage_module_days: Sum of Factory_Wait_Duration
        - site_storage_module_days: Sum of Onsite_Wait_Duration  
        - transport_bunch_number: Number of unique Transport_Start times
        """
        if solution_df.empty:
            return {
                "construction_days": 0,
                "factory_storage_module_days": 0,
                "site_storage_module_days": 0,
                "transport_bunch_number": 0
            }
        
        # Construction Days: from earliest Production_Start to latest Installation_Finish
        prod_start_col = solution_df.get('Production_Start')
        inst_finish_col = solution_df.get('Installation_Finish')
        
        construction_days = 0
        if prod_start_col is not None and inst_finish_col is not None:
            valid_prod_starts = prod_start_col.dropna()
            valid_finishes = inst_finish_col.dropna()
            if len(valid_prod_starts) > 0 and len(valid_finishes) > 0:
                earliest_prod_start = float(valid_prod_starts.min())
                latest_finish = float(valid_finishes.max())
                construction_days = max(0, latest_finish - earliest_prod_start + 1)
                # Convert from time index to days (assuming 1 time index = 1 hour, 8 hours per day)
                #construction_days = construction_days / 8.0
        
        # Factory Storage Module Days: sum of Factory_Wait_Duration
        factory_wait_col = solution_df.get('Factory_Wait_Duration', pd.Series())
        factory_storage_module_days = 0
        if len(factory_wait_col) > 0:
            factory_storage_module_days = float(factory_wait_col.fillna(0).sum())  # Convert to days
            print(f"[DEBUG] Factory Storage Module Days calculation:")
            print(f"  - factory_wait_col length: {len(factory_wait_col)}")
            print(f"  - factory_wait_col values: {factory_wait_col.tolist()}")
            print(f"  - factory_wait_col sum (after fillna): {factory_wait_col.fillna(0).sum()}")
            print(f"  - factory_storage_module_days: {factory_storage_module_days}")
        
        # Site Storage Module Days: sum of Onsite_Wait_Duration
        onsite_wait_col = solution_df.get('Onsite_Wait_Duration', pd.Series())
        site_storage_module_days = 0
        if len(onsite_wait_col) > 0:
            site_storage_module_days = float(onsite_wait_col.fillna(0).sum()) # Convert to days
        
        # Transport Bunch Number: number of unique Transport_Start times
        transport_start_col = solution_df.get('Transport_Start')
        transport_bunch_number = 0
        if transport_start_col is not None:
            unique_transport_starts = transport_start_col.dropna().unique()
            transport_bunch_number = len(unique_transport_starts)
        
        return {
            "construction_days": round(construction_days, 1),
            "factory_storage_module_days": round(factory_storage_module_days, 1),
            "site_storage_module_days": round(site_storage_module_days, 1),
            "transport_bunch_number": transport_bunch_number
        }
    
    def _update_metric_card(self, card: QFrame, metric_name: str, v1_value: float, v2_value: float):
        """Update a metric card with calculated values"""
        # Format values based on metric type
        if "Hours" in metric_name:
            v1_str = f"{v1_value:.1f} hours" if v1_value > 0 else "0 hours"
            v2_str = f"{v2_value:.1f} hours" if v2_value > 0 else "0 hours"
        elif "Number" in metric_name:
            v1_str = f"{int(v1_value)} bunches" if v1_value > 0 else "0 bunches"
            v2_str = f"{int(v2_value)} bunches" if v2_value > 0 else "0 bunches"
        else:
            v1_str = str(v1_value) if v1_value > 0 else "0"
            v2_str = str(v2_value) if v2_value > 0 else "0"
        
        # Calculate change
        change = v1_value - v2_value
        if v2_value != 0:
            change_percent = abs(change / v2_value * 100)
        else:
            change_percent = 0 if change == 0 else 100
        
        # Determine trend and color
        if change > 0:
            trend = "up"
            color = "#DC2626"  # Red for increase (usually bad for most metrics)
        elif change < 0:
            trend = "down"
            color = "#10B981"  # Green for decrease (usually good for most metrics)
        else:
            trend = "neutral"
            color = "#6B7280"  # Gray for no change
        
        # Format change string
        change_str = f"{change:+.1f}" if isinstance(change, float) else f"{change:+d}"
        change_percent_str = f"{change_percent:.1f}%"
        
        # Update labels
        card.v1_value_label.setText(v1_str)
        card.v2_value_label.setText(v2_str)
        card.change_label.setText(f"Change: {change_str} ({change_percent_str})")
        card.change_label.setStyleSheet(f"font-size: 12px; color: {color}; font-weight: 500;")
        
        # Update trend icon
        if trend == "up":
            card.trend_icon.setText("↗")
        elif trend == "down":
            card.trend_icon.setText("↘")
        else:
            card.trend_icon.setText("→")
        card.trend_icon.setStyleSheet(f"font-size: 14px; color: {color};")
    
    def _create_gantt_canvas(self) -> FigureCanvas:
        """Create a matplotlib canvas for Gantt chart"""
        fig = Figure(figsize=(12, 6), facecolor='white')
        canvas = FigureCanvas(fig)
        canvas.setMinimumHeight(300)
        return canvas
    
    def _draw_gantt_chart(self, canvas: FigureCanvas, solution_df: pd.DataFrame, version_label: str = "",
                         settings: Optional[dict] = None,
                         project_start_datetime: Optional[str] = None):
        """
        Draw Gantt chart with 5 phases:
        1. Production (from Production_Start, duration Production_Duration)
        2. Factory Storage (from Production end to Transport_Start)
        3. Transport (from Transport_Start, duration Transport_Duration, to Arrival_Time)
        4. Site Storage (from Arrival_Time to Installation_Start)
        5. Installation (from Installation_Start, duration Installation_Duration, to Installation_Finish)
        
        Args:
            canvas: Matplotlib canvas
            solution_df: DataFrame with solution data (time indices)
            version_label: Label for the version
            settings: Settings dictionary for building working calendar
            project_start_datetime: Project start datetime string (format: "%m/%d/%Y")
        """
        if solution_df.empty:
            # Clear and show empty message
            canvas.figure.clear()
            ax = canvas.figure.add_subplot(111)
            ax.text(0.5, 0.5, 'No data available', ha='center', va='center', 
                   transform=ax.transAxes, fontsize=14, color='#9CA3AF')
            ax.axis('off')
            canvas.draw()
            canvas.update()
            return
        
        # Clear previous plot
        canvas.figure.clear()
        ax = canvas.figure.add_subplot(111)
        
        # Color mapping for phases
        colors = {
            'production': '#3B82F6',      # Blue
            'factory_storage': '#9CA3AF', # Gray
            'transport': '#F59E0B',       # Orange
            'site_storage': '#E5E7EB',    # Light Gray
            'installation': '#10B981'     # Green
        }
        
        # Sort by Production_Start (ascending - earliest first) to ensure proper order
        if 'Production_Start' in solution_df.columns:
            solution_df = solution_df.sort_values('Production_Start', ascending=True, na_position='last').reset_index(drop=True)
        
        num_modules = len(solution_df)
        
        if num_modules == 0:
            ax.text(0.5, 0.5, 'No modules to display', ha='center', va='center',
                   transform=ax.transAxes, fontsize=14, color='#9CA3AF')
            ax.axis('off')
            canvas.draw()
            canvas.update()
            return
        
        # Calculate y positions (one row per module)
        # Reverse the y positions so earliest fabrication starts appear at the top
        y_positions = np.arange(num_modules)[::-1]  # Reverse: [n-1, n-2, ..., 1, 0]
        
        # Find time range (time index mode)
        min_time_num = float('inf')
        max_time_num = float('-inf')
        
        for _, row in solution_df.iterrows():
            prod_start = row.get('Production_Start')
            if pd.notna(prod_start):
                min_time_num = min(min_time_num, float(prod_start))
            
            inst_finish = row.get('Installation_Finish')
            if pd.notna(inst_finish):
                max_time_num = max(max_time_num, float(inst_finish))
        
        if min_time_num == float('inf'):
            min_time_num = 0
        if max_time_num == float('-inf'):
            max_time_num = 100
        
        time_range = max_time_num - min_time_num
        if time_range == 0:
            time_range = 1
        padding = time_range * 0.1
        ax.set_xlim(min_time_num - padding, max_time_num + padding)
        
        # Build working calendar slots for time index to date conversion
        working_calendar_slots = None
        if settings and project_start_datetime and hasattr(self, 'main_window') and self.main_window:
            try:
                fmt = "%m/%d/%Y"
                start_date = datetime.strptime(project_start_datetime, fmt).date()
                # Determine max index needed based on actual data
                max_idx = max(int(max_time_num), 1000)  # Use at least 1000, or actual max if larger
                working_calendar_slots = self.main_window._build_working_calendar_slots(settings, start_date, max_idx)
            except Exception as e:
                print(f"Warning: Could not build working calendar slots: {e}")
        
        # Draw bars for each module
        bar_height = 0.6
        
        for idx, (_, row) in enumerate(solution_df.iterrows()):
            y_pos = y_positions[idx]
            module_id = str(row.get('Module_ID', ''))
            
            # Get time indices
            prod_start_val = row.get('Production_Start')
            prod_dur_val = row.get('Production_Duration', 0)
            transport_start_val = row.get('Transport_Start')
            arrival_time_val = row.get('Arrival_Time')
            install_start_val = row.get('Installation_Start')
            install_dur_val = row.get('Installation_Duration', 0)
            
            # Calculate finish times (time indices)
            prod_finish_idx = None
            if pd.notna(prod_start_val) and pd.notna(prod_dur_val) and prod_dur_val > 0:
                prod_finish_idx = int(prod_start_val) + int(prod_dur_val)
            
            install_finish_idx = None
            if pd.notna(install_start_val) and pd.notna(install_dur_val) and install_dur_val > 0:
                install_finish_idx = int(install_start_val) + int(install_dur_val)
            
            # Helper function to draw bar from start to end (time index mode)
            def draw_bar_from_to_num(start_num: Optional[float], end_num: Optional[float], color: str):
                """Draw a horizontal bar from start_num to end_num (time index)"""
                if start_num is not None and end_num is not None:
                    duration_num = end_num - start_num
                    if duration_num > 0:
                        ax.barh(y_pos, duration_num, left=start_num, height=bar_height,
                               color=color, edgecolor='white', linewidth=0.5)
            
            # Convert to numeric values (time index mode)
            prod_start_num = float(prod_start_val) if pd.notna(prod_start_val) else None
            prod_finish_num = float(prod_finish_idx) if prod_finish_idx else None
            transport_start_num = float(transport_start_val) if pd.notna(transport_start_val) else None
            arrival_time_num = float(arrival_time_val) if pd.notna(arrival_time_val) else None
            install_start_num = float(install_start_val) if pd.notna(install_start_val) else None
            install_finish_num = float(install_finish_idx) if install_finish_idx else None
            
            # Draw all bars using time index (directly from start to end)
            # 1. Production (from prod_start to prod_finish)
            draw_bar_from_to_num(prod_start_num, prod_finish_num, colors['production'])
            
            # 2. Factory Storage (from prod_finish to transport_start)
            draw_bar_from_to_num(prod_finish_num, transport_start_num, colors['factory_storage'])
            
            # 3. Transport (from transport_start to arrival_time)
            draw_bar_from_to_num(transport_start_num, arrival_time_num, colors['transport'])
            
            # 4. Site Storage (from arrival_time to install_start)
            draw_bar_from_to_num(arrival_time_num, install_start_num, colors['site_storage'])
            
            # 5. Installation (from install_start to install_finish)
            draw_bar_from_to_num(install_start_num, install_finish_num, colors['installation'])
        
        # Set y-axis labels with smaller font size for better readability
        # Labels should match the reversed order (earliest at top)
        ax.set_yticks(y_positions)
        module_labels = [str(row.get('Module_ID', '')) for _, row in solution_df.iterrows()]
        ax.set_yticklabels(module_labels)
        # Set y-axis limits to match reversed positions (top to bottom: highest to lowest y value)
        # y_positions is already reversed, so earliest (y=num_modules-1) appears at top
        ax.set_ylim(-0.5, num_modules - 0.5)
        
        # Add date annotations: project start and finish dates
        project_start_time_idx = 1  # Time index 1 corresponds to project start
        project_finish_time_idx = None
        
        # Find the maximum actual finish time index (project finish)
        # Note: Database stores Installation_Finish as (start + duration - 1), but 
        # we draw bars with end position as (start + duration), so we need to calculate
        # the actual visual end position to match what's drawn in the chart
        max_visual_finish = float('-inf')
        for _, row in solution_df.iterrows():
            install_start_val = row.get('Installation_Start')
            install_dur_val = row.get('Installation_Duration', 0)
            if pd.notna(install_start_val) and pd.notna(install_dur_val) and install_dur_val > 0:
                # Visual end position is start + duration (exclusive end, matches bar drawing)
                visual_finish = int(install_start_val) + int(install_dur_val)
                max_visual_finish = max(max_visual_finish, visual_finish)
        
        if max_visual_finish != float('-inf'):
            project_finish_time_idx = int(max_visual_finish)
        
        # Convert time indices to dates if working_calendar_slots is available
        project_start_date_str = None
        project_finish_date_str = None
        
        if working_calendar_slots and len(working_calendar_slots) > project_start_time_idx:
            try:
                start_dt = working_calendar_slots[project_start_time_idx]
                project_start_date_str = start_dt.strftime("%Y-%m-%d")
            except (IndexError, AttributeError):
                pass
        
        if working_calendar_slots and project_finish_time_idx and len(working_calendar_slots) > project_finish_time_idx:
            try:
                finish_dt = working_calendar_slots[project_finish_time_idx]
                if finish_dt is not None:
                    project_finish_date_str = finish_dt.strftime("%Y-%m-%d")
            except (IndexError, AttributeError, TypeError):
                pass
        
        # Draw vertical reference lines for start and finish
        ref_line_color = '#9CA3AF'
        ref_line_alpha = 0.5
        ref_line_style = '--'
        ref_line_width = 1.5
        
        # Draw start reference line
        if project_start_time_idx:
            ax.axvline(x=project_start_time_idx, color=ref_line_color, alpha=ref_line_alpha,
                      linestyle=ref_line_style, linewidth=ref_line_width, zorder=0)
        
        # Draw finish reference line
        if project_finish_time_idx:
            ax.axvline(x=project_finish_time_idx, color=ref_line_color, alpha=ref_line_alpha,
                      linestyle=ref_line_style, linewidth=ref_line_width, zorder=0)
        
        # Add date labels at the top of reference lines
        label_fontsize = 8
        label_color = '#6B7280'
        label_y_pos = num_modules - 0.3  # Position labels at the top
        
        if project_start_time_idx and project_start_date_str:
            ax.text(project_start_time_idx, label_y_pos, f'Start: {project_start_date_str}',
                   ha='center', va='bottom', fontsize=label_fontsize, color=label_color,
                   bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor=ref_line_color,
                            alpha=0.8, linewidth=0.5))
        
        if project_finish_time_idx and project_finish_date_str:
            ax.text(project_finish_time_idx, label_y_pos, f'Finish: {project_finish_date_str}',
                   ha='center', va='bottom', fontsize=label_fontsize, color=label_color,
                   bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor=ref_line_color,
                            alpha=0.8, linewidth=0.5))
        

        # Labels and styling
        ax.set_xlabel('Time Index', fontsize=11, color='#374151')
        ax.set_ylabel('Module ID', fontsize=11, color='#374151')
        if version_label:
            ax.set_title(version_label, fontsize=12, fontweight=500, color='#111827', pad=10)
        
        # Style the axes
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#E5E7EB')
        ax.spines['bottom'].set_color('#E5E7EB')
        # Use smaller font size for y-axis labels (module IDs) for better readability
        ax.tick_params(axis='x', colors='#6B7280', labelsize=10)
        ax.tick_params(axis='y', colors='#6B7280', labelsize=6)  # Smaller font for module IDs
        ax.grid(True, axis='x', linestyle='--', alpha=0.3, color='#D1D5DB')
        
        canvas.figure.tight_layout()
        canvas.draw()
        # Force update to ensure the canvas is refreshed
        canvas.update()
    
    def load_version_list(self, engine, project_id: int):
        """
        Load version list from database and populate comboboxes.
        This method should be called from MainWindow when ComparisonPage is shown.
        """
        from sqlalchemy import inspect, text
        from planning_tool.datamanager import ScheduleDataManager
        
        self.engine = engine
        self.project_id = project_id
        
        if project_id is None:
            self.upper_version_combo.clear()
            self.lower_version_combo.clear()
            return
        
        mgr = ScheduleDataManager(engine)
        versions_table = mgr.optimization_versions_table_name(project_id)
        
        inspector = inspect(engine)
        if versions_table not in inspector.get_table_names():
            self.upper_version_combo.clear()
            self.lower_version_combo.clear()
            return
        
        try:
            # Load versions from database
            query = f'SELECT version_id, version_number FROM "{versions_table}" ORDER BY version_id DESC'
            versions_df = pd.read_sql(text(query), engine)
            
            # Disconnect signals if they were connected (they shouldn't be, but just in case)
            try:
                self.upper_version_combo.currentIndexChanged.disconnect()
                self.lower_version_combo.currentIndexChanged.disconnect()
            except TypeError:
                pass  # Signals weren't connected, that's fine
            
            # Clear and populate comboboxes
            self.upper_version_combo.clear()
            self.lower_version_combo.clear()
            self.version_id_map = {}
            
            if not versions_df.empty:
                for _, row in versions_df.iterrows():
                    version_id = int(row['version_id'])
                    version_number = row['version_number']
                    display_text = f"Version {version_number}"
                    
                    index = self.upper_version_combo.count()
                    self.upper_version_combo.addItem(display_text)
                    self.lower_version_combo.addItem(display_text)
                    self.version_id_map[index] = version_id
                
                # Set default selections (latest version for upper, second latest for lower if available)
                if len(versions_df) >= 1:
                    self.upper_version_combo.setCurrentIndex(0)  # Latest version
                if len(versions_df) >= 2:
                    self.lower_version_combo.setCurrentIndex(1)  # Second latest version
                elif len(versions_df) >= 1:
                    self.lower_version_combo.setCurrentIndex(0)  # Same as upper if only one version
            
            # Don't auto-trigger chart loading, user will click Compare button
            # Charts will be empty until user clicks Compare button
            
        except Exception as e:
            print(f"Error loading version list: {e}")
            self.upper_version_combo.clear()
            self.lower_version_combo.clear()
    
    def _on_version_changed(self):
        """Handle Compare button click and update Gantt charts"""
        print(f"[DEBUG] Compare button clicked")
        print(f"[DEBUG] engine: {self.engine}, project_id: {self.project_id}")
        
        if self.engine is None or self.project_id is None:
            print("[DEBUG] Engine or project_id is None, returning")
            # Show empty charts
            self._draw_gantt_chart(self.upper_gantt_canvas, pd.DataFrame(), "Upper Version (No project selected)")
            self._draw_gantt_chart(self.lower_gantt_canvas, pd.DataFrame(), "Lower Version (No project selected)")
            return
        
        from sqlalchemy import inspect, text
        from planning_tool.datamanager import ScheduleDataManager
        
        mgr = ScheduleDataManager(self.engine)
        solution_table = mgr.solution_table_name(self.project_id)
        versions_table = mgr.optimization_versions_table_name(self.project_id)
        
        inspector = inspect(self.engine)
        if solution_table not in inspector.get_table_names():
            print(f"[DEBUG] Solution table {solution_table} does not exist")
            # Show empty charts with message
            self._draw_gantt_chart(self.upper_gantt_canvas, pd.DataFrame(), "Upper Version (No data)")
            self._draw_gantt_chart(self.lower_gantt_canvas, pd.DataFrame(), "Lower Version (No data)")
            return
        
        try:
            # Get selected version IDs
            upper_index = self.upper_version_combo.currentIndex()
            lower_index = self.lower_version_combo.currentIndex()
            
            print(f"[DEBUG] Selected indices: upper={upper_index}, lower={lower_index}")
            print(f"[DEBUG] version_id_map: {self.version_id_map}")
            
            upper_version_id = self.version_id_map.get(upper_index)
            lower_version_id = self.version_id_map.get(lower_index)
            
            print(f"[DEBUG] Version IDs: upper={upper_version_id}, lower={lower_version_id}")
            
            
            # Check which version_ids actually have data in solution table
            available_version_ids = set()
            try:
                check_query = f'SELECT DISTINCT version_id FROM "{solution_table}" WHERE version_id IS NOT NULL'
                available_df = pd.read_sql(text(check_query), self.engine)
                available_version_ids = set(available_df['version_id'].dropna().astype(int).tolist())
                print(f"[DEBUG] Available version_ids in solution table: {available_version_ids}")
            except Exception as e:
                print(f"[DEBUG] Error checking available versions: {e}")
            
            # Get settings and project_start_datetime for time conversion
            # Try to get settings from main_window if available
            settings = {}
            if hasattr(self, 'main_window') and self.main_window:
                settings = self.main_window._get_active_settings() or {}
            
            # Helper function to load version data and get project_start_datetime
            def load_version_data(version_id, available_ids, table_name, versions_table_name):
                df = pd.DataFrame()
                label = "Version"
                start_datetime = None
                
                if version_id is not None:
                    if version_id in available_ids:
                        # Load data for the specific version_id, sorted by Production_Start ASC (earliest first)
                        query = f'SELECT * FROM "{table_name}" WHERE version_id = :version_id ORDER BY Production_Start ASC'
                        df = pd.read_sql(text(query), self.engine, params={"version_id": version_id})
                        # Get version label and project_start_datetime
                        if versions_table_name in inspector.get_table_names():
                            v_query = f'SELECT version_number, project_start_datetime FROM "{versions_table_name}" WHERE version_id = :version_id'
                            v_result = pd.read_sql(text(v_query), self.engine, params={"version_id": version_id})
                            if not v_result.empty:
                                label = f"Version {v_result.iloc[0]['version_number']}"
                                if pd.notna(v_result.iloc[0]['project_start_datetime']):
                                    start_datetime = v_result.iloc[0]['project_start_datetime']
                    else:
                        label = f"Version {version_id} (No data)"
                
                return df, label, start_datetime
            
            # Load upper version data
            upper_df, upper_label, upper_start_datetime = load_version_data(
                upper_version_id, available_version_ids, solution_table, versions_table)
            
            if not upper_label or upper_label == "Version":
                if self.upper_version_combo.count() > 0:
                    upper_label = "Upper Version (Please select)"
                else:
                    upper_label = "Upper Version"
            
            # Load lower version data
            lower_df, lower_label, lower_start_datetime = load_version_data(
                lower_version_id, available_version_ids, solution_table, versions_table)
            
            if not lower_label or lower_label == "Version":
                if self.lower_version_combo.count() > 0:
                    lower_label = "Lower Version (Please select)"
                else:
                    lower_label = "Lower Version"
            
            # Determine project_start_datetime (use upper version's if available, otherwise lower's)
            project_start_datetime = upper_start_datetime or lower_start_datetime
            
            # Build working calendar slots if we have settings and start date
            # We'll build it dynamically in _draw_gantt_chart based on actual data max index
            
            # Calculate metrics for both versions
            upper_metrics = self._calculate_metrics(upper_df)
            lower_metrics = self._calculate_metrics(lower_df)
            
            # Update metric cards
            if hasattr(self, 'metric_cards'):
                self._update_metric_card(
                    self.metric_cards["Construction Hours"],
                    "Construction Hours",
                    upper_metrics["construction_days"],
                    lower_metrics["construction_days"]
                )
                self._update_metric_card(
                    self.metric_cards["Factory Storage Module Hours"],
                    "Factory Storage Module Hours",
                    upper_metrics["factory_storage_module_days"],
                    lower_metrics["factory_storage_module_days"]
                )
                self._update_metric_card(
                    self.metric_cards["Site Storage Module Hours"],
                    "Site Storage Module Hours",
                    upper_metrics["site_storage_module_days"],
                    lower_metrics["site_storage_module_days"]
                )
                self._update_metric_card(
                    self.metric_cards["Transport Bunch Number"],
                    "Transport Bunch Number",
                    upper_metrics["transport_bunch_number"],
                    lower_metrics["transport_bunch_number"]
                )
            
            # Draw charts with date annotations
            print(f"[DEBUG] Drawing charts...")
            self._draw_gantt_chart(self.upper_gantt_canvas, upper_df, upper_label,
                                  settings=settings,
                                  project_start_datetime=upper_start_datetime or project_start_datetime)
            self._draw_gantt_chart(self.lower_gantt_canvas, lower_df, lower_label,
                                  settings=settings,
                                  project_start_datetime=lower_start_datetime or project_start_datetime)
            # Force UI update
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()
            print(f"[DEBUG] Charts drawn")
            
        except Exception as e:
            print(f"Error loading Gantt data: {e}")
            import traceback
            traceback.print_exc()
            # Draw empty charts on error
            self._draw_gantt_chart(self.upper_gantt_canvas, pd.DataFrame(), "Upper Version")
            self._draw_gantt_chart(self.lower_gantt_canvas, pd.DataFrame(), "Lower Version")

