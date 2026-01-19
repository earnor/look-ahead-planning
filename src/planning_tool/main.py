from PyQt6.QtCore import Qt, QSize, pyqtSignal, QRect
from PyQt6.QtGui import QFont, QPixmap, QDragEnterEvent, QDropEvent, QMouseEvent, QPainter, QColor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QLineEdit, QComboBox,
    QHBoxLayout, QVBoxLayout, QGridLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QSizePolicy, QSpacerItem, QButtonGroup, QStackedWidget, QFileDialog, QMessageBox, QProgressBar,
    QSplitter, QCheckBox, QGroupBox, QScrollArea, QInputDialog, QDateTimeEdit, QTimeEdit, QDialog,
    QDialogButtonBox, QSpinBox, QDoubleSpinBox
)
from PyQt6.QtCore import QDateTime, QTime, QDate, QLocale
from pathlib import Path
import sys
import os
import pandas as pd
from sqlalchemy import create_engine, text, inspect
from planning_tool.datamanager import ScheduleDataManager
from planning_tool.model import PrefabScheduler, estimate_time_horizon
from planning_tool.rescheduler import load_delays_from_db, TaskStateIdentifier, DelayApplier, FixedConstraintsBuilder
from datetime import datetime, time, timedelta
import traceback

def get_current_datetime() -> datetime:
    """
    Get current datetime for the system.
    
    For rolling optimization testing: can be overridden by environment variable TEST_REOPTIMIZE_DATETIME.
    This makes the system "think" it's at a different time point, enabling simulation of project progress
    at various time points without waiting for real time to advance.
    
    The simulated time affects:
    - Task state identification (COMPLETED/IN_PROGRESS/NOT_STARTED)
    - Fixed constraints for re-optimization
    - Delay detection timing
    - All time-based decisions in the system
    
    Format: "YYYY-MM-DD HH:MM" (e.g., "2026-01-19 09:00")
    Returns system datetime.now() if TEST_REOPTIMIZE_DATETIME is not set.
    """
    test_time_str = os.getenv('TEST_REOPTIMIZE_DATETIME')
    if test_time_str:
        try:
            # Try parsing with common datetime formats
            for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"]:
                try:
                    test_datetime = datetime.strptime(test_time_str, fmt)
                    print(f"[SIMULATION MODE] System using simulated current time: {test_datetime}")
                    return test_datetime
                except ValueError:
                    continue
            # If all formats fail, fall back to system time
            print(f"[WARNING] Failed to parse TEST_REOPTIMIZE_DATETIME='{test_time_str}', using system time instead")
        except Exception as e:
            print(f"[WARNING] Error parsing TEST_REOPTIMIZE_DATETIME: {e}, using system time instead")
    
    return datetime.now()
from planning_tool.ui import (
    DashboardPage, SchedulePage, UploadPage, SettingsPage, ComparisonPage,
    TopBar, Sidebar, DashboardTable, StatusCell,
    DelayInputDialog, Card, FileDropArea, Chip
)


class MainWindow(QMainWindow):
    def __init__(self, engine=None, parent=None):
        super().__init__()
        self.setWindowTitle("ETH Zurich")
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
            # Connect dashboard page's pageRequested signal to switch_page
            if isinstance(page_dashboard, DashboardPage):
                self.page_dashboard = page_dashboard
                page_dashboard.pageRequested.connect(self.switch_page)
        except NameError:
            page_dashboard = QLabel("Dashboard"); page_dashboard.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.page_dashboard = None

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

        try:
            page_comparison = ComparisonPage()
            self.page_comparison = page_comparison
            # Store reference to MainWindow in ComparisonPage for accessing settings and methods
            page_comparison.main_window = self
        except NameError:
            page_comparison = QLabel("Comparison"); page_comparison.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.page_comparison = None

        self.page_index = {
            "dashboard": self.stack.addWidget(page_dashboard),
            "schedule":  self.stack.addWidget(page_schedule),
            "comparison": self.stack.addWidget(page_comparison),
            "upload":    self.stack.addWidget(page_upload),
            "settings":  self.stack.addWidget(page_settings),
        }
        self.stack.setCurrentIndex(self.page_index["dashboard"])

        # wire calculate button (SchedulePage) -> MainWindow handler
        if isinstance(page_schedule, SchedulePage):
            self.page_schedule = page_schedule
            page_schedule.btn_calculate.clicked.connect(self.on_calculate_clicked)
            page_schedule.btn_export.clicked.connect(self.on_export_schedule)
            page_schedule.btn_delete_version.clicked.connect(self.on_delete_version_clicked)
            # Store reference to MainWindow in SchedulePage for delay saving and version loading
            page_schedule.main_window = self

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
        
        # Load dashboard data if dashboard is the initial page and we have a project
        if self.current_project_id is not None and hasattr(self, "page_dashboard") and self.page_dashboard:
            self.load_dashboard_data()

    def save_delay_to_db(self, delay_info: dict):
        """
        Phase 5.1: Save delay information to database.
        Converts detected_at_datetime to time index (τ) using working calendar.
        Called from SchedulePage when user confirms delay input.
        """
        if self.current_project_id is None:
            QMessageBox.warning(self, "Error", "No project selected.")
            return
        
        try:
            # Parse detected_at_datetime
            detected_at_str = delay_info["detected_at_datetime"]
            detected_at_dt = datetime.strptime(detected_at_str, "%Y-%m-%d %H:%M:%S")
            
            # Get settings to build working calendar slots
            settings = self._get_active_settings() or {}
            if not settings:
                QMessageBox.warning(self, "Error", "Settings not available. Please configure settings first.")
                return
            
            # Parse start date from settings
            fmt = "%m/%d/%Y"
            start_str = settings.get("start_datetime", "")
            if not start_str:
                QMessageBox.warning(self, "Error", "Start date not configured.")
                return
            
            start_date = datetime.strptime(start_str, fmt).date()
            
            # Build working calendar slots to find time index
            # We need to estimate max_slot - use a large number for now
            # In practice, we should use the current solution's max time index
            max_slot = 10000  # Large enough for most projects
            #这个max_slot需注意
            working_calendar_slots = self._build_working_calendar_slots(settings, start_date, max_slot)
            
            # Find time index (τ) for detected_at_dt
            tau = None
            for idx, slot_dt in enumerate(working_calendar_slots[1:], start=1):  # Skip index 0
                if slot_dt >= detected_at_dt:
                    tau = idx
                    break
            
            if tau is None:
                # If detected_at_dt is after all slots, use the last slot index
                tau = len(working_calendar_slots) - 1
            
            # Save to database
            delay_table = ScheduleDataManager.delay_updates_table_name(self.current_project_id)
            with self.engine.begin() as conn:
                conn.exec_driver_sql(f"""
                    INSERT INTO "{delay_table}" 
                    (module_id, delay_type, phase, delay_hours, detected_at_time, detected_at_datetime, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    delay_info["module_id"],
                    delay_info["delay_type"],
                    delay_info["phase"],
                    delay_info["delay_hours"],
                    tau,
                    detected_at_str,
                    delay_info.get("reason")
                ))
            
            QMessageBox.information(self, "Success", f"Delay saved successfully.\nτ = {tau}")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save delay: {str(e)}")

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

        # Get calculate button reference
        calculate_btn = None
        original_btn_text = ""
        if hasattr(self, "page_schedule") and hasattr(self.page_schedule, "btn_calculate"):
            calculate_btn = self.page_schedule.btn_calculate
            original_btn_text = calculate_btn.text()
            calculate_btn.setEnabled(False)
            calculate_btn.setText("Calculating...")
            QApplication.processEvents()  # Update UI immediately

        # Create and show simple calculating dialog
        calc_dialog = QDialog(self)
        calc_dialog.setWindowTitle("Calculating...")
        calc_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        calc_dialog.setModal(True)
        calc_dialog.setFixedSize(200, 100)
        
        layout = QVBoxLayout(calc_dialog)
        label = QLabel("Calculating...")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        
        calc_dialog.show()
        QApplication.processEvents()  # Ensure dialog is displayed

        try:
            # 1) get settings
            settings = self._get_active_settings() or {}  # return a dict of settings
            
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
            QApplication.processEvents()
            raw_table = self.mgr.raw_table_name(self.current_project_id)
            df = pd.read_sql_table(raw_table, self.engine)

            # minimal extraction of d, D, L, E from raw table
            # (assumes certain column names; adjust later as needed)
            # Here we index modules 1..N in dataframe order and map string Module IDs to indices
            N = len(df)
            # Use clearer names to avoid confusion with delay objects:
            # I_d: installation durations, D: production durations, L: transport durations
            I_d = {i + 1: int(df.iloc[i]["Installation Duration"]) for i in range(N)}
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

            # 3) compute time horizon T from dates (in working hours)
            # Simple estimate: calculate average hours per day from working calendar
            # If not available, default to 8 hours per day
            hours_per_day = 8.0  # default
            try:
                # Try to estimate from working calendar settings
                working_days = settings.get("working_days", {})
                if working_days:
                    # Count working days per week
                    working_days_per_week = sum(1 for v in working_days.values() if v)
                    if working_days_per_week > 0:
                        # Parse work hours (datetime and time are already imported at top of file)
                        def parse_time(s: str, default: time) -> time:
                            if not s:
                                return default
                            for fmt in ("%I:%M %p", "%H:%M"):
                                try:
                                    return datetime.strptime(s, fmt).time()
                                except ValueError:
                                    continue
                            return default
                        
                        work_start = parse_time(settings.get("work_start_time", "08:00"), time(8, 0))
                        work_end = parse_time(settings.get("work_end_time", "17:00"), time(17, 0))
                        break_start = parse_time(settings.get("break_start_time", "12:00"), time(12, 0))
                        break_end = parse_time(settings.get("break_end_time", "13:00"), time(13, 0))
                        
                        # Calculate hours per working day
                        ref_date = datetime(2025, 1, 1)
                        period1 = (datetime.combine(ref_date, break_start) - datetime.combine(ref_date, work_start)).total_seconds() / 3600
                        period2 = (datetime.combine(ref_date, work_end) - datetime.combine(ref_date, break_end)).total_seconds() / 3600
                        hours_per_working_day = max(0, period1) + max(0, period2)
                        
                        # Average hours per calendar day = (working_days_per_week / 7) * hours_per_working_day
                        hours_per_day = (working_days_per_week / 7.0) * hours_per_working_day
            except Exception:
                pass  # Use default 8.0 if calculation fails
            
            QApplication.processEvents()
            T = estimate_time_horizon(start_date, end_date, hours_per_day=hours_per_day)

            # Check if we have pending delays (Phase 5.2 & 6: Re-optimization workflow)
            QApplication.processEvents()
            delay_table = ScheduleDataManager.delay_updates_table_name(self.current_project_id)
            versions_table = ScheduleDataManager.optimization_versions_table_name(self.current_project_id)
            
            # Check for delays without version_id (pending delays)
            with self.engine.begin() as conn:
                pending_delays_query = f'SELECT COUNT(*) FROM "{delay_table}" WHERE version_id IS NULL'
                pending_count = conn.execute(text(pending_delays_query)).scalar()
            
            is_reoptimization = pending_count > 0
            
            if is_reoptimization:
                QApplication.processEvents()
                pending_delay_map = {}
                modules_with_delay = set()
                # Phase 6: Re-optimization workflow
                # 1. Load pending delays
                QApplication.processEvents()
                delays = load_delays_from_db(self.engine, self.current_project_id, version_id=None)
                
                if not delays:
                    calc_dialog.close()
                    if calculate_btn:
                        calculate_btn.setEnabled(True)
                        calculate_btn.setText(original_btn_text)
                    QMessageBox.warning(self, "No Delays", "No pending delays found.")
                    return
                
                # Aggregate pending delays by module and phase for display/highlight
                for d in delays:
                    key = (str(d.module_id), str(d.phase).upper())
                    pending_delay_map[key] = pending_delay_map.get(key, 0) + float(d.delay_hours or 0)
                    modules_with_delay.add(str(d.module_id))
                
                # Get the latest solution to use as base
                QApplication.processEvents()
                solution_table = self.mgr.solution_table_name(self.current_project_id)
                try:
                    inspector = inspect(self.engine)
                    if solution_table in inspector.get_table_names():
                        columns = [col['name'] for col in inspector.get_columns(solution_table)]
                        if 'version_id' in columns:
                            # Get latest version
                            query = f'''
                                SELECT * FROM "{solution_table}"
                                WHERE version_id = (SELECT MAX(version_id) FROM "{solution_table}" WHERE version_id IS NOT NULL)
                                   OR (version_id IS NULL AND NOT EXISTS (SELECT 1 FROM "{solution_table}" WHERE version_id IS NOT NULL))
                            '''
                            df_base_solution = pd.read_sql(query, self.engine)
                        else:
                            df_base_solution = pd.read_sql_table(solution_table, self.engine)
                    else:
                        calc_dialog.close()
                        if calculate_btn:
                            calculate_btn.setEnabled(True)
                            calculate_btn.setText(original_btn_text)
                        QMessageBox.warning(self, "No Base Solution", "No previous solution found. Please run initial optimization first.")
                        return
                    # Safety: ensure we actually got a DataFrame
                    if not isinstance(df_base_solution, pd.DataFrame):
                        calc_dialog.close()
                        if calculate_btn:
                            calculate_btn.setEnabled(True)
                            calculate_btn.setText(original_btn_text)
                        QMessageBox.critical(self, "Error", "Base solution is invalid (not a DataFrame).")
                        return
                except Exception as e:
                    calc_dialog.close()
                    if calculate_btn:
                        calculate_btn.setEnabled(True)
                        calculate_btn.setText(original_btn_text)
                    QMessageBox.critical(self, "Error", f"Failed to load base solution: {str(e)}")
                    return
                
                if df_base_solution.empty:
                    calc_dialog.close()
                    if calculate_btn:
                        calculate_btn.setEnabled(True)
                        calculate_btn.setText(original_btn_text)
                    QMessageBox.warning(self, "No Base Solution", "No previous solution found. Please run initial optimization first.")
                    return

                # IMPORTANT (Re-optimization): initialize duration dictionaries (D, L, I_d)
                # from the latest base solution (df_base_solution), NOT from the raw input table.
                #
                # Reason:
                # - Raw table contains original durations.
                # - df_base_solution contains durations after previous re-optimizations (including DURATION_EXTENSION).
                # If we always start from raw, a second re-optimization can unintentionally reset durations and
                # "convert" the missing duration into storage/wait time instead.
                try:
                    # Build a quick lookup by Module_ID -> row (use first match if duplicates)
                    _base_by_id = {}
                    for _idx, _row in df_base_solution.iterrows():
                        _mid = str(_row.get('Module_ID', '')).strip()
                        if _mid and _mid not in _base_by_id:
                            _base_by_id[_mid] = _row
                    for _mid, _midx in id_to_index.items():
                        _r = _base_by_id.get(_mid)
                        if _r is None:
                            continue

                        # Production duration (D)
                        _pd = _r.get('Production_Duration')
                        if pd.notna(_pd):
                            D[_midx] = int(_pd)

                        # Transport duration (L)
                        _td = _r.get('Transport_Duration')
                        if pd.notna(_td):
                            L[_midx] = int(_td)

                        # Installation duration (I_d)
                        _id = _r.get('Installation_Duration')
                        if pd.notna(_id):
                            I_d[_midx] = int(_id)
                except Exception as e:
                    # Non-fatal: fall back to raw-based durations (old behavior) if something unexpected happens
                    print(f"[WARNING] (Reopt) Failed to initialize durations from base solution: {e}")
                
                # Debug safeguard
                print(f"[Reopt] base_solution type={type(df_base_solution)} shape={getattr(df_base_solution, 'shape', None)}")

                # Build working calendar slots (needed for datetime to index conversion)
                max_idx = max(
                    df_base_solution.get('Installation_Start', pd.Series([T])).max(),
                    df_base_solution.get('Installation_Finish', pd.Series([T])).max(),
                    df_base_solution.get('Arrival_Time', pd.Series([T])).max(),
                    df_base_solution.get('Production_Start', pd.Series([T])).max(),
                    T
                )
                working_calendar_slots = self._build_working_calendar_slots(settings, start_date, int(max_idx))
                
                # Print working calendar slots for debugging
                print(f"\n[DEBUG] ========== Working Calendar Slots ==========")
                print(f"[DEBUG] Total slots: {len(working_calendar_slots)} (index 0 is placeholder)")
                if len(working_calendar_slots) > 1:
                    print(f"[DEBUG] First 10 slots:")
                    for idx in range(1, min(11, len(working_calendar_slots))):
                        print(f"  [DEBUG]   Index {idx}: {working_calendar_slots[idx]}")
                    if len(working_calendar_slots) > 11:
                        print(f"  [DEBUG]   ...")
                    if len(working_calendar_slots) > 20:
                        print(f"[DEBUG] Last 10 slots:")
                        for idx in range(max(1, len(working_calendar_slots) - 10), len(working_calendar_slots)):
                            print(f"  [DEBUG]   Index {idx}: {working_calendar_slots[idx]}")
                print(f"[DEBUG] ============================================\n")
                
                # Determine current_time (actual current time for re-optimization)
                # Use get_current_datetime() which respects TEST_REOPTIMIZE_DATETIME for testing
                current_datetime = get_current_datetime()
                
                # Convert current_datetime to time index
                print(f"[DEBUG] Converting current_datetime to time index: {current_datetime}")
                
                current_time = None
                for idx, slot_dt in enumerate(working_calendar_slots[1:], start=1):  # Skip index 0
                    if slot_dt >= current_datetime:
                        current_time = idx
                        break
                
                if current_time is None:
                    # If current_datetime is after all slots, use the last slot index
                    # If current_datetime is before all slots, use index 1 (first slot)
                    if len(working_calendar_slots) > 1 and working_calendar_slots[1] > current_datetime:
                        current_time = 1
                        print(f"[DEBUG] current_datetime is before first slot, using current_time = 1")
                    else:
                        current_time = len(working_calendar_slots) - 1
                        print(f"[DEBUG] current_datetime is after last slot, using current_time = {current_time}")
                else:
                    print(f"[DEBUG] Found current_time = {current_time} for current_datetime = {current_datetime}")
                    if current_time < len(working_calendar_slots):
                        print(f"[DEBUG]   Slot at current_time: {working_calendar_slots[current_time]}")
                    if current_time > 1:
                        print(f"[DEBUG]   Previous slot: {working_calendar_slots[current_time - 1]}")
                    if current_time + 1 < len(working_calendar_slots):
                        print(f"[DEBUG]   Next slot: {working_calendar_slots[current_time + 1]}")
                
                # 2. Identify task states (based on current_time)
                QApplication.processEvents()
                # Pass current_datetime directly to ensure accurate time comparison for state identification
                state_identifier = TaskStateIdentifier(df_base_solution, current_time, working_calendar_slots, current_datetime=current_datetime)
                task_states = state_identifier.identify_all_states()
                
                # 3. Apply delays
                QApplication.processEvents()
                delay_applier = DelayApplier(df_base_solution, delays, task_states)
                modified_solution_df = delay_applier.apply_delays()
                
                # 4. Update D, d, L dictionaries with delayed durations
                # This ensures the optimizer uses the correct durations for tasks with DURATION_EXTENSION
                # Only COMPLETED tasks keep original durations (they're already finished)
                print(f"[DEBUG] Updating duration dictionaries (D, L, I_d) from modified_solution_df")
                for _, row in modified_solution_df.iterrows():
                    module_id = str(row['Module_ID'])
                    if module_id in id_to_index:
                        module_idx = id_to_index[module_id]
                        module_states = task_states.get(module_id, [])
                        
                        # Check each phase and update duration if not COMPLETED
                        for state in module_states:
                            if state.phase == "FABRICATION":
                                # Update if not COMPLETED (IN_PROGRESS or NOT_STARTED can have duration extensions)
                                if state.status != "COMPLETED":
                                    new_duration = row.get('Production_Duration')
                                    base_rows = df_base_solution[df_base_solution['Module_ID'] == module_id]
                                    original_duration = D.get(module_idx, 0)
                                    if not base_rows.empty:
                                        original_duration = base_rows.iloc[0].get('Production_Duration', original_duration)
                                    # Only update if duration was actually changed (delay was applied)
                                    if pd.notna(new_duration) and new_duration != original_duration:
                                        D[module_idx] = int(new_duration)
                                        print(f"[DEBUG] Updated D[{module_idx}] (FABRICATION) for {module_id}: {original_duration} -> {new_duration} (status: {state.status})")
                                    elif state.status == "IN_PROGRESS":
                                        print(f"[DEBUG] IN_PROGRESS FABRICATION {module_id}: new={new_duration}, orig={original_duration}, same={new_duration == original_duration if pd.notna(new_duration) else 'N/A'}")
                            elif state.phase == "TRANSPORT":
                                if state.status != "COMPLETED":
                                    new_duration = row.get('Transport_Duration')
                                    base_rows = df_base_solution[df_base_solution['Module_ID'] == module_id]
                                    original_duration = L.get(module_idx, 0)
                                    if not base_rows.empty:
                                        original_duration = base_rows.iloc[0].get('Transport_Duration', original_duration)
                                    if pd.notna(new_duration) and new_duration != original_duration:
                                        L[module_idx] = int(new_duration)
                            elif state.phase == "INSTALLATION":
                                if state.status != "COMPLETED":
                                    new_duration = row.get('Installation_Duration')
                                    base_rows = df_base_solution[df_base_solution['Module_ID'] == module_id]
                                    # use I_d (installation duration dict) as base
                                    original_duration = I_d.get(module_idx, 0)
                                    if not base_rows.empty:
                                        original_duration = base_rows.iloc[0].get('Installation_Duration', original_duration)
                                    if pd.notna(new_duration) and new_duration != original_duration:
                                        I_d[module_idx] = int(new_duration)
                
                # 5. Build fixed constraints (using current_time, not tau)
                QApplication.processEvents()
                fixed_builder = FixedConstraintsBuilder(
                    task_states, 
                    current_time, 
                    modified_solution_df, 
                    working_calendar_slots,
                    df_base_solution
                )
                fixed_constraints = fixed_builder.build_fixed_constraints()
                
                # 6. Create new version record (Phase 5.2)
                QApplication.processEvents()
                # Get latest version number
                with self.engine.begin() as conn:
                    latest_version_query = f'SELECT MAX(version_number) FROM "{versions_table}"'
                    latest_version_result = conn.execute(text(latest_version_query)).scalar()
                    new_version_number = (latest_version_result or 0) + 1
                    
                    # Get base version_id (latest version)
                    base_version_query = f'''
                        SELECT version_id FROM "{versions_table}" 
                        WHERE version_number = (SELECT MAX(version_number) FROM "{versions_table}")
                        LIMIT 1
                    '''
                    base_version_result = conn.execute(text(base_version_query)).scalar()
                    base_version_id = base_version_result
                    
                    # Get project_start_datetime from base version (re-optimization should use the same start date)
                    base_start_datetime = None
                    if base_version_id:
                        base_start_query = text(f'SELECT project_start_datetime FROM "{versions_table}" WHERE version_id = :version_id')
                        base_start_result = conn.execute(base_start_query, {"version_id": base_version_id}).scalar()
                        if base_start_result:
                            base_start_datetime = base_start_result
                            print(f"[DEBUG] Using base version {base_version_id} project_start_datetime: '{base_start_datetime}'")
                    
                    # Use base version's start_datetime if available, otherwise fallback to current settings
                    reopt_start_datetime = base_start_datetime if base_start_datetime else (start_str if start_str and start_str.lower() != "mm/dd/yyyy" else None)
                    
                    # Get delay IDs for pending delays
                    delay_ids_query = f'SELECT delay_id FROM "{delay_table}" WHERE version_id IS NULL'
                    delay_ids = [str(row[0]) for row in conn.execute(text(delay_ids_query)).fetchall()]
                    delay_ids_str = ','.join(delay_ids) if delay_ids else None
                    
                    # Insert new version record (use current_time as reoptimize_from_time)
                    # Inherit project_start_datetime from base version (re-optimization should use same start date)
                    insert_version_query = text(f'''
                        INSERT INTO "{versions_table}" 
                        (version_number, base_version_id, reoptimize_from_time, delay_ids, project_start_datetime)
                        VALUES (:version_number, :base_version_id, :reoptimize_from_time, :delay_ids, :project_start_datetime)
                    ''')
                    conn.execute(insert_version_query, {
                        "version_number": new_version_number,
                        "base_version_id": base_version_id,
                        "reoptimize_from_time": current_time,
                        "delay_ids": delay_ids_str,
                        "project_start_datetime": reopt_start_datetime
                    })
                    
                    # Get the new version_id
                    new_version_id_query = text(f'SELECT version_id FROM "{versions_table}" WHERE version_number = :version_number')
                    new_version_id = conn.execute(new_version_id_query, {"version_number": new_version_number}).scalar()
                    
                    # Update delay records to link to new version
                    update_delays_query = text(f'UPDATE "{delay_table}" SET version_id = :version_id WHERE version_id IS NULL')
                    conn.execute(update_delays_query, {"version_id": new_version_id})
                
                # 7. Build and solve model with fixed constraints
                QApplication.processEvents()
                scheduler = PrefabScheduler(
                    N=N,
                    T=T,
                    d=I_d,
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
                
                # Set fixed constraints and re-optimization time (use current_time, not tau)
                scheduler.set_fixed_constraints(
                    fixed_installation_starts=fixed_constraints.get('fixed_installation_starts'),
                    fixed_production_starts=fixed_constraints.get('fixed_production_starts'),
                    fixed_arrival_times=fixed_constraints.get('fixed_arrival_times'),
                    fixed_durations=fixed_constraints.get('fixed_durations'),
                    reoptimize_from_time=current_time,
                    earliest_production_starts=fixed_constraints.get('earliest_production_starts'),
                    earliest_transport_starts=fixed_constraints.get('earliest_transport_starts'),
                    earliest_installation_starts=fixed_constraints.get('earliest_installation_starts')
                )
                
                QApplication.processEvents()
                status = scheduler.solve()
                
                # Check if optimization was successful
                from gurobipy import GRB
                print(f"[DEBUG] Re-optimization solve status: {status}")
                if status not in [GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL]:
                    calc_dialog.close()
                    if calculate_btn:
                        calculate_btn.setEnabled(True)
                        calculate_btn.setText(original_btn_text)
                    status_names = {
                        GRB.INFEASIBLE: "INFEASIBLE",
                        GRB.UNBOUNDED: "UNBOUNDED",
                        GRB.INF_OR_UNBD: "INF_OR_UNBD",
                        GRB.INTERRUPTED: "INTERRUPTED",
                        GRB.NUMERIC: "NUMERIC",
                    }
                    status_name = status_names.get(status, f"Status {status}")
                    QMessageBox.warning(self, "Optimization Failed", 
                        f"Re-optimization failed with status: {status_name}\n\n"
                        f"Please check your constraints and delays.")
                    return
                
                # 8. Save results with version_id (Phase 6.3)
                # Include Earliest_* columns from modified_solution_df (lower bounds from START_POSTPONEMENT)
                QApplication.processEvents()
                print(f"[DEBUG] Saving results to database with version_id={new_version_id}")
                save_success = scheduler.save_results_to_db(
                    self.engine,
                    self.current_project_id,
                    module_id_mapping=index_to_id,
                    version_id=new_version_id,
                    earliest_start_columns=modified_solution_df  # Pass modified_solution_df to include Earliest_* columns
                )
                
                print(f"[DEBUG] Save results returned: {save_success}")
                if not save_success:
                    calc_dialog.close()
                    if calculate_btn:
                        calculate_btn.setEnabled(True)
                        calculate_btn.setText(original_btn_text)
                    QMessageBox.critical(self, "Save Failed", 
                        "Failed to save optimization results to database.\n"
                        "Please check the console for error messages.")
                    return
                
                # Update version record with optimization results
                solution = scheduler.get_solution_dict()
                if solution:
                    with self.engine.begin() as conn:
                        # Get base version's start_datetime again (for consistency)
                        base_start_datetime = None
                        if base_version_id:
                            base_start_query = text(f'SELECT project_start_datetime FROM "{versions_table}" WHERE version_id = :version_id')
                            base_start_result = conn.execute(base_start_query, {"version_id": base_version_id}).scalar()
                            if base_start_result:
                                base_start_datetime = base_start_result
                        
                        reopt_start_datetime = base_start_datetime if base_start_datetime else (start_str if start_str and start_str.lower() != "mm/dd/yyyy" else None)
                        
                        update_version_query = text(f'''
                            UPDATE "{versions_table}" 
                            SET objective_value = :objective_value, status = :status,
                                project_start_datetime = COALESCE(project_start_datetime, :project_start_datetime)
                            WHERE version_id = :version_id
                        ''')
                        conn.execute(update_version_query, {
                            "objective_value": solution.get('objective'),
                            "status": solution.get('status'),
                            "project_start_datetime": reopt_start_datetime,
                            "version_id": new_version_id
                        })
                
                # Close dialog and restore button state
                calc_dialog.close()
                if calculate_btn:
                    calculate_btn.setEnabled(True)
                    calculate_btn.setText(original_btn_text)
                
                QMessageBox.information(self, "Re-optimization Complete", 
                    f"Re-optimization completed successfully.\nVersion: {new_version_number}\nCurrent time: {current_time}")
            else:
                # Initial optimization (existing logic)
                # 4) build and solve model
                QApplication.processEvents()
                scheduler = PrefabScheduler(
                N=N,
                T=T,
                d=I_d,
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
                QApplication.processEvents()
                status = scheduler.solve()

                # 5) Create or get version 0 record for initial optimization (before saving results)
                QApplication.processEvents()
                versions_table = self.mgr.optimization_versions_table_name(self.current_project_id)
                version_0_id = None
                
                with self.engine.begin() as conn:
                    # Get or create version 0 record (use INSERT OR IGNORE to prevent duplicates)
                    check_version_query = text(f'SELECT version_id FROM "{versions_table}" WHERE version_number = 0')
                    version_0_id = conn.execute(check_version_query).scalar()
                    
                    if version_0_id is None:
                        # Version 0 doesn't exist, create it (INSERT OR IGNORE ensures no duplicates even in concurrent scenarios)
                        # Save the start_datetime used for this optimization
                        insert_version_query = text(f'''
                            INSERT OR IGNORE INTO "{versions_table}" 
                            (version_number, base_version_id, reoptimize_from_time, project_start_datetime)
                            VALUES (0, NULL, :reoptimize_from_time, :project_start_datetime)
                        ''')
                        conn.execute(insert_version_query, {
                            "reoptimize_from_time": get_current_datetime(),
                            "project_start_datetime": start_str if start_str and start_str.lower() != "mm/dd/yyyy" else None
                        })
                        
                        # Get the version_id for version 0 (after insert or if it was created concurrently)
                        get_version_id_query = text(f'SELECT version_id FROM "{versions_table}" WHERE version_number = 0')
                        version_0_id = conn.execute(get_version_id_query).scalar()
                    
                    # Update project_start_datetime if it's missing (for existing records)
                    if version_0_id is not None:
                        update_start_date_query = text(f'''
                            UPDATE "{versions_table}" 
                            SET project_start_datetime = :project_start_datetime
                            WHERE version_id = :version_id 
                        ''')
                        conn.execute(update_start_date_query, {
                            "project_start_datetime": start_str if start_str and start_str.lower() != "mm/dd/yyyy" else None,
                            "version_id": version_0_id
                        })

                # 5.5) Save results to DB with version_0_id (preserving real Module IDs)
                QApplication.processEvents()
                scheduler.save_results_to_db(
                    self.engine,
                    self.current_project_id,
                    module_id_mapping=index_to_id,
                    version_id=version_0_id
                )

                # 5.6) Update version record with optimization results
                QApplication.processEvents()
                solution = scheduler.get_solution_dict()
                if solution and version_0_id:
                    with self.engine.begin() as conn:
                        update_version_query = text(f'''
                            UPDATE "{versions_table}" 
                            SET objective_value = :objective_value, status = :status,
                                project_start_datetime = COALESCE(project_start_datetime, :project_start_datetime)
                            WHERE version_id = :version_id
                        ''')
                        conn.execute(update_version_query, {
                            "objective_value": solution.get('objective'),
                            "status": solution.get('status'),
                            "project_start_datetime": start_str if start_str and start_str.lower() != "mm/dd/yyyy" else None,
                            "version_id": version_0_id
                        })

            # 6) load solution table and map indices to real-world schedule using working calendar
            QApplication.processEvents()
            solution_table = self.mgr.solution_table_name(self.current_project_id)
            # If version_id column exists, get the latest version (max version_id) or all if version_id is NULL
            # Otherwise, just read all data
            try:
                inspector = inspect(self.engine)
                if solution_table in inspector.get_table_names():
                    columns = [col['name'] for col in inspector.get_columns(solution_table)]
                    if 'version_id' in columns:
                        # Get latest version (max version_id) or records with NULL version_id
                        query = f'''
                            SELECT * FROM "{solution_table}"
                            WHERE version_id IS NULL 
                               OR version_id = (SELECT MAX(version_id) FROM "{solution_table}" WHERE version_id IS NOT NULL)
                        '''
                        df_sol = pd.read_sql(query, self.engine)
                    else:
                        df_sol = pd.read_sql_table(solution_table, self.engine)
                else:
                    df_sol = pd.DataFrame()
            except Exception:
                # Fallback: just read all data
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
                
                current_time = get_current_datetime() if use_system_time else get_current_datetime()  # Use simulated time if TEST_REOPTIMIZE_DATETIME is set

                rows = []
                # Use pending delays map if available (only for re-optimization)
                pending_delay_map = locals().get("pending_delay_map", {})
                modules_with_delay = locals().get("modules_with_delay", set())

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
                    
                    # Get pending delay values per phase (only pending delays, version_id IS NULL)
                    fab_delay = pending_delay_map.get((str(mod_id), "FABRICATION"), 0)
                    trans_delay = pending_delay_map.get((str(mod_id), "TRANSPORT"), 0)
                    inst_delay = pending_delay_map.get((str(mod_id), "INSTALLATION"), 0)
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
                        "Fab. Delay (h)": fab_delay,
                        "Trans. Delay (h)": trans_delay,
                        "Inst. Delay (h)": inst_delay,
                        "_has_delay": has_delay or (str(mod_id) in modules_with_delay),
                        "_sort_key": fab_start_dt,  # Store datetime object for sorting
                    })

                # Sort rows by Fabrication Start Time (earliest first)
                # Rows with None fabrication start time will be placed at the end
                rows.sort(key=lambda x: (x["_sort_key"] is None, x["_sort_key"] or datetime.max))
                
                # Remove the temporary sort key
                for row in rows:
                    row.pop("_sort_key", None)

                QApplication.processEvents()
                self.page_schedule.populate_rows(rows)
                
                # Refresh version list in schedule page to include newly created version (without auto-loading)
                # Data is already loaded above, we just need to refresh the combobox list
                if hasattr(self, "page_schedule") and isinstance(self.page_schedule, SchedulePage):
                    self.page_schedule.load_version_list(self.engine, self.current_project_id, auto_load=False)

            # Close dialog and restore button state
            calc_dialog.close()
            if calculate_btn:
                calculate_btn.setEnabled(True)
                calculate_btn.setText(original_btn_text)

            QMessageBox.information(
                self,
                "Optimization Finished",
                f"Model solved with status {status}. Results have been saved and schedule table updated."
            )
        except Exception as e:
            # Close dialog and restore button state on error
            calc_dialog.close()
            if calculate_btn:
                calculate_btn.setEnabled(True)
                calculate_btn.setText(original_btn_text)
            
            tb = traceback.format_exc()
            print(tb)
            QMessageBox.critical(self, "Error in Calculate", f"{e}\n\n{tb}")

    def load_schedule_by_version(self, project_id: int, version_id: int):
        """
        Load schedule data for a specific version and populate the schedule table.
        Called from SchedulePage when user selects a version from the combobox.
        """
        print(f"[DEBUG MainWindow] load_schedule_by_version called: project_id={project_id}, version_id={version_id}")
        
        if not hasattr(self, "page_schedule") or not isinstance(self.page_schedule, SchedulePage):
            print(f"[DEBUG MainWindow] page_schedule not available")
            return
        
        try:
            solution_table = self.mgr.solution_table_name(project_id)
            print(f"[DEBUG MainWindow] solution_table: {solution_table}")
            inspector = inspect(self.engine)
            
            table_names = inspector.get_table_names()
            print(f"[DEBUG MainWindow] Available tables: {table_names}")
            
            if solution_table not in table_names:
                print(f"[DEBUG MainWindow] Solution table {solution_table} does not exist - no optimization results yet")
                # Clear the schedule table to show empty state
                if hasattr(self, "page_schedule") and isinstance(self.page_schedule, SchedulePage):
                    self.page_schedule.populate_rows([])
                return
            
            # Check what version_ids exist in the solution table
            check_versions_query = f'SELECT DISTINCT version_id, COUNT(*) as count FROM "{solution_table}" GROUP BY version_id'
            versions_in_table = pd.read_sql(text(check_versions_query), self.engine)
            print(f"[DEBUG MainWindow] Version IDs in solution table:\n{versions_in_table}")
            
            # Load data for the specific version
            query = f'SELECT * FROM "{solution_table}" WHERE version_id = :version_id ORDER BY Production_Start ASC'
            print(f"[DEBUG MainWindow] Executing query: {query} with version_id={version_id}")
            df_sol = pd.read_sql(text(query), self.engine, params={"version_id": version_id})
            print(f"[DEBUG MainWindow] Loaded {len(df_sol)} rows for version_id={version_id}")
            
            # If no data found, check if this version corresponds to version_number = 0
            # and if so, try loading NULL version_id data (legacy data)
            if df_sol.empty:
                print(f"[DEBUG MainWindow] No data found for version_id={version_id}, checking if this is version 0")
                versions_table = self.mgr.optimization_versions_table_name(project_id)
                if versions_table in table_names:
                    # Check if the requested version_id corresponds to version_number = 0
                    check_version_0_query = f'SELECT version_number FROM "{versions_table}" WHERE version_id = :version_id'
                    version_number_result = pd.read_sql(text(check_version_0_query), self.engine, params={"version_id": version_id})
                    if not version_number_result.empty:
                        version_number = version_number_result.iloc[0]['version_number']
                        if version_number == 0:
                            print(f"[DEBUG MainWindow] This is version 0, trying to load NULL version_id data")
                            # Try loading data where version_id IS NULL (legacy data)
                            legacy_query = f'SELECT * FROM "{solution_table}" WHERE version_id IS NULL ORDER BY Production_Start ASC'
                            df_sol = pd.read_sql(text(legacy_query), self.engine)
                            print(f"[DEBUG MainWindow] Loaded {len(df_sol)} rows from legacy NULL version_id data")
                        else:
                            print(f"[DEBUG MainWindow] version_id={version_id} corresponds to version_number={version_number}, but no data found")
            
            if df_sol.empty:
                print(f"[DEBUG MainWindow] No data found for version_id={version_id} (including legacy data)")
                # Clear the schedule table to show empty state
                if hasattr(self, "page_schedule") and isinstance(self.page_schedule, SchedulePage):
                    self.page_schedule.populate_rows([])
                return
            
            # Get settings for working calendar (still needed for other settings like work hours, working days, etc.)
            settings = self._get_active_settings() or {}
            if not settings:
                return
            
            # Get saved start_datetime from version record (preferred)
            versions_table = self.mgr.optimization_versions_table_name(project_id)
            saved_start_str = None
            if versions_table in table_names:
                try:
                    version_info_query = f'SELECT project_start_datetime FROM "{versions_table}" WHERE version_id = :version_id'
                    version_info_result = pd.read_sql(text(version_info_query), self.engine, params={"version_id": version_id})
                    if not version_info_result.empty and pd.notna(version_info_result.iloc[0]['project_start_datetime']):
                        saved_start_str = version_info_result.iloc[0]['project_start_datetime']
                        print(f"[DEBUG MainWindow] Found saved project_start_datetime for version {version_id}: '{saved_start_str}'")
                except Exception as e:
                    print(f"[DEBUG MainWindow] Could not retrieve saved start_datetime: {e}")
            
            # Parse start date - use saved value if available, otherwise fallback to current settings
            fmt = "%m/%d/%Y"
            start_str = saved_start_str if saved_start_str else settings.get("start_datetime", "")
            if not start_str or start_str.lower() == "mm/dd/yyyy":
                # Handle placeholder text - use a default date or skip
                print(f"[DEBUG MainWindow] Invalid start_datetime value: '{start_str}', using today's date as fallback")
                start_date = datetime.today().date()
            else:
                try:
                    start_date = datetime.strptime(start_str, fmt).date()
                except ValueError:
                    print(f"[DEBUG MainWindow] Failed to parse start_datetime '{start_str}', using today's date as fallback")
                    start_date = datetime.today().date()
            
            # Determine max index needed
            idx_cols = ["Installation_Start", "Installation_Finish", "Arrival_Time", "Production_Start", "Transport_Start"]
            max_idx = 0
            for col in idx_cols:
                if col in df_sol.columns:
                    max_idx = max(max_idx, int(df_sol[col].max()))
            if max_idx <= 0:
                max_idx = 1000  # Default fallback
            
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
            idx_settings = self.page_index.get("settings")
            use_system_time = True
            if idx_settings is not None:
                settings_widget = self.stack.widget(idx_settings)
                if isinstance(settings_widget, SettingsPage):
                    use_system_time = settings_widget.use_system_time.isChecked()
            
            current_time = get_current_datetime() if use_system_time else get_current_datetime()
            
            # Load delays for this version (if any)
            delay_table = ScheduleDataManager.delay_updates_table_name(project_id)
            pending_delay_map = {}
            modules_with_delay = set()
            try:
                if delay_table in inspector.get_table_names():
                    delays_query = f'SELECT module_id, phase, delay_hours FROM "{delay_table}" WHERE version_id = :version_id'
                    delays_df = pd.read_sql(text(delays_query), self.engine, params={"version_id": version_id})
                    for _, delay_row in delays_df.iterrows():
                        module_id = str(delay_row['module_id'])
                        phase = str(delay_row['phase']).upper()
                        delay_hours = float(delay_row['delay_hours'] or 0)
                        if delay_hours > 0:
                            pending_delay_map[(module_id, phase)] = delay_hours
                            modules_with_delay.add(module_id)
            except Exception as e:
                print(f"Warning: Could not load delays for version {version_id}: {e}")
            
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
                install_start_dt = idx_to_dt_obj(inst_start_idx) if inst_start_idx else None
                install_finish_dt = idx_to_dt_obj(install_finish_idx) if install_finish_idx else None
                fab_start_dt = idx_to_dt_obj(fab_start_idx) if fab_start_idx else None
                
                # Get delay values per phase for this version
                fab_delay = pending_delay_map.get((str(mod_id), "FABRICATION"), 0)
                trans_delay = pending_delay_map.get((str(mod_id), "TRANSPORT"), 0)
                inst_delay = pending_delay_map.get((str(mod_id), "INSTALLATION"), 0)
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
                    "Fab. Delay (h)": fab_delay,
                    "Trans. Delay (h)": trans_delay,
                    "Inst. Delay (h)": inst_delay,
                    "_has_delay": has_delay or (str(mod_id) in modules_with_delay),
                    "_sort_key": fab_start_dt,
                })
            
            # Sort rows by Fabrication Start Time
            rows.sort(key=lambda x: (x["_sort_key"] is None, x["_sort_key"] or datetime.max))
            
            # Remove the temporary sort key
            for row in rows:
                row.pop("_sort_key", None)
            
            self.page_schedule.populate_rows(rows)
            
        except Exception as e:
            print(f"Error loading schedule by version: {e}")
            import traceback
            traceback.print_exc()

    def on_export_schedule(self):
        """Export schedule table to Excel file"""
        if not hasattr(self, "page_schedule") or not isinstance(self.page_schedule, SchedulePage):
            QMessageBox.warning(self, "No Schedule", "Please go to Schedule page first.")
            return
        
        table = self.page_schedule.table
        if table.rowCount() == 0:
            QMessageBox.warning(self, "Empty Table", "Schedule table is empty. Please run Calculate first.")
            return
        
        # Get project name
        project_name = self.topbar.project_combo.currentText() if self.current_project_id else "Unknown"
        # Sanitize project name for filename (remove invalid characters)
        safe_project_name = "".join(c for c in project_name if c.isalnum() or c in (' ', '-', '_')).strip()
        safe_project_name = safe_project_name.replace(' ', '_') if safe_project_name else "project"
        
        # Get version number from database
        version_number = None
        if self.current_project_id:
            try:
                solution_table = self.mgr.solution_table_name(self.current_project_id)
                versions_table = self.mgr.optimization_versions_table_name(self.current_project_id)
                inspector = inspect(self.engine)
                
                if solution_table in inspector.get_table_names():
                    columns = [col['name'] for col in inspector.get_columns(solution_table)]
                    if 'version_id' in columns:
                        # Get the latest version_id from solution table
                        with self.engine.begin() as conn:
                            max_version_id_query = f'SELECT MAX(version_id) FROM "{solution_table}" WHERE version_id IS NOT NULL'
                            max_version_id = conn.execute(text(max_version_id_query)).scalar()
                            
                            if max_version_id and versions_table in inspector.get_table_names():
                                # Get version_number from versions table
                                version_query = f'SELECT version_number FROM "{versions_table}" WHERE version_id = :version_id'
                                version_result = conn.execute(text(version_query), {"version_id": max_version_id}).scalar()
                                if version_result is not None:
                                    version_number = version_result
            except Exception as e:
                print(f"Warning: Could not retrieve version number: {e}")
        
        # Build filename
        if version_number is not None:
            default_filename = f"{safe_project_name}_v{version_number}.xlsx"
        else:
            # Fallback to project name only if version not available
            default_filename = f"{safe_project_name}.xlsx"
        
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
            
            # Get settings for weight values
            settings = self._get_active_settings() or {}
            weight_settings_data = [
                {"Setting": "Factory Inventory Cost (C_F)", "Value": settings.get("factory_inv_cost", "")},
                {"Setting": "Onsite Inventory Cost (C_O)", "Value": settings.get("onsite_inv_cost", "")},
                {"Setting": "Penalty Cost per Unit Time (C_I)", "Value": settings.get("penalty_cost", "")},
                {"Setting": "Order Batch Cost (OC)", "Value": settings.get("order_cost", "")}
            ]
            weight_settings_df = pd.DataFrame(weight_settings_data)
            
            # Export to Excel with multiple sheets - try openpyxl first, fallback to default engine
            try:
                with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
                    df.to_excel(writer, sheet_name='Schedule', index=False)
                    weight_settings_df.to_excel(writer, sheet_name='Settings', index=False)
            except ImportError:
                # If openpyxl not available, try default engine (may not support multiple sheets)
                try:
                    with pd.ExcelWriter(file_path, engine='xlsxwriter') as writer:
                        df.to_excel(writer, sheet_name='Schedule', index=False)
                        weight_settings_df.to_excel(writer, sheet_name='Settings', index=False)
                except ImportError:
                    # Last resort: single sheet with default engine
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
            # Update sidebar button states
            self._update_sidebar_selection(name)
            # Load data for comparison page when it's shown
            if name == "comparison" and hasattr(self, "page_comparison") and self.page_comparison:
                if self.current_project_id is not None:
                    self.page_comparison.load_version_list(self.engine, self.current_project_id)
            # Load version list for schedule page when it's shown
            elif name == "schedule" and hasattr(self, "page_schedule") and self.page_schedule:
                if self.current_project_id is not None:
                    self.page_schedule.load_version_list(self.engine, self.current_project_id)
            # Load data for dashboard page when it's shown
            elif name == "dashboard" and hasattr(self, "page_dashboard") and self.page_dashboard:
                self.load_dashboard_data()
    
    def _update_sidebar_selection(self, page_name: str):
        """Update sidebar button selection based on current page"""
        # Uncheck all buttons first
        self.sidebar.btn_dash.setChecked(False)
        self.sidebar.btn_sched.setChecked(False)
        self.sidebar.btn_comparison.setChecked(False)
        self.sidebar.btn_upload.setChecked(False)
        self.sidebar.btn_settings.setChecked(False)
        
        # Check the corresponding button
        if page_name == "dashboard":
            self.sidebar.btn_dash.setChecked(True)
        elif page_name == "schedule":
            self.sidebar.btn_sched.setChecked(True)
        elif page_name == "comparison":
            self.sidebar.btn_comparison.setChecked(True)
        elif page_name == "upload":
            self.sidebar.btn_upload.setChecked(True)
        elif page_name == "settings":
            self.sidebar.btn_settings.setChecked(True)
    
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
            # Refresh dashboard page if currently viewing it
            if hasattr(self, "page_dashboard") and self.page_dashboard:
                current_idx = self.stack.currentIndex()
                if current_idx == self.page_index.get("dashboard"):
                    self.load_dashboard_data()
            # Refresh comparison page version list if currently viewing it
            if hasattr(self, "page_comparison") and self.page_comparison:
                current_idx = self.stack.currentIndex()
                if current_idx == self.page_index.get("comparison"):
                    self.page_comparison.load_version_list(self.engine, self.current_project_id)
            # Refresh schedule page version list if currently viewing it
            if hasattr(self, "page_schedule") and self.page_schedule:
                current_idx = self.stack.currentIndex()
                if current_idx == self.page_index.get("schedule"):
                    self.page_schedule.load_version_list(self.engine, self.current_project_id)
        else:
            self.current_project_id = None
            self.topbar.delete_project_btn.hide()  # Hide delete button when no project selected
            # Clear dashboard page if currently viewing it
            if hasattr(self, "page_dashboard") and self.page_dashboard:
                current_idx = self.stack.currentIndex()
                if current_idx == self.page_index.get("dashboard"):
                    self.load_dashboard_data()
            # Clear comparison page if currently viewing it
            if hasattr(self, "page_comparison") and self.page_comparison:
                current_idx = self.stack.currentIndex()
                if current_idx == self.page_index.get("comparison"):
                    self.page_comparison.load_version_list(self.engine, None)
            # Clear schedule page if currently viewing it
            if hasattr(self, "page_schedule") and self.page_schedule:
                current_idx = self.stack.currentIndex()
                if current_idx == self.page_index.get("schedule"):
                    self.page_schedule.load_version_list(self.engine, None)

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
        
    
    def load_dashboard_data(self):
        """Load data for dashboard page, specifically today's fabrication modules"""
        if not hasattr(self, "page_dashboard") or not isinstance(self.page_dashboard, DashboardPage):
            return
        
        if self.current_project_id is None:
            # Clear table if no project selected
            if hasattr(self.page_dashboard, "table"):
                self.page_dashboard.table.load_tomorrow_fabrication_modules([])
            return
        else:
            print(f"current_project_id: {self.current_project_id}")
        
        try:
            from sqlalchemy import inspect, text
            from datetime import timedelta
            
            solution_table = self.mgr.solution_table_name(self.current_project_id)
            versions_table = self.mgr.optimization_versions_table_name(self.current_project_id)
            inspector = inspect(self.engine)
            
            # Check if solution table exists
            if solution_table not in inspector.get_table_names():
                if hasattr(self.page_dashboard, "table"):
                    self.page_dashboard.table.load_tomorrow_fabrication_modules([])
                return
            
            # Get max version_id from optimization_versions table (not from solution_table)
            max_version_id = None
            if versions_table in inspector.get_table_names():
                max_version_query = f'SELECT MAX(version_id) FROM "{versions_table}"'
                max_version_result = pd.read_sql(text(max_version_query), self.engine)
                max_version_id = max_version_result.iloc[0, 0] if not max_version_result.empty else None
            else:
                # Fallback: try to get from solution_table if versions_table doesn't exist
                max_version_query = f'SELECT MAX(version_id) FROM "{solution_table}" WHERE version_id IS NOT NULL'
                max_version_result = pd.read_sql(text(max_version_query), self.engine)
                max_version_id = max_version_result.iloc[0, 0] if not max_version_result.empty else None
            
            if max_version_id is None or pd.isna(max_version_id):
                # No version data, clear table
                if hasattr(self.page_dashboard, "table"):
                    self.page_dashboard.table.load_tomorrow_fabrication_modules([])
                return
            
            # Ensure version_id is an integer
            max_version_id = int(max_version_id)
            
            # Get saved start_datetime from version record
            saved_start_str = None
            if versions_table in inspector.get_table_names():
                try:
                    version_info_query = f'SELECT project_start_datetime FROM "{versions_table}" WHERE version_id = :version_id'
                    version_info_result = pd.read_sql(text(version_info_query), self.engine, params={"version_id": max_version_id})
                    if not version_info_result.empty and pd.notna(version_info_result.iloc[0]['project_start_datetime']):
                        saved_start_str = version_info_result.iloc[0]['project_start_datetime']
                except Exception:
                    pass
            
            # Get settings for working calendar
            settings = self._get_active_settings() or {}
            if not settings:
                if hasattr(self.page_dashboard, "table"):
                    self.page_dashboard.table.load_tomorrow_fabrication_modules([])
                return
            
            # Parse start date - use saved value if available, otherwise fallback to current settings
            fmt = "%m/%d/%Y"
            start_str = saved_start_str if saved_start_str else settings.get("start_datetime", "")
            print(f"start_str: {start_str}")
            if not start_str or start_str.lower() == "mm/dd/yyyy":
                start_date = datetime.today().date()
            else:
                try:
                    start_date = datetime.strptime(start_str, fmt).date()
                except ValueError:
                    start_date = datetime.today().date()
            
            # Calculate today's date (use simulated time if TEST_REOPTIMIZE_DATETIME is set)
            today_date = get_current_datetime().date()
            
            # Load solution data for max version
            query = f'SELECT * FROM "{solution_table}" WHERE version_id = :version_id'
            df_sol = pd.read_sql(text(query), self.engine, params={"version_id": max_version_id})
            
            if df_sol.empty:
                if hasattr(self.page_dashboard, "table"):
                    self.page_dashboard.table.load_tomorrow_fabrication_modules([])
                return
            
            # Determine max index needed
            idx_cols = ["Production_Start", "Installation_Finish"]
            max_idx = 0
            for col in idx_cols:
                if col in df_sol.columns:
                    max_idx = max(max_idx, int(df_sol[col].max()) if not df_sol[col].isna().all() else 0)
            if max_idx <= 0:
                max_idx = 1000  # Default fallback
            
            # Build working calendar slots
            slots = self._build_working_calendar_slots(settings, start_date, max_idx)
            
            # Find time indices that correspond to today's date
            today_indices = set()
            for idx in range(1, len(slots)):
                if slots[idx] is not None:
                    slot_date = slots[idx].date()
                    if slot_date == today_date:
                        today_indices.add(idx)
            
            # Query modules with Production_Start in today's time indices
            if not today_indices:
                # No working slots today, table will be empty
                df_today = pd.DataFrame()
            else:
                df_today = df_sol[df_sol['Production_Start'].isin(today_indices)].copy()
            
            # Convert Production_Start to datetime string
            def idx_to_dt_str(idx: int) -> str:
                if idx is None or idx <= 0 or idx >= len(slots):
                    return ""
                dt = slots[idx]
                return dt.strftime("%Y-%m-%d %H:%M")
            
            # Prepare data for table
            table_data = []
            for _, row in df_today.iterrows():
                module_id = str(row.get('Module_ID', ''))
                prod_start_idx = int(row.get('Production_Start', 0))
                prod_duration = int(row.get('Production_Duration', 0))
                start_datetime_str = idx_to_dt_str(prod_start_idx)
                
                table_data.append({
                    "Module_ID": module_id,
                    "Fabrication_Start_Time": start_datetime_str,
                    "Production_Duration": str(prod_duration),
                    "Production_Start": str(prod_start_idx),
                    "_sort_key": prod_start_idx  # For sorting
                })
            
            # Sort by Production_Start (time index) in ascending order
            table_data.sort(key=lambda x: x["_sort_key"])
            # Remove sort key before passing to table
            for item in table_data:
                item.pop("_sort_key", None)
            
            # Load data into table
            if hasattr(self.page_dashboard, "table"):
                self.page_dashboard.table.load_tomorrow_fabrication_modules(table_data)
            
            # Calculate and update key metrics
            self._update_dashboard_metrics(
                df_sol, max_version_id, slots, start_date, today_date, settings, inspector
            )
                
        except Exception as e:
            print(f"Error loading dashboard data: {e}")
            import traceback
            traceback.print_exc()
            if hasattr(self.page_dashboard, "table"):
                self.page_dashboard.table.load_tomorrow_fabrication_modules([])
    
    def _update_dashboard_metrics(self, df_sol: pd.DataFrame, max_version_id: int, 
                                  slots: list, start_date: datetime.date, today_date: datetime.date,
                                  settings: dict, inspector):
        """Calculate and update dashboard key metrics"""
        if not hasattr(self.page_dashboard, "card_planned_vs_actual"):
            return  # Cards not initialized yet
        
        try:
            from sqlalchemy import text
            from calendar import month_abbr
            
            solution_table = self.mgr.solution_table_name(self.current_project_id)
            versions_table = self.mgr.optimization_versions_table_name(self.current_project_id)
            delay_table = ScheduleDataManager.delay_updates_table_name(self.current_project_id)
            summary_table = self.mgr.summary_table_name(self.current_project_id)
            
            # Re-query solution data to ensure we're using the correct version_id
            query = f'SELECT * FROM "{solution_table}" WHERE version_id = :version_id'
            df_sol = pd.read_sql(text(query), self.engine, params={"version_id": max_version_id})
            
            if df_sol.empty:
                return  # No data for this version, skip metrics update
            
            # Helper function to format date as "Dec, 15, 2025"
            def format_date_as_month_day_year(dt: datetime.date) -> str:
                """Format date as 'Dec, 15, 2025'"""
                month_name = month_abbr[dt.month]
                return f"{month_name}, {dt.day}, {dt.year}"
            
            # Helper function to convert time index to date
            def idx_to_date(idx: int) -> datetime.date:
                if idx is None or idx <= 0 or idx >= len(slots):
                    return None
                return slots[idx].date()
            
            # Calculate current time index from current datetime
            # Use the last time index <= current_datetime (most accurate for completed status)
            # Use simulated time if TEST_REOPTIMIZE_DATETIME is set
            current_datetime = get_current_datetime()
            current_time_idx = None
            # Find the last time index <= current_datetime
            for idx in range(1, len(slots)):
                if slots[idx] is not None and slots[idx] <= current_datetime:
                    current_time_idx = idx
                elif slots[idx] is not None and slots[idx] > current_datetime:
                    break
            
            # If current_datetime is before all slots, use index 1
            if current_time_idx is None:
                current_time_idx = 1 if len(slots) > 1 else None
            
            # 1. Planned vs Actual: completed modules / total modules
            total_modules = len(df_sol)
            completed_modules = 0
            if current_time_idx is not None and 'Installation_Finish' in df_sol.columns:
                for _, row in df_sol.iterrows():
                    install_finish_idx = row.get('Installation_Finish')
                    if pd.notna(install_finish_idx) and int(install_finish_idx) <= current_time_idx:
                        completed_modules += 1
            
            planned_vs_actual_pct = (completed_modules / total_modules * 100) if total_modules > 0 else 0
            planned_vs_actual_str = f"{planned_vs_actual_pct:.0f}%"
            
            # 2. Critical Tasks: count of delays
            critical_tasks_count = 0
            if delay_table in inspector.get_table_names():
                try:
                    delay_count_query = f'SELECT COUNT(*) FROM "{delay_table}" WHERE version_id = :version_id'
                    delay_count_result = pd.read_sql(text(delay_count_query), self.engine, params={"version_id": max_version_id})
                    critical_tasks_count = delay_count_result.iloc[0, 0] if not delay_count_result.empty else 0
                except Exception:
                    pass
            
            # Update Planned vs Actual with subtitle based on delays
            planned_vs_actual_subtitle = "Delayed" if critical_tasks_count > 0 else ""
            self.page_dashboard.card_planned_vs_actual.update(
                value=planned_vs_actual_str,
                subtitle=planned_vs_actual_subtitle
            )
            
            self.page_dashboard.card_critical_tasks.update(
                value=str(critical_tasks_count),
                subtitle="Requiring attention" if critical_tasks_count > 0 else "No delays"
            )
            
            # 3. Start Date: project start date from version record, formatted as "Dec, 15, 2025"
            start_date_str = "N/A"
            if versions_table in inspector.get_table_names():
                try:
                    start_date_query = f'SELECT project_start_datetime FROM "{versions_table}" WHERE version_id = :version_id'
                    start_date_result = pd.read_sql(text(start_date_query), self.engine, params={"version_id": max_version_id})
                    if not start_date_result.empty and pd.notna(start_date_result.iloc[0]['project_start_datetime']):
                        start_date_str_db = start_date_result.iloc[0]['project_start_datetime']
                        # Parse date string (format: "MM/DD/YYYY")
                        try:
                            fmt = "%m/%d/%Y"
                            start_date_dt = datetime.strptime(start_date_str_db, fmt)
                            start_date_str = format_date_as_month_day_year(start_date_dt.date())
                        except ValueError:
                            pass
                except Exception:
                    pass
            
            self.page_dashboard.card_start_date.update(
                value=start_date_str,
                subtitle=""
            )
            
            # 4. Forecast Completion: project finish date from solution table (max version)
            forecast_completion_str = "N/A"
            if 'Installation_Finish' in df_sol.columns:
                try:
                    install_finish_col = df_sol['Installation_Finish'].dropna()
                    if len(install_finish_col) > 0:
                        finish_time_idx = int(install_finish_col.max())
                        finish_date = idx_to_date(finish_time_idx)
                        if finish_date:
                            forecast_completion_str = format_date_as_month_day_year(finish_date)
                except Exception:
                    pass
            
            self.page_dashboard.card_forecast_completion.update(
                value=forecast_completion_str,
                subtitle=""
            )
            
            # 5. Factory Storage Modules: modules currently in factory storage
            factory_storage_count = 0
            if current_time_idx is not None:
                if 'Factory_Wait_Start' in df_sol.columns and 'Factory_Wait_Duration' in df_sol.columns:
                    for _, row in df_sol.iterrows():
                        factory_wait_start = row.get('Factory_Wait_Start')
                        factory_wait_duration = row.get('Factory_Wait_Duration')
                        if pd.notna(factory_wait_start) and pd.notna(factory_wait_duration):
                            factory_wait_start_idx = int(factory_wait_start)
                            factory_wait_end_idx = factory_wait_start_idx + int(factory_wait_duration)
                            # Module is in factory storage if current time is within wait period
                            if factory_wait_start_idx <= current_time_idx < factory_wait_end_idx:
                                factory_storage_count += 1
            
            self.page_dashboard.card_factory_storage.update(
                value=str(factory_storage_count),
                subtitle="Ready for transport"
            )
            
            # 6. Site Storage Modules: modules currently in site storage
            site_storage_count = 0
            if current_time_idx is not None:
                if 'Onsite_Wait_Start' in df_sol.columns and 'Onsite_Wait_Duration' in df_sol.columns:
                    for _, row in df_sol.iterrows():
                        onsite_wait_start = row.get('Onsite_Wait_Start')
                        onsite_wait_duration = row.get('Onsite_Wait_Duration')
                        if pd.notna(onsite_wait_start) and pd.notna(onsite_wait_duration):
                            onsite_wait_start_idx = int(onsite_wait_start)
                            onsite_wait_end_idx = onsite_wait_start_idx + int(onsite_wait_duration)
                            # Module is in site storage if current time is within wait period
                            if onsite_wait_start_idx <= current_time_idx < onsite_wait_end_idx:
                                site_storage_count += 1
            
            self.page_dashboard.card_site_storage.update(
                value=str(site_storage_count),
                subtitle="Awaiting installation"
            )
            
        except Exception as e:
            print(f"Error updating dashboard metrics: {e}")
            import traceback
            traceback.print_exc()
    
    def on_delete_version_clicked(self):
        """
        Handle delete version button click.
        Deletes the currently selected version and all its associated data.
        """
        if self.current_project_id is None:
            QMessageBox.warning(self, "Error", "No project selected.")
            return
        
        # Get currently selected version
        if not hasattr(self.page_schedule, 'version_combo') or self.page_schedule.version_combo.count() == 0:
            QMessageBox.warning(self, "Error", "No version selected.")
            return
        
        current_index = self.page_schedule.version_combo.currentIndex()
        if current_index < 0:
            QMessageBox.warning(self, "Error", "No version selected.")
            return
        
        version_id = self.page_schedule.version_id_map.get(current_index)
        if version_id is None:
            QMessageBox.warning(self, "Error", "Invalid version selected.")
            return
        
        # Get version number for display
        versions_table = self.mgr.optimization_versions_table_name(self.current_project_id)
        try:
            with self.engine.begin() as conn:
                version_query = text(f'SELECT version_number FROM "{versions_table}" WHERE version_id = :version_id')
                version_number = conn.execute(version_query, {"version_id": version_id}).scalar()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to get version information: {str(e)}")
            return
        
        if version_number is None:
            QMessageBox.warning(self, "Error", "Version not found.")
            return
        
        # Confirm deletion
        reply = QMessageBox.question(
            self,
            "Delete Version",
            f"Are you sure you want to delete Version {version_number}?\n\n"
            "This will delete:\n"
            "- All solution data for this version\n"
            "- All summary data for this version\n"
            "- All delay records associated with this version\n\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                # Delete the version
                success = self.mgr.delete_version(self.current_project_id, version_id)
                
                if success:
                    # Get the index of the previous version (or next version if no previous)
                    # Calculate this before reloading since the combo will be cleared
                    previous_index = current_index - 1 if current_index > 0 else (current_index + 1 if current_index < self.page_schedule.version_combo.count() - 1 else -1)
                    
                    # Reload version list without auto_load to manually control which version is selected
                    self.page_schedule.load_version_list(self.engine, self.current_project_id, auto_load=False)
                    
                    # If there are still versions, select the one at previous_index (or first if previous was deleted)
                    if self.page_schedule.version_combo.count() > 0:
                        if previous_index >= 0 and previous_index < self.page_schedule.version_combo.count():
                            self.page_schedule.version_combo.setCurrentIndex(previous_index)
                        else:
                            self.page_schedule.version_combo.setCurrentIndex(0)
                        # Manually trigger version change to load the selected version
                        self.page_schedule._on_version_changed()
                    
                    QMessageBox.information(
                        self,
                        "Version Deleted",
                        f"Version {version_number} has been deleted successfully."
                    )
                else:
                    QMessageBox.critical(
                        self,
                        "Delete Failed",
                        f"Failed to delete Version {version_number}. Please check the console for details."
                    )
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Error",
                    f"An error occurred while deleting the version:\n{str(e)}"
                )
                import traceback
                traceback.print_exc()

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


