"""
Application-Specific UI Components

This module contains UI components specific to this application,
such as TopBar, Sidebar, DashboardTable, and StatusCell.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QColor, QFont
from PyQt6.QtWidgets import (
    QFrame, QLabel, QPushButton, QComboBox, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QWidget, QHBoxLayout, QVBoxLayout,
    QButtonGroup, QSizePolicy
)
from pathlib import Path
from .widgets import SidebarButton, AspectRatioPixmapLabel


class TopBar(QFrame):
    """Top navigation bar component"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TopBar")

        title = QLabel("Dynamical Construction Planning Tool")
        title.setObjectName("appTitle")

        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(300)

        self.delete_project_btn = QPushButton("Delete Project")
        self.delete_project_btn.setToolTip("Delete current project (This action cannot be undone)")
        self.delete_project_btn.setObjectName("DeleteProjectBtn")
        self.delete_project_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_project_btn.setMinimumHeight(36)
        self.delete_project_btn.setMinimumWidth(140)
        self.delete_project_btn.setStyleSheet("""
            QPushButton#DeleteProjectBtn {
                background: #FFFFFF;
                color: #5585b5;
                border: 1.5px solid #5585b5;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: 500;
                font-size: 13px;
            }
            QPushButton#DeleteProjectBtn:hover {
                background: #FEF2F2;
                border-color: #EF4444;
                color: #B91C1C;
            }
            QPushButton#DeleteProjectBtn:pressed {
                background: #FEE2E2;
                border-color: #5585b5;
                color: #5585b5;
            }
        """)
        self.delete_project_btn.hide()

        search = QLineEdit()
        search.setPlaceholderText("Search tasks, resources…")
        search.setClearButtonEnabled(True)
        search.setMinimumWidth(420)

        left = QHBoxLayout()
        left.addWidget(title)
        left.addSpacing(40)
        left.addWidget(self.project_combo)
        left.addWidget(self.delete_project_btn)

        lay = QHBoxLayout(self)
        lay.addLayout(left)
        lay.addStretch(1)
        lay.addWidget(search)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)


class Sidebar(QFrame):
    """Sidebar navigation component"""
    pageRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")

        APP_DIR = Path(__file__).resolve().parent.parent
        pix_path = APP_DIR / "logo.png"

        logo = AspectRatioPixmapLabel()
        pm = QPixmap(str(pix_path))
        logo.setPixmap(pm)
        logo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        logo.setMaximumHeight(80)

        self.btn_dash = SidebarButton("Dashboard")
        self.btn_sched = SidebarButton("Schedule")
        self.btn_comparison = SidebarButton("Comparison")
        self.btn_upload = SidebarButton("Upload Data")
        self.btn_settings = SidebarButton("Settings")
        self.btn_dash.setChecked(True)

        group = QButtonGroup(self)
        group.setExclusive(True)
        for b in (self.btn_dash, self.btn_sched, self.btn_comparison, self.btn_upload, self.btn_settings):
            b.setCheckable(True)
            group.addButton(b)

        self.btn_dash.clicked.connect(lambda: self.pageRequested.emit("dashboard"))
        self.btn_sched.clicked.connect(lambda: self.pageRequested.emit("schedule"))
        self.btn_comparison.clicked.connect(lambda: self.pageRequested.emit("comparison"))
        self.btn_upload.clicked.connect(lambda: self.pageRequested.emit("upload"))
        self.btn_settings.clicked.connect(lambda: self.pageRequested.emit("settings"))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(24)
        lay.addWidget(logo)
        lay.addSpacing(16)
        lay.addWidget(self.btn_dash)
        lay.addWidget(self.btn_sched)
        lay.addWidget(self.btn_comparison)
        lay.addWidget(self.btn_upload)
        lay.addWidget(self.btn_settings)
        lay.addStretch(1)


class DashboardTable(QFrame):
    """Dashboard table component"""
    pageRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TableCard")
        self.setStyleSheet("""
            QFrame#TableCard {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 12px;
            }
            QLabel#sectionTitle {
                font-size: 18px;
                font-weight: 600;
                color: #111827;
            }
            QLabel#sectionSubtitle {
                font-size: 13px;
                color: #6B7280;
            }
            QPushButton#primaryBtn {
                background: #111827;
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton#primaryBtn:hover {
                background: #374151;
            }
            QTableWidget {
                gridline-color: #E5E7EB;
                border: none;
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
            QHeaderView::section:last {
                border-right: none;
            }
            QTableWidget::item {
                border-right: 1px solid #E5E7EB;
                border-bottom: 1px solid #E5E7EB;
                padding: 8px;
            }
        """)

        self.title = QLabel("What Start to Fabricate Today")
        self.title.setObjectName("sectionTitle")
        self.subtitle = QLabel("Modules scheduled to start fabrication today")
        self.subtitle.setObjectName("sectionSubtitle")

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Module ID", "Fabrication Start Time", "Fabrication Duration (h)", "Production Start Index"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(True)
        self.table.setMinimumHeight(200)

        btn = QPushButton("Go to Schedule")
        btn.clicked.connect(lambda: self.pageRequested.emit("schedule"))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(36)
        btn.setObjectName("primaryBtn")

        top = QHBoxLayout()
        txt = QVBoxLayout()
        txt.setSpacing(4)
        txt.addWidget(self.title)
        txt.addWidget(self.subtitle)
        top.addLayout(txt)
        top.addStretch(1)
        top.addWidget(btn)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.table)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
    
    def load_tomorrow_fabrication_modules(self, data: list):
        """
        Load modules that start fabrication tomorrow into the table.
        
        Args:
            data: List of tuples/dicts with (module_id, start_datetime_str, duration, production_start_index)
                  or list of dicts with keys: Module_ID, Fabrication_Start_Time, Production_Duration, Production_Start
        """
        self.table.setRowCount(0)  # Clear existing rows
        
        if not data:
            # Show empty state message
            self.table.setRowCount(1)
            item = QTableWidgetItem("No modules scheduled for today")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setFlags(Qt.ItemFlag.NoItemFlags)  # Make it non-selectable
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, 4)  # Span across all columns
            return
        
        self.table.setRowCount(len(data))
        
        for r, row_data in enumerate(data):
            # Handle both tuple and dict formats
            if isinstance(row_data, dict):
                module_id = str(row_data.get("Module_ID", ""))
                start_time = str(row_data.get("Fabrication_Start_Time", ""))
                duration = str(row_data.get("Production_Duration", ""))
                prod_start_idx = str(row_data.get("Production_Start", ""))
            else:
                # Tuple format: (module_id, start_time, duration, prod_start_idx)
                module_id = str(row_data[0]) if len(row_data) > 0 else ""
                start_time = str(row_data[1]) if len(row_data) > 1 else ""
                duration = str(row_data[2]) if len(row_data) > 2 else ""
                prod_start_idx = str(row_data[3]) if len(row_data) > 3 else ""
            
            # Module ID
            item_id = QTableWidgetItem(module_id)
            item_id.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self.table.setItem(r, 0, item_id)
            
            # Fabrication Start Time
            item_time = QTableWidgetItem(start_time)
            item_time.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, 1, item_time)
            
            # Fabrication Duration
            item_dur = QTableWidgetItem(duration)
            item_dur.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, 2, item_dur)
            
            # Production Start Index (hidden column, for reference - hide this column from display)
            item_idx = QTableWidgetItem(prod_start_idx)
            item_idx.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, 3, item_idx)
        
        # Hide the Production Start Index column (column 3) as it's for internal reference only
        self.table.setColumnHidden(3, True)


class StatusCell(QWidget):
    """Status cell with colored background for Module Schedule"""
    def __init__(self, status: str):
        super().__init__()
        status_colors = {
            "Completed": ("#D1FAE5", "#065F46"),
            "In Progress": ("#DBEAFE", "#1E40AF"),
            "Delayed": ("#FEE2E2", "#991B1B"),
            "Upcoming": ("#F3F4F6", "#374151"),
        }
        bg, fg = status_colors.get(status, ("#F3F4F6", "#374151"))
        h = QHBoxLayout(self)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(0)
        label = QLabel(status)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(f"""
            QLabel {{
                background: {bg};
                color: {fg};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
                font-weight: 500;
            }}
        """)
        h.addWidget(label)

