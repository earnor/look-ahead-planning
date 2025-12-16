"""
Generic Reusable UI Widgets

This module contains generic, reusable UI components that can be used
across different parts of the application.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QDragEnterEvent, QDropEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QPushButton, QFrame, QLabel, QWidget, QLineEdit,
    QHBoxLayout, QVBoxLayout, QFileDialog, QProgressBar
)
from pathlib import Path


class SidebarButton(QPushButton):
    """Custom button for sidebar navigation"""
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setMinimumHeight(40)
        self.setProperty("sidebar", True)


class KpiCard(QFrame):
    """KPI card widget for displaying key performance indicators"""
    def __init__(self, title: str, value: str, subtitle: str = "", trend: str = "", icon: str = "", parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QSizePolicy
        self.setObjectName("KpiCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        
        # Set card styling
        self.setStyleSheet("""
            QFrame#KpiCard {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 12px;
            }
            QLabel#kpiTitle {
                font-size: 13px;
                font-weight: 500;
                color: #6B7280;
            }
            QLabel#kpiValue {
                font-size: 28px;
                font-weight: 600;
                color: #111827;
            }
            QLabel#kpiSubtitle {
                font-size: 12px;
                color: #6B7280;
            }
            QLabel#kpiTrend {
                font-size: 12px;
                font-weight: 500;
                color: #6B7280;
            }
            QLabel#kpiIcon {
                font-size: 24px;
            }
        """)

        # Icon label (if provided)
        icon_lbl = None
        if icon:
            icon_lbl = QLabel(icon)
            icon_lbl.setObjectName("kpiIcon")
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_lbl.setFixedSize(40, 40)
            icon_lbl.setStyleSheet("""
                QLabel#kpiIcon {
                    border-radius: 20px;
                    font-size: 20px;
                }
            """)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("kpiTitle")

        value_lbl = QLabel(value)
        value_lbl.setObjectName("kpiValue")

        sub_lbl = QLabel(subtitle)
        sub_lbl.setObjectName("kpiSubtitle")

        trend_lbl = QLabel(trend)
        trend_lbl.setObjectName("kpiTrend")
        trend_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # Top row: icon (if exists) + title + trend
        top = QHBoxLayout()
        if icon_lbl:
            top.addWidget(icon_lbl)
            top.addSpacing(12)
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


class AspectRatioPixmapLabel(QLabel):
    """Label that maintains aspect ratio when scaling pixmaps"""
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


class FileDropArea(QFrame):
    """File drop area widget for drag-and-drop file uploads"""
    from PyQt6.QtCore import pyqtSignal
    fileSelected = pyqtSignal(str)  # type: ignore

    def __init__(self, title: str, exts: list[str], parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._exts = [e.lower() for e in exts]
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

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._open_dialog()

    def _open_dialog(self):
        filt = "Files (" + " ".join(f"*{e}" for e in self._exts) + ")"
        paths, _ = QFileDialog.getOpenFileNames(self, "Select files", "", filt)
        if paths:
            self._emit_one(paths[0])

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
    """Chip/tag widget for displaying labels"""
    def __init__(self, text: str, kind="default"):
        super().__init__(text)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMargin(6)
        color = "#E5E7EB" if kind == "default" else "#DBEAFE"
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
    """Card widget container"""
    def __init__(self, title: str, trailing_widget: QWidget | None = None):
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


class ProgressBarCell(QWidget):
    """Progress bar cell widget for tables"""
    def __init__(self, percent: int):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
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
    """Tag cell widget for tables"""
    def __init__(self, text: str, bg="#eef2ff", fg="#1e40af"):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 6, 0, 6)
        h.addStretch(1)
        h.addWidget(pill_label(text, bg, fg))
        h.addStretch(1)


def pill_label(text: str, bg: str, fg: str = "#0d0d0d") -> QLabel:
    """Helper function to create a pill-shaped label"""
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

