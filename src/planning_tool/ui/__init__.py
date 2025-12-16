"""
UI Components Module

This module contains all UI-related components organized into:
- widgets: Generic reusable UI widgets
- components: Application-specific UI components
- dialogs: Dialog windows
- pages: Main page widgets
"""

from .widgets import (
    SidebarButton,
    KpiCard,
    Chip,
    Card,
    FileDropArea,
    AspectRatioPixmapLabel,
    ProgressBarCell,
    TagCell,
    pill_label
)

from .components import (
    TopBar,
    Sidebar,
    DashboardTable,
    StatusCell
)

from .dialogs import (
    DelayInputDialog
)

from .pages import (
    DashboardPage,
    SchedulePage,
    UploadPage,
    SettingsPage,
    ComparisonPage
)

__all__ = [
    # Widgets
    'SidebarButton',
    'KpiCard',
    'Chip',
    'Card',
    'FileDropArea',
    'AspectRatioPixmapLabel',
    'ProgressBarCell',
    'TagCell',
    'pill_label',
    # Components
    'TopBar',
    'Sidebar',
    'DashboardTable',
    'StatusCell',
    # Dialogs
    'DelayInputDialog',
    # Pages
    'DashboardPage',
    'SchedulePage',
    'UploadPage',
    'SettingsPage',
    'ComparisonPage',
]

