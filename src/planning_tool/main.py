from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QPixmap, QDragEnterEvent, QDropEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QLineEdit, QComboBox,
    QHBoxLayout, QVBoxLayout, QGridLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QSizePolicy, QSpacerItem, QButtonGroup, QStackedWidget, QFileDialog, QMessageBox
)
from pathlib import Path
import sys
import pandas as pd
from sqlalchemy import create_engine, text


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

        project_combo = QComboBox()
        project_combo.addItems(["Rapla Lasteaed", "Project A", "Project B"])
        project_combo.setMinimumWidth(300)

        search = QLineEdit()
        search.setPlaceholderText("Search tasks, resources…")
        search.setClearButtonEnabled(True)
        search.setMinimumWidth(420)

        left = QHBoxLayout()
        #left.addWidget(brand)
        left.addWidget(title)
        left.addSpacing(40)
        left.addWidget(project_combo)

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
        self.btn_dash.setChecked(True)

        group = QButtonGroup(self)
        group.setExclusive(True)
        for b in (self.btn_dash, self.btn_sched, self.btn_upload):
            b.setCheckable(True)
            group.addButton(b)

        self.btn_dash.clicked.connect(   lambda: self.pageRequested.emit("dashboard"))
        self.btn_sched.clicked.connect(  lambda: self.pageRequested.emit("schedule"))
        self.btn_upload.clicked.connect( lambda: self.pageRequested.emit("upload"))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(24)
        lay.addWidget(logo)
        lay.addSpacing(16)
        lay.addWidget(self.btn_dash)
        lay.addWidget(self.btn_sched)
        lay.addWidget(self.btn_upload)
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
            self._emit_one(paths[0])  

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
    def __init__(self, title: str, icon_emoji="🗂", trailing_widget: QWidget|None=None):
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
        title_lbl = QLabel(f"<b style='font-size:30px;'>{icon_emoji} {title}</b>")
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
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(60)

        # ------------------ Card 1: Schedule Data Import ------------------
        card1 = Card("Schedule Data Import", icon_emoji="🗓", trailing_widget=None)
        drop1 = FileDropArea(
            title="Schedule Data Import",
            exts=[".csv"],
        )
        drop1.fileSelected.connect(self.on_schedule_files) # we will connect this to our sql db later

        card1.body.addWidget(drop1)

        # required chips
        req_wrap = QFrame()
        req_lay = QHBoxLayout(req_wrap)
        req_lay.setContentsMargins(0,0,0,0); req_lay.setSpacing(8)
        req_lay.addWidget(QLabel("<b>Required Fields in Upload</b>"))
        for t in ["Task ID", "Task Name", "Plan Start Date", "Plan End Date", "Risk", "Predecessors"]:
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
        card2 = Card("3D Building Model Upload", icon_emoji="🏗")
        drop2 = FileDropArea(
            title="3D Building Model Upload",
            exts=[".ifc", ".rvt"],
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
    def on_schedule_files(self, path: str):
        df = self.parse_csv(path)
        self._write_to_db(df, "schedule_tasks", if_exists="append")
        QMessageBox.information(self, "Success", f"Imported {len(df):,} rows from:\n{path}")

    def on_model_files(self, path: str):
        # TODO: 在这里解析IFC/GLTF等或触发后端处理
        print("[Model Upload] selected:", path)

    # ---------------- Process Our Data ----------------
    REQUIRED_COLS = {
        'Task ID', 'Task Type', 'Plan Start Date', 'Plan End Date', 'Plan Duration', 
        'Actual Start Date', 'Actual End Date', 'Actual Duration', 'Complete %', 
        'Slack Day', 'Risk', 'Predecessors', 'Difference'
    }

    def parse_csv(self, path: str) -> pd.DataFrame:
        """Parse CSV file and process dates and durations"""
        input_df = pd.read_csv(path)

        # Convert date columns
        for col in ("Plan Start Date", "Plan End Date"):
            if col in input_df:
                input_df[col] = pd.to_datetime(input_df[col], errors="coerce")

        # Calculate planned duration (based on start and end dates)
        if {"Plan Start Date", "Plan End Date"}.issubset(input_df.columns):
            delta = input_df["Plan End Date"] - input_df["Plan Start Date"]
            input_df["Plan Duration"] = (
                delta.dt.total_seconds() / 3600
            ).round(2).astype("Float64")

        return input_df

    def _write_to_db(self, df: pd.DataFrame, table: str, if_exists="append"):
        """Write DataFrame to database"""
        df.to_sql(table, self.engine, if_exists=if_exists, index=False, method="multi", chunksize=1000)

        # Create indexes to improve query performance
        with self.engine.begin() as conn:
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{table}_task_id ON {table}(task_id)"))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{table}_start ON {table}(start_date)"))
            except Exception:
                pass  # Index may already exist

class MainWindow(QMainWindow):
    def __init__(self, engine, parent=None):
        super().__init__()
        self.setWindowTitle("ConstructPlan - PyQt6")
        self.resize(1280, 760)
        self.engine = engine

        self.sidebar = Sidebar()
        self.sidebar.pageRequested.connect(self.switch_page)

        self.dashboardtable = DashboardTable()
        self.dashboardtable.pageRequested.connect(self.switch_page)

        self.topbar = TopBar()

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
        except NameError:
            page_upload = QLabel("Upload"); page_upload.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.page_index = {
            "dashboard": self.stack.addWidget(page_dashboard),
            "schedule":  self.stack.addWidget(page_schedule),
            "upload":    self.stack.addWidget(page_upload),
        }
        self.stack.setCurrentIndex(self.page_index["dashboard"])

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

    def switch_page(self, name: str):
        idx = self.page_index.get(name)
        if idx is not None:
            self.stack.setCurrentIndex(idx)


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI"))
    engine = create_engine(
        "sqlite:///input_database.db",  # 或你的PG/MySQL连接串
        echo=False, future=True
    )
    w = MainWindow(engine=engine)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()


