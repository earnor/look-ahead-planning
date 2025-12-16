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
import pandas as pd
from sqlalchemy import create_engine, text, inspect
from planning_tool.datamanager import ScheduleDataManager
from planning_tool.model import PrefabScheduler, estimate_time_horizon
from planning_tool.rescheduler import load_delays_from_db, TaskStateIdentifier, DelayApplier, FixedConstraintsBuilder
from datetime import datetime, time, timedelta
import traceback
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
                page_dashboard.pageRequested.connect(self.switch_page)
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

        try:
            page_comparison = ComparisonPage()
        except NameError:
            page_comparison = QLabel("Comparison"); page_comparison.setAlignment(Qt.AlignmentFlag.AlignCenter)

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
            # Store reference to MainWindow in SchedulePage for delay saving
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
            
            T = estimate_time_horizon(start_date, end_date, hours_per_day=hours_per_day)

            # Check if we have pending delays (Phase 5.2 & 6: Re-optimization workflow)
            delay_table = ScheduleDataManager.delay_updates_table_name(self.current_project_id)
            versions_table = ScheduleDataManager.optimization_versions_table_name(self.current_project_id)
            
            # Check for delays without version_id (pending delays)
            with self.engine.begin() as conn:
                pending_delays_query = f'SELECT COUNT(*) FROM "{delay_table}" WHERE version_id IS NULL'
                pending_count = conn.execute(text(pending_delays_query)).scalar()
            
            is_reoptimization = pending_count > 0
            
            if is_reoptimization:
                pending_delay_map = {}
                modules_with_delay = set()
                # Phase 6: Re-optimization workflow
                # 1. Load pending delays
                delays = load_delays_from_db(self.engine, self.current_project_id, version_id=None)
                
                if not delays:
                    QMessageBox.warning(self, "No Delays", "No pending delays found.")
                    return
                
                # Aggregate pending delays by module and phase for display/highlight
                for d in delays:
                    key = (str(d.module_id), str(d.phase).upper())
                    pending_delay_map[key] = pending_delay_map.get(key, 0) + float(d.delay_hours or 0)
                    modules_with_delay.add(str(d.module_id))
                
                # Get the latest solution to use as base
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
                        QMessageBox.warning(self, "No Base Solution", "No previous solution found. Please run initial optimization first.")
                        return
                    # Safety: ensure we actually got a DataFrame
                    if not isinstance(df_base_solution, pd.DataFrame):
                        QMessageBox.critical(self, "Error", "Base solution is invalid (not a DataFrame).")
                        return
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to load base solution: {str(e)}")
                    return
                
                if df_base_solution.empty:
                    QMessageBox.warning(self, "No Base Solution", "No previous solution found. Please run initial optimization first.")
                    return
                
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
                
                # Determine current_time (actual current time for re-optimization)
                # Use system time or allow future extension for simulation time
                current_datetime = datetime.now()
                
                # Convert current_datetime to time index
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
                    else:
                        current_time = len(working_calendar_slots) - 1
                
                # 2. Identify task states (based on current_time)
                state_identifier = TaskStateIdentifier(df_base_solution, current_time, working_calendar_slots)
                task_states = state_identifier.identify_all_states()
                
                # 3. Apply delays
                delay_applier = DelayApplier(df_base_solution, delays, task_states)
                modified_solution_df = delay_applier.apply_delays()
                
                # 4. Update D, d, L dictionaries with delayed durations
                # This ensures the optimizer uses the correct durations for tasks with DURATION_EXTENSION
                # Only COMPLETED tasks keep original durations (they're already finished)
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
                fixed_builder = FixedConstraintsBuilder(
                    task_states, 
                    current_time, 
                    modified_solution_df, 
                    working_calendar_slots,
                    df_base_solution
                )
                fixed_constraints = fixed_builder.build_fixed_constraints()
                
                # 6. Create new version record (Phase 5.2)
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
                    
                    # Get delay IDs for pending delays
                    delay_ids_query = f'SELECT delay_id FROM "{delay_table}" WHERE version_id IS NULL'
                    delay_ids = [str(row[0]) for row in conn.execute(text(delay_ids_query)).fetchall()]
                    delay_ids_str = ','.join(delay_ids) if delay_ids else None
                    
                    # Insert new version record (use current_time as reoptimize_from_time)
                    insert_version_query = text(f'''
                        INSERT INTO "{versions_table}" 
                        (version_number, base_version_id, reoptimize_from_time, delay_ids)
                        VALUES (:version_number, :base_version_id, :reoptimize_from_time, :delay_ids)
                    ''')
                    conn.execute(insert_version_query, {
                        "version_number": new_version_number,
                        "base_version_id": base_version_id,
                        "reoptimize_from_time": current_time,
                        "delay_ids": delay_ids_str
                    })
                    
                    # Get the new version_id
                    new_version_id_query = text(f'SELECT version_id FROM "{versions_table}" WHERE version_number = :version_number')
                    new_version_id = conn.execute(new_version_id_query, {"version_number": new_version_number}).scalar()
                    
                    # Update delay records to link to new version
                    update_delays_query = text(f'UPDATE "{delay_table}" SET version_id = :version_id WHERE version_id IS NULL')
                    conn.execute(update_delays_query, {"version_id": new_version_id})
                
                # 7. Build and solve model with fixed constraints
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
                    reoptimize_from_time=current_time
                )
                
                status = scheduler.solve()
                
                # 8. Save results with version_id (Phase 6.3)
                scheduler.save_results_to_db(
                    self.engine,
                    self.current_project_id,
                    module_id_mapping=index_to_id,
                    version_id=new_version_id
                )
                
                # Update version record with optimization results
                solution = scheduler.get_solution_dict()
                if solution:
                    with self.engine.begin() as conn:
                        update_version_query = text(f'''
                            UPDATE "{versions_table}" 
                            SET objective_value = :objective_value, status = :status
                            WHERE version_id = :version_id
                        ''')
                        conn.execute(update_version_query, {
                            "objective_value": solution.get('objective'),
                            "status": solution.get('status'),
                            "version_id": new_version_id
                        })
                
                QMessageBox.information(self, "Re-optimization Complete", 
                    f"Re-optimization completed successfully.\nVersion: {new_version_number}\nCurrent time: {current_time}")
            else:
                # Initial optimization (existing logic)
                # 4) build and solve model
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
            status = scheduler.solve()

            # 5) save results to DB for later post-processing, preserving real Module IDs
            scheduler.save_results_to_db(
                self.engine,
                self.current_project_id,
                module_id_mapping=index_to_id
            )

            # 6) load solution table and map indices to real-world schedule using working calendar
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
                
                current_time = datetime.now() if use_system_time else datetime.now()  # For now always use system time, may change later

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
            tb = traceback.format_exc()
            print(tb)
            QMessageBox.critical(self, "Error in Calculate", f"{e}\n\n{tb}")

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
            # Update sidebar button states
            self._update_sidebar_selection(name)
    
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


