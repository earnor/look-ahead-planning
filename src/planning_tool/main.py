from PyQt6.QtCore import Qt, QSize, pyqtSignal, QRect
from PyQt6.QtGui import QFont, QPixmap, QDragEnterEvent, QDropEvent, QMouseEvent, QPainter, QColor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QLineEdit, QComboBox,
    QHBoxLayout, QVBoxLayout, QGridLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QSizePolicy, QSpacerItem, QButtonGroup, QStackedWidget, QFileDialog, QMessageBox, QProgressBar,
    QSplitter, QCheckBox, QGroupBox, QScrollArea, QInputDialog, QDateTimeEdit, QTimeEdit
)
from PyQt6.QtCore import QDateTime, QTime, QDate, QLocale
from pathlib import Path
import sys
import pandas as pd
from sqlalchemy import create_engine, text
from planning_tool.datamanager import ScheduleDataManager
from planning_tool.model import PrefabScheduler, estimate_time_horizon
from datetime import datetime, time, timedelta


class SidebarButton(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor) # for better UX on hover
        self.setCheckable(True) # toggle state
        self.setMinimumHeight(40)
        self.setProperty("sidebar", True)


class KpiCard(QFrame):
    def __init__(self, title: str, value: str, subtitle: str = "", trend: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("KpiCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        title_lbl = QLabel(title) # show the title
        title_lbl.setObjectName("kpiTitle")

        value_lbl = QLabel(value)
        value_lbl.setObjectName("kpiValue")

        sub_lbl = QLabel(subtitle)
        sub_lbl.setObjectName("kpiSubtitle")

        trend_lbl = QLabel(trend)
        trend_lbl.setObjectName("kpiTrend") # we will all these later
        trend_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        top = QHBoxLayout()
        top.addWidget(title_lbl)
        top.addStretch(1)
        top.addWidget(trend_lbl)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(value_lbl)
        lay.addWidget(sub_lbl)
        lay.addStretch(1)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)


class TopBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TopBar")
        #self.setFixedHeight(56)

        #brand = QLabel("ETH Zurich")
        #brand.setObjectName("brandBadge")
        #brand.setAlignment(Qt.AlignmentFlag.AlignCenter)


        title = QLabel("Dynamical Construction Planning Tool")
        title.setObjectName("appTitle")

        self.project_combo = QComboBox()
        # No initial items - projects will be added when created
        self.project_combo.setMinimumWidth(300)
        
        # Delete project button (only visible when a project is selected)
        self.delete_project_btn = QPushButton("Delete Project")
        self.delete_project_btn.setToolTip("Delete current project (This action cannot be undone)")
        self.delete_project_btn.setObjectName("DeleteProjectBtn")
        self.delete_project_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_project_btn.setMinimumHeight(36)
        self.delete_project_btn.setMinimumWidth(140)
        # Apply danger/destructive button styling
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
        self.delete_project_btn.hide()  # Hidden by default, shown when project selected

        search = QLineEdit()
        search.setPlaceholderText("Search tasks, resources…")
        search.setClearButtonEnabled(True)
        search.setMinimumWidth(420)

        left = QHBoxLayout()
        #left.addWidget(brand)
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


class DashboardTable(QFrame):
    pageRequested = pyqtSignal(str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TableCard")

        title = QLabel("What’s Late This Week")
        title.setObjectName("sectionTitle")
        subtitle = QLabel("Tasks behind schedule or at risk")
        subtitle.setObjectName("sectionSubtitle")

        table = QTableWidget(4, 6)
        table.setHorizontalHeaderLabels(["ID", "Task", "Trade", "Planned", "Actual", "Δ Days"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setMinimumHeight(220)

        data = [
            ("T-401", "Install HVAC System - Level 3", "Mechanical", "Oct 5", "Oct 12", "+7"),
            ("T-205", "Electrical Rough-in - Level 2", "Electrical", "Oct 8", "Oct 13", "+5"),
            ("T-312", "Drywall Installation - Level 3", "Finishes", "Oct 10", "—", "+3"),
            ("T-156", "Plumbing Fixtures - Level 1", "Plumbing", "Oct 6", "Oct 10", "+4"),
        ]
        for r, row in enumerate(data):
            for c, val in enumerate(row):
                item = QTableWidgetItem(val)
                if c in (0, 2, 3, 4, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
                table.setItem(r, c, item)

        btn = QPushButton("Go to Schedule")
        # if press btn, page will jump to schedule page
        btn.clicked.connect(  lambda: self.pageRequested.emit("schedule"))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(36)
        btn.setObjectName("primaryBtn")

        top = QHBoxLayout()
        txt = QVBoxLayout()
        txt.addWidget(title)
        txt.addWidget(subtitle)
        top.addLayout(txt)
        top.addStretch(1)
        top.addWidget(btn)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(table)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

class AspectRatioPixmapLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._orig = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setScaledContents(False)

    def setPixmap(self, pm: QPixmap) -> None:
        self._orig = pm
        super().setPixmap(pm)   
        self._rescale()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._rescale()

    def _rescale(self):
        if not self._orig or self.width() <= 0 or self.height() <= 0:
            return
        scaled = self._orig.scaled(
            self.size(),                     
            Qt.AspectRatioMode.KeepAspectRatio, 
            Qt.TransformationMode.SmoothTransformation
        )
        super().setPixmap(scaled)

class SidebarButton(QPushButton):
    def __init__(self, text):
        super().__init__(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(36)

class Sidebar(QFrame):
    pageRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")

        APP_DIR = Path(__file__).resolve().parent
        pix_path = APP_DIR / "logo.png"

        logo = AspectRatioPixmapLabel()
        pm = QPixmap(str(pix_path))
        logo.setPixmap(pm)
        logo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        logo.setMaximumHeight(80)

        self.btn_dash    = SidebarButton("Dashboard")
        self.btn_sched   = SidebarButton("Schedule")
        self.btn_upload  = SidebarButton("Upload Data")
        self.btn_settings = SidebarButton("Settings")
        self.btn_dash.setChecked(True)

        group = QButtonGroup(self)
        group.setExclusive(True)
        for b in (self.btn_dash, self.btn_sched, self.btn_upload, self.btn_settings):
            b.setCheckable(True)
            group.addButton(b)

        self.btn_dash.clicked.connect(   lambda: self.pageRequested.emit("dashboard"))
        self.btn_sched.clicked.connect(  lambda: self.pageRequested.emit("schedule"))
        self.btn_upload.clicked.connect( lambda: self.pageRequested.emit("upload"))
        self.btn_settings.clicked.connect(lambda: self.pageRequested.emit("settings"))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(24)
        lay.addWidget(logo)
        lay.addSpacing(16)
        lay.addWidget(self.btn_dash)
        lay.addWidget(self.btn_sched)
        lay.addWidget(self.btn_upload)
        lay.addWidget(self.btn_settings)
        lay.addStretch(1)


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

# file_drop_area.py

class FileDropArea(QFrame):
    fileSelected = pyqtSignal(str) 

    def __init__(self, title: str, exts: list[str], parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._exts = [e.lower() for e in exts]  # like ['.xlsx','.xls','.csv']
        self.setObjectName("DropArea")

        self._label = QLabel(
            f"""<div style="text-align:center;">
                <div style="font-size:28px; line-height:1.2;">⬆</div>
                <div><b>Click to upload</b><br/>or drag and drop</div>
                <div style="margin-top:6px; color:#6b7280; font-size:12px;">
                    Supported formats: {", ".join([e.lstrip(".").upper() for e in self._exts])}
                </div>
            </div>"""
        )
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.addWidget(self._label)

        # card-like style
        self.setStyleSheet("""
            QFrame#DropArea {
                border: 1px dashed #D1D5DB;
                border-radius: 10px;
                background: #FAFAFA;
            }
            QFrame#DropArea:hover {
                background: #F5F7FF;
                border-color: #A5B4FC;
            }
        """)

    # click to upload
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._open_dialog()

    def _open_dialog(self):
        filt = "Files (" + " ".join(f"*{e}" for e in self._exts) + ")"
        paths, _ = QFileDialog.getOpenFileNames(self, "Select files", "", filt)
        if paths:
            self._emit_one(paths[0])  # Emit first selected file 

    # drag & drop
    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        for u in e.mimeData().urls():
            if u.isLocalFile():
                p = u.toLocalFile()
                if p and Path(p).suffix.lower() in self._exts:
                    self._emit_one(p)           
                    return

    def _emit_one(self, path: str):
        self.fileSelected.emit(path)

class Chip(QLabel):
    def __init__(self, text: str, kind="default"):
        super().__init__(text)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMargin(6)
        color = "#E5E7EB" if kind=="default" else "#DBEAFE"
        self.setStyleSheet(f"""
            QLabel {{
              background: {color};
              border-radius: 8px;
              padding: 2px 8px;
              color: #374151;
              font-size: 12px;
            }}
        """)

class Card(QFrame):
    def __init__(self, title: str, trailing_widget: QWidget|None=None):
        super().__init__()
        self.setObjectName("Card")
        self.setStyleSheet("""
            QFrame#Card {
                border: 1px solid #E5E7EB;
                border-radius: 12px;
                background: #FFFFFF;
            }
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(40)

        # header
        hdr = QHBoxLayout()
        title_lbl = QLabel(f"<b style='font-size:30px;'>{title}</b>")
        title_lbl.setTextFormat(Qt.TextFormat.RichText)
        hdr.addWidget(title_lbl)
        hdr.addStretch(1)
        if trailing_widget:
            hdr.addWidget(trailing_widget, 0, Qt.AlignmentFlag.AlignRight)
        lay.addLayout(hdr)

        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        lay.addLayout(self.body)



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
        drop1.fileSelected.connect(self.on_create_project_from_csv) # we will connect this to our sql db later

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
        
# ---------- Helpers ----------
def pill_label(text: str, bg: str, fg: str = "#0d0d0d") -> QLabel:
    lab = QLabel(text)
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lab.setStyleSheet(f"""
        QLabel {{
            background: {bg};
            color: {fg};
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 12px;
        }}
    """)
    return lab

"""
def risk_badge(level: str) -> QLabel:
    color = {"Low": "#34c759", "Medium": "#ff9f0a", "High": "#ff3b30"}.get(level, "#999")
    return pill_label(level, "transparent", color)
"""
class ProgressBarCell(QWidget):
    def __init__(self, percent: int):
        super().__init__()
        layout = QHBoxLayout(self); layout.setContentsMargins(0, 8, 0, 8)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(percent)
        bar.setFormat(f"{percent}%")
        bar.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar.setStyleSheet("""
            QProgressBar {
                background: #f1f3f5; border-radius: 6px; text-align: right; padding-right: 6px; height: 10px;
                color: #0d0d0d; font-size: 11px;
            }
            QProgressBar::chunk { background: #0ea5e9; border-radius: 6px; }
        """)
        layout.addWidget(bar)


class TagCell(QWidget):
    def __init__(self, text: str, bg="#eef2ff", fg="#1e40af"):
        super().__init__()
        h = QHBoxLayout(self); h.setContentsMargins(0, 6, 0, 6)
        h.addStretch(1)
        h.addWidget(pill_label(text, bg, fg))
        h.addStretch(1)

class StatusCell(QWidget):
    """Status cell with colored background for Module Schedule"""
    def __init__(self, status: str):
        super().__init__()
        status_colors = {
            "Completed": ("#D1FAE5", "#065F46"),  # Light green background, dark green text
            "In Progress": ("#DBEAFE", "#1E40AF"),  # Light blue background, dark blue text
            "Delayed": ("#FEE2E2", "#991B1B"),  # Light red background, dark red text
            "Upcoming": ("#F3F4F6", "#374151"),  # Light gray background, dark gray text
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

# ---------- Main Window ----------
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
                #c= 1 - c
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

        # store for later population
        self.table = table
        return table

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
            "start_datetime": self.start_datetime.text(), # return "2025-12-02 10:30" text link that
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

class MainWindow(QMainWindow):
    def __init__(self, engine=None, parent=None):
        super().__init__()
        self.setWindowTitle("ConstructPlan - PyQt6")
        self.resize(1280, 760)
        if engine is None:
            engine = create_engine("sqlite:///scheduler.db", echo=False, future=True)
        self.engine = engine
        self.mgr = ScheduleDataManager(engine)

        self.sidebar = Sidebar()
        self.sidebar.pageRequested.connect(self.switch_page)

        self.dashboardtable = DashboardTable()
        self.dashboardtable.pageRequested.connect(self.switch_page)

        self.topbar = TopBar()
        # Connect delete button signal
        self.topbar.delete_project_btn.clicked.connect(self._on_delete_project_clicked)
        self.project_lookup: dict[str, int] = {}
        self.current_project_id: int | None = None
        self._populate_project_combo()
        self.topbar.project_combo.currentTextChanged.connect(self._on_project_selected)

        self.stack = QStackedWidget()

        try:
            page_dashboard = DashboardPage()
        except NameError:
            page_dashboard = QLabel("Dashboard"); page_dashboard.setAlignment(Qt.AlignmentFlag.AlignCenter)

        try:
            page_schedule = SchedulePage()
        except NameError:
            page_schedule = QLabel("Schedule"); page_schedule.setAlignment(Qt.AlignmentFlag.AlignCenter)

        try:
            page_upload = UploadPage(engine=self.engine)
            # Connect signal to update project combo in topbar
            page_upload.projectCreated.connect(self._on_project_created)
        except NameError:
            page_upload = QLabel("Upload"); page_upload.setAlignment(Qt.AlignmentFlag.AlignCenter)

        try:
            page_settings = SettingsPage()
        except NameError:
            page_settings = QLabel("Settings"); page_settings.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.page_index = {
            "dashboard": self.stack.addWidget(page_dashboard),
            "schedule":  self.stack.addWidget(page_schedule),
            "upload":    self.stack.addWidget(page_upload),
            "settings":  self.stack.addWidget(page_settings),
        }
        self.stack.setCurrentIndex(self.page_index["dashboard"])

        # wire calculate button (SchedulePage) -> MainWindow handler
        if isinstance(page_schedule, SchedulePage):
            self.page_schedule = page_schedule
            page_schedule.btn_calculate.clicked.connect(self.on_calculate_clicked)
            page_schedule.btn_export.clicked.connect(self.on_export_schedule)

        central = QWidget()
        central_lay = QVBoxLayout(central)
        central_lay.setContentsMargins(0, 0, 0, 0)
        central_lay.setSpacing(12)
        central_lay.addWidget(self.topbar)
        central_lay.addWidget(self.stack, 1)

        root = QWidget()
        root_lay = QHBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)
        root_lay.addWidget(self.sidebar, 1)
        root_lay.addWidget(central, 6)

        self.setCentralWidget(root)

    def _get_active_settings(self) -> dict | None:
        """
        Helper to fetch current settings from SettingsPage.
        Returns a dict compatible with SettingsPage._save_settings or None.
        """
        idx = self.page_index.get("settings")
        if idx is None:
            return None
        widget = self.stack.widget(idx)
        if isinstance(widget, SettingsPage):
            return widget._save_settings()
        return None

    def _build_working_calendar_slots(self, settings: dict, start_date: datetime.date, max_slot: int) -> list[datetime]:
        """
        Build a list of working datetimes for time indices 1..max_slot using:
        - working_days (Mon..Sun)
        - work_start_time, work_end_time
        - optional break window
        Each slot represents 1 hour of effective work.
        """
        # working days map: {"Mon": True/False, ...}
        day_map = settings.get("working_days", {})
        # default Mon-Fri if not provided
        if not day_map:
            day_map = {d: (d in ["Mon", "Tue", "Wed", "Thu", "Fri"]) for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]} #应该不会出现这个问题

        # helper to parse "08:00 AM" style strings
        def parse_time(s: str, default: time) -> time:
            if not s:
                return default
            for fmt in ("%I:%M %p", "%H:%M"):
                try:
                    return datetime.strptime(s, fmt).time()
                except ValueError:
                    continue
            return default

        work_start = parse_time(settings.get("work_start_time", ""), time(8, 0))
        work_end = parse_time(settings.get("work_end_time", ""), time(17, 0))
        break_start = parse_time(settings.get("break_start_time", ""), time(12, 0))
        break_end = parse_time(settings.get("break_end_time", ""), time(13, 0))

        slots: list[datetime] = [None]  # 0-th unused, slots[1] is time index 1
        cur_date = start_date
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        while len(slots) - 1 < max_slot:
            weekday_name = day_names[cur_date.weekday()]
            if day_map.get(weekday_name, False):
                # working periods: [work_start, break_start) and [break_end, work_end)
                for period_start, period_end in ((work_start, break_start), (break_end, work_end)):
                    cur_dt = datetime.combine(cur_date, period_start)
                    end_dt = datetime.combine(cur_date, period_end)
                    while cur_dt < end_dt and len(slots) - 1 < max_slot:
                        slots.append(cur_dt)
                        cur_dt += timedelta(hours=1)
            cur_date += timedelta(days=1)

        return slots

    def on_calculate_clicked(self):
        """
        Handler for Calculate button:
        - Read settings (dates, capacities, costs)
        - Read raw schedule for current project
        - Build and solve PrefabScheduler
        - Save results to DB for later post-processing / visualization
        """
        if self.current_project_id is None:
            QMessageBox.warning(self, "No Project", "Please select or create a project before running Calculate.")
            return

        # 1) get settings
        settings = self._get_active_settings() or {}  # return a dict of settings
        try:
            
            # parse dates (we use only date part for T)
            fmt = "%m/%d/%Y"
            start_str = settings.get("start_datetime", "")
            target_str = settings.get("target_datetime", "")
            start_date = datetime.strptime(start_str, fmt).date() if start_str else datetime.today().date()
            end_date = datetime.strptime(target_str, fmt).date() if target_str else start_date

            # crew / machines / capacities / costs
            C_install = int(settings.get("crew_count", "1") or 1)
            M_machine = int(settings.get("machine_count", "1") or 1)
            S_site = int(settings.get("site_storage", "0") or 0)
            S_fac = int(settings.get("factory_storage", "0") or 0)
            OC = float(settings.get("order_cost", "0") or 0)
            C_I = float(settings.get("penalty_cost", "0") or 0)
            C_F = float(settings.get("factory_inv_cost", "0") or 0)
            C_O = float(settings.get("onsite_inv_cost", "0") or 0)

            # 2) load raw schedule for current project
            raw_table = self.mgr.raw_table_name(self.current_project_id)
            df = pd.read_sql_table(raw_table, self.engine)

            # minimal extraction of d, D, L, E from raw table
            # (assumes certain column names; adjust later as needed)
            # Here we index modules 1..N in dataframe order and map string Module IDs to indices
            N = len(df)
            d = {i + 1: int(df.iloc[i]["Installation Duration"]) for i in range(N)}
            D = {i + 1: int(df.iloc[i]["Production Duration"]) for i in range(N)}
            L = {i + 1: int(df.iloc[i]["Transportation Duration"]) for i in range(N)}

            # build mapping between real Module IDs and internal indices 1..N
            module_id_col = "Module_ID"
            id_to_index: dict[str, int] = {}
            index_to_id: dict[int, str] = {}
            for i in range(N):
                key = str(df.iloc[i][module_id_col]).strip()
                if key:
                    id_to_index[key] = i + 1
                    index_to_id[i + 1] = key

            # precedence list E, expecting a column like "Installation Precedence" with module IDs
            E = []
            if "Installation Precedence" in df.columns:
                for i in range(N):
                    preds_str = str(df.iloc[i]["Installation Precedence"] or "").strip()
                    if not preds_str or preds_str.upper() == "NaN":
                        continue
                    preds = [p.strip() for p in preds_str.split(",") if p.strip()]
                    for p in preds:
                        idx = id_to_index.get(p)
                        if idx is not None:
                            E.append((idx, i + 1))

            # 3) compute time horizon T from dates
            T = estimate_time_horizon(start_date, end_date)

            # 4) build and solve model
            scheduler = PrefabScheduler(
                N=N,
                T=T,
                d=d,
                E=E,
                D=D,
                L=L,
                C_install=C_install,
                M_machine=M_machine,
                S_site=S_site,
                S_fac=S_fac,
                OC=OC,
                C_I=C_I,
                C_F=C_F,
                C_O=C_O,
            )
            status = scheduler.solve()

            # 5) save results to DB for later post-processing, preserving real Module IDs
            scheduler.save_results_to_db(
                self.engine,
                self.current_project_id,
                module_id_mapping=index_to_id
            )

            # 6) load solution table and map indices to real-world schedule using working calendar
            solution_table = self.mgr.solution_table_name(self.current_project_id)
            df_sol = pd.read_sql_table(solution_table, self.engine)

            if not df_sol.empty and hasattr(self, "page_schedule") and isinstance(self.page_schedule, SchedulePage):
                # determine max index needed
                idx_cols = ["Installation_Start", "Installation_Finish", "Arrival_Time", "Production_Start", "Transport_Start"]
                max_idx = 0
                for col in idx_cols:
                    if col in df_sol.columns:
                        max_idx = max(max_idx, int(df_sol[col].max()))
                if max_idx <= 0:
                    max_idx = T

                slots = self._build_working_calendar_slots(settings, start_date, max_idx)

                def idx_to_dt(idx: int) -> str:
                    if idx is None or idx <= 0 or idx >= len(slots):
                        return ""
                    return slots[idx].strftime("%Y-%m-%d %H:%M")
                
                def idx_to_dt_obj(idx: int) -> datetime | None:
                    """Convert index to datetime object for comparison"""
                    if idx is None or idx <= 0 or idx >= len(slots):
                        return None
                    return slots[idx]

                # Get current simulation time
                # Check if we should use system time from settings
                idx_settings = self.page_index.get("settings")
                use_system_time = True
                if idx_settings is not None:
                    settings_widget = self.stack.widget(idx_settings)
                    if isinstance(settings_widget, SettingsPage):
                        use_system_time = settings_widget.use_system_time.isChecked()
                
                current_time = datetime.now() if use_system_time else datetime.now()  # For now always use system time, may change later

                rows = []
                for _, row in df_sol.iterrows():
                    mod_id = row.get("Module_ID", "")
                    fab_start_idx = int(row["Production_Start"]) if not pd.isna(row.get("Production_Start")) else None
                    fab_dur = int(row.get("Production_Duration", 0))
                    trans_start_idx = int(row["Transport_Start"]) if not pd.isna(row.get("Transport_Start")) else None
                    trans_dur = int(row.get("Transport_Duration", 0))
                    inst_start_idx = int(row["Installation_Start"]) if not pd.isna(row.get("Installation_Start")) else None
                    inst_dur = int(row.get("Installation_Duration", 0))
                    install_finish_idx = int(row["Installation_Finish"]) if not pd.isna(row.get("Installation_Finish")) else None
                    
                    # Calculate status based on current time
                    # According to rules:
                    # 1. If current time ≥ installation end → Completed
                    # 2. If current time ≥ fabrication start and < installation end → In Progress
                    # 3. If delay > 0 → Delayed
                    # 4. Else → Upcoming
                    
                    install_start_dt = idx_to_dt_obj(inst_start_idx) if inst_start_idx else None
                    install_finish_dt = idx_to_dt_obj(install_finish_idx) if install_finish_idx else None
                    fab_start_dt = idx_to_dt_obj(fab_start_idx) if fab_start_idx else None
                    
                    # Get delay values (for now all are 0, but we check for future use)
                    fab_delay = 0  # Will be populated later
                    trans_delay = 0
                    inst_delay = 0
                    has_delay = (fab_delay > 0) or (trans_delay > 0) or (inst_delay > 0)
                    
                    status = "Upcoming"  # default
                    
                    if has_delay:
                        status = "Delayed"
                    elif install_finish_dt and current_time >= install_finish_dt:
                        status = "Completed"
                    elif fab_start_dt and install_finish_dt:
                        if current_time >= fab_start_dt and current_time < install_finish_dt:
                            status = "In Progress"

                    rows.append({
                        "Module ID": mod_id,
                        "Fabrication Start Time": idx_to_dt(fab_start_idx),
                        "Fabrication Duration (h)": fab_dur,
                        "Transport Start Time": idx_to_dt(trans_start_idx),
                        "Transport Duration (h)": trans_dur,
                        "Installation Start Time": idx_to_dt(inst_start_idx),
                        "Installation Duration (h)": inst_dur,
                        "Status": status,
                        "Fab. Delay (h)": 0,
                        "Trans. Delay (h)": 0,
                        "Inst. Delay (h)": 0,
                        "_sort_key": install_start_dt,  # Store datetime object for sorting
                    })

                # Sort rows by Installation Start Time (earliest first)
                # Rows with None installation start time will be placed at the end
                rows.sort(key=lambda x: (x["_sort_key"] is None, x["_sort_key"] or datetime.max))
                
                # Remove the temporary sort key
                for row in rows:
                    row.pop("_sort_key", None)

                self.page_schedule.populate_rows(rows)

            QMessageBox.information(
                self,
                "Optimization Finished",
                f"Model solved with status {status}. Results have been saved and schedule table updated."
            )
        except Exception as e:
            QMessageBox.critical(self, "Error in Calculate", str(e))

    def on_export_schedule(self):
        """Export schedule table to Excel file"""
        if not hasattr(self, "page_schedule") or not isinstance(self.page_schedule, SchedulePage):
            QMessageBox.warning(self, "No Schedule", "Please go to Schedule page first.")
            return
        
        table = self.page_schedule.table
        if table.rowCount() == 0:
            QMessageBox.warning(self, "Empty Table", "Schedule table is empty. Please run Calculate first.")
            return
        
        # Get file path from user
        default_filename = f"schedule_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Schedule to Excel",
            default_filename,
            "Excel Files (*.xlsx);;All Files (*)"
        )
        
        if not file_path:
            return  # User cancelled
        
        try:
            # Extract data from table
            data = []
            for row in range(table.rowCount()):
                row_data = []
                for col in range(table.columnCount()):
                    if col == 7:  # Status column (has widget)
                        widget = table.cellWidget(row, col)
                        if widget:
                            # Try to find QLabel inside StatusCell
                            labels = widget.findChildren(QLabel)
                            if labels:
                                row_data.append(labels[0].text())
                            else:
                                row_data.append("")
                        else:
                            row_data.append("")
                    else:
                        item = table.item(row, col)
                        row_data.append(item.text() if item else "")
                data.append(row_data)
            
            # Create DataFrame with same column headers as table
            column_headers = [
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
            ]
            
            df = pd.DataFrame(data, columns=column_headers)
            
            # Export to Excel - try openpyxl first, fallback to default engine
            try:
                df.to_excel(file_path, index=False, engine='openpyxl')
            except ImportError:
                # If openpyxl not available, try default engine
                df.to_excel(file_path, index=False)
            
            QMessageBox.information(
                self,
                "Export Successful",
                f"Schedule has been exported to:\n{file_path}"
            )
        except ImportError as e:
            QMessageBox.critical(
                self,
                "Export Failed",
                f"Excel export requires openpyxl library.\nPlease install it: pip install openpyxl\n\nError: {str(e)}"
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Export Failed",
                f"Failed to export schedule:\n{str(e)}"
            )

    def switch_page(self, name: str):
        idx = self.page_index.get(name)
        if idx is not None:
            self.stack.setCurrentIndex(idx)
    
    def _populate_project_combo(self):
        """Load existing projects from DB into the combo box."""
        projects = self.mgr.list_projects()
        combo = self.topbar.project_combo
        combo.blockSignals(True)
        combo.clear()
        self.project_lookup = {}
        for proj in projects:
            name = proj["project_name"]
            pid = proj["project_id"]
            self.project_lookup[name] = pid # we will use this pid later for executing processes on a specific project
            combo.addItem(name)
        combo.blockSignals(False)
        if projects:
            first_name = projects[0]["project_name"] # setting the first project as the default project
            combo.setCurrentText(first_name)
            self.current_project_id = projects[0]["project_id"]
            self.topbar.delete_project_btn.show()  # Show delete button when projects exist
        else:
            self.current_project_id = None
            self.topbar.delete_project_btn.hide()  # Hide delete button when no projects

    def _on_project_selected(self, project_name: str):
        """Triggered when user selects a project from the combo box."""
        if project_name and project_name in self.project_lookup:
            self.current_project_id = self.project_lookup[project_name]
            self.topbar.delete_project_btn.show()  # Show delete button when project is selected
            # Future: refresh views based on selected project tables
        else:
            self.current_project_id = None
            self.topbar.delete_project_btn.hide()  # Hide delete button when no project selected

    def _on_project_created(self, project_id: int, project_name: str):
        """Handler for when a new project is created - updates the project combo"""
        combo = self.topbar.project_combo
        existing = [combo.itemText(i) for i in range(combo.count())]
        self.project_lookup[project_name] = project_id
        if project_name not in existing:
            combo.addItem(project_name)
        combo.setCurrentText(project_name)
        self.current_project_id = project_id
        self.topbar.delete_project_btn.show()  # Show delete button when project exists
    
    def _on_delete_project_clicked(self):
        """Handler for delete project button click"""
        if not self.current_project_id:
            return
        
        current_name = self.topbar.project_combo.currentText()
        if not current_name:
            return
        
        # Confirm deletion
        reply = QMessageBox.question(
            self,
            "Delete Project",
            f"Are you sure you want to delete project '{current_name}'?\n\n"
            "This will permanently delete:\n"
            "- All input data (raw_schedule)\n"
            "- All optimization results (solution_schedule)\n"
            "- All summary and inventory data\n\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                # Delete the project from database
                success = self.mgr.delete_project(self.current_project_id)
                
                if success:
                    # Remove from combo box
                    combo = self.topbar.project_combo
                    index = combo.findText(current_name)
                    if index >= 0:
                        combo.removeItem(index)
                    
                    # Remove from lookup
                    if current_name in self.project_lookup:
                        del self.project_lookup[current_name]
                    
                    # Update current project
                    if combo.count() > 0:
                        # Select first project if available
                        new_name = combo.itemText(0)
                        combo.setCurrentText(new_name)
                        self.current_project_id = self.project_lookup.get(new_name)
                    else:
                        # No projects left
                        self.current_project_id = None
                        self.topbar.delete_project_btn.hide()
                    
                    QMessageBox.information(
                        self,
                        "Project Deleted",
                        f"Project '{current_name}' has been deleted successfully."
                    )
                else:
                    QMessageBox.critical(
                        self,
                        "Delete Failed",
                        f"Failed to delete project '{current_name}'. Please check the console for details."
                    )
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Error",
                    f"An error occurred while deleting the project:\n{str(e)}"
                )


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI"))
    # Set application locale to English to ensure date/time widgets display in English
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.Switzerland))
    engine = create_engine(
        "sqlite:///input_database.db",  
        echo=False, future=True
    )
    w = MainWindow(engine=engine)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()


