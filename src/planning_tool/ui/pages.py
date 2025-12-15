"""
Page Components

This module contains all main page widgets for the application:
- DashboardPage: Main dashboard with KPIs and tables
- SchedulePage: Module schedule display and management
- UploadPage: File upload interface
- SettingsPage: Project settings configuration
"""
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


class DashboardPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # 4 KPI cards
        cards = QGridLayout()
        cards.setHorizontalSpacing(12)
        cards.setVerticalSpacing(12)

        cards.addWidget(KpiCard("Planned vs Actual", "92%", "On schedule", "↗ +3%"), 0, 0)
        cards.addWidget(KpiCard("Critical Tasks", "12", "Requiring attention", "↘ -2"), 0, 1)
        cards.addWidget(KpiCard("Delay Days", "23", "Total across project", "↗ +5"), 0, 2)
        cards.addWidget(KpiCard("Forecast Completion", "Dec 15, 2025", "3 days behind baseline", "↘ -3"), 1, 0)
        cards.addWidget(KpiCard("Open Issues", "8", "Awaiting resolution", "↘ -3"), 1, 1)
        cards.addItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred), 1, 2)

        table = DashboardTable()

        lay = QVBoxLayout(self)
        lay.addLayout(cards)
        lay.addWidget(table)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)


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
            ("Order Batch Cost (OC):", "order_cost", "0.5"),
            ("Penalty Cost per Unit Time (C_I):", "penalty_cost", "1"),
            ("Factory Inventory Cost (C_F):", "factory_inv_cost", "0.2"),
            ("Onsite Inventory Cost (C_O):", "onsite_inv_cost", "0.2"),
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

