"""
Dynamic Rescheduling Module

This module handles delay detection and re-optimization of schedules.
It identifies task states, applies delays, and builds fixed constraints for re-optimization.
"""
from typing import Dict, List, Tuple, Optional, Set
from datetime import datetime
from dataclasses import dataclass
import pandas as pd
from sqlalchemy import Engine, text
from .datamanager import ScheduleDataManager
  


@dataclass
class TaskState:
    """Represents the state of a task at a given time"""
    module_id: str
    module_index: int
    phase: str  # "FABRICATION", "TRANSPORT", "INSTALLATION"
    status: str  # "COMPLETED", "IN_PROGRESS", "NOT_STARTED"
    start_time: Optional[int]  # Time index when phase starts (planned)
    finish_time: Optional[int]  # Time index when phase finishes (planned)
    progress: float  # 0.0 to 1.0, how much of the phase is completed
    actual_start_time: Optional[int] = None  # Actual start time if started (for IN_PROGRESS)


@dataclass
class DelayInfo:
    """Represents a delay record"""
    module_id: str
    delay_type: str  # "DURATION_EXTENSION" or "START_POSTPONEMENT"
    phase: str  # "FABRICATION", "TRANSPORT", "INSTALLATION"
    delay_hours: float
    detected_at_time: int  # Time index τ when delay was detected
    detected_at_datetime: str
    reason: Optional[str] = None


class TaskStateIdentifier:
    """Identifies task states from solution data based on current_time"""
    
    def __init__(self, solution_df: pd.DataFrame, current_time: int,
                 working_calendar_slots: List[datetime], current_datetime: Optional[datetime] = None):
        """
        Args:
            solution_df: DataFrame with columns Module_ID, Installation_Start, 
                        Production_Start, Arrival_Time, etc.
            current_time: Time index representing the actual current time (re-optimization starts from here)
            working_calendar_slots: List of datetime objects mapping time indices to real times
            current_datetime: Optional datetime representing current time (if provided, will be used directly instead of converting from current_time)
        """
        self.solution_df = solution_df
        self.current_time = current_time
        self.working_calendar_slots = working_calendar_slots
        # Use provided current_datetime directly if available, otherwise convert from current_time
        if current_datetime is not None:
            self.current_datetime = current_datetime
            print(f"[DEBUG TaskStateIdentifier] Using provided current_datetime={current_datetime}")
        else:
            # Convert current_time (time index) to datetime for comparison
            self.current_datetime = self._index_to_datetime(current_time) if current_time > 0 else None
            print(f"[DEBUG TaskStateIdentifier] Converted from current_time={current_time} to current_datetime={self.current_datetime}")
        self._time_to_index_map = self._build_time_map()
    
    def _build_time_map(self) -> Dict[datetime, int]:
        """
        Build mapping from datetime to time index.
        
        working_calendar_slots structure:
        - slots[0] = None (placeholder, unused)
        - slots[1] = first working slot (time index 1)
        - slots[2] = second working slot (time index 2)
        - ...
        
        So idx directly maps to time index (skip idx=0).
        """
        return {dt: idx for idx, dt in enumerate(self.working_calendar_slots) if dt is not None}
    
    def _datetime_to_index(self, dt: datetime) -> Optional[int]:
        """
        Convert datetime to time index.
        
        Skip index 0 (placeholder), start from index 1.
        """
        # Find closest time index, skip index 0 (placeholder)
        for idx, slot in enumerate(self.working_calendar_slots):
            if slot is not None and slot >= dt:
                return idx  # idx directly maps to time index
        return None
    
    def _index_to_datetime(self, idx: int) -> Optional[datetime]:
        """
        Convert time index to datetime.
        
        working_calendar_slots structure:
        - slots[0] = None (placeholder, unused)
        - slots[1] = first working slot (time index 1)
        - slots[2] = second working slot (time index 2)
        - ...
        
        So time index idx directly maps to slots[idx].
        """
        if 1 <= idx < len(self.working_calendar_slots):
            return self.working_calendar_slots[idx]
        return None
    
    def identify_all_states(self) -> Dict[str, List[TaskState]]:
        """
        Identify states for all modules and all phases.
        Returns: {module_id: [TaskState for FABRICATION, TRANSPORT, INSTALLATION]}
        """
        states = {}
        
        for _, row in self.solution_df.iterrows():
            module_id = str(row['Module_ID'])
            module_index = int(row.get('Module_Index', 0))
            
            # Get timing information
            prod_start_idx = row.get('Production_Start')
            prod_duration = row.get('Production_Duration', 0)
            transport_duration = row.get('Transport_Duration', 0)
            install_start_idx = row.get('Installation_Start')
            install_duration = row.get('Installation_Duration', 0)
            transport_start_idx = row.get('Transport_Start')
            
            # Calculate phase timings
            fab_start = prod_start_idx
            fab_finish = fab_start + prod_duration - 1 if fab_start else None
            
            # Transport start is directly from Transport_Start column
            transport_start = transport_start_idx
            
            # Get arrival time from database (if exists)
            # Arrival_Time is the time when transport finishes (module arrives at site)
            transport_finish = row.get('Arrival_Time')
            # If Arrival_Time doesn't exist, calculate from Transport_Start + Transport_Duration
            if transport_finish is None and transport_start is not None:
                transport_finish = transport_start + transport_duration
            
            install_start = install_start_idx
            install_finish = install_start + install_duration - 1 if install_start else None
            
            # Identify states for each phase
            module_states = []
            
            # Fabrication phase
            fab_state = self._identify_phase_state(
                module_id, module_index, "FABRICATION",
                fab_start, fab_finish, prod_duration
            )
            if fab_state:
                module_states.append(fab_state)
            
            # Transport phase
            transport_state = self._identify_phase_state(
                module_id, module_index, "TRANSPORT",
                transport_start, transport_finish, transport_duration
            )
            if transport_state:
                module_states.append(transport_state)
            
            # Installation phase
            install_state = self._identify_phase_state(
                module_id, module_index, "INSTALLATION",
                install_start, install_finish, install_duration
            )
            if install_state:
                module_states.append(install_state)
            
            states[module_id] = module_states
        
        return states
    
    def _identify_phase_state(self, module_id: str, module_index: int, 
                              phase: str, start_idx: Optional[int], 
                              finish_idx: Optional[int], duration: int) -> Optional[TaskState]:
        """Identify state for a single phase based on current_time"""
        
        # Convert time indices to datetimes
        start_dt = self._index_to_datetime(start_idx)
        finish_dt = self._index_to_datetime(finish_idx) if finish_idx else None
        
        # Debug: print time conversion for state identification
        if module_id and (module_id.startswith("VS-02-21") or phase == "FABRICATION"):
            print(f"[DEBUG _identify_phase_state] {module_id} {phase}: start_idx={start_idx} -> {start_dt}, finish_idx={finish_idx} -> {finish_dt}, current_datetime={self.current_datetime}")
        
        # Determine status based on current_time (actual current time)
        if self.current_datetime is None:
            # If current_datetime is not available, treat all as not started
            return TaskState(
                module_id=module_id,
                module_index=module_index,
                phase=phase,
                status="NOT_STARTED",
                start_time=start_idx,
                finish_time=finish_idx,
                progress=0.0
            )
        
        # Determine status at current_time
        if finish_dt and finish_dt < self.current_datetime:
            # Completed by current_time
            status = "COMPLETED"
            progress = 1.0
            actual_start = None  # Not needed for completed tasks
        elif start_dt and start_dt <= self.current_datetime:
            # In progress at current_time
            status = "IN_PROGRESS"
            actual_start = start_idx  # Assume started at planned start (could be refined with actual records)
            if finish_dt:
                total_duration = (finish_dt - start_dt).total_seconds() / 3600
                elapsed = (self.current_datetime - start_dt).total_seconds() / 3600
                if total_duration > 0:
                    progress = min(1.0, max(0.0, elapsed / total_duration))
                else:
                    progress = 0.0  # avoid division by zero when duration invalid
            else:
                progress = 0.5  # Unknown progress
        else:
            # Not started at current_time
            status = "NOT_STARTED"
            progress = 0.0
            actual_start = None
        
        return TaskState(
            module_id=module_id,
            module_index=module_index,
            phase=phase,
            status=status,
            start_time=start_idx,
            finish_time=finish_idx,
            progress=progress,
            actual_start_time=actual_start
        )


class DelayApplier:
    """Applies delays to schedules and propagates effects"""
    
    def __init__(self, solution_df: pd.DataFrame, delays: List[DelayInfo],
                 task_states: Dict[str, List[TaskState]]):
        """
        Args:
            solution_df: Original solution DataFrame
            delays: List of delay records
            task_states: Task states identified by TaskStateIdentifier
        """
        self.solution_df = solution_df.copy()
        self.delays = delays
        self.task_states = task_states
        self._module_id_to_index = {
            str(row['Module_ID']): int(row.get('Module_Index', 0))
            for _, row in self.solution_df.iterrows()
        }
    
    def apply_delays(self) -> pd.DataFrame:
        """
        Apply delays to the solution DataFrame.
        Returns modified DataFrame with updated timings.
        """
        modified_df = self.solution_df.copy()
        
        # Initialize earliest start columns if they don't exist
        if 'Earliest_Production_Start' not in modified_df.columns:
            modified_df['Earliest_Production_Start'] = None
        if 'Earliest_Transport_Start' not in modified_df.columns:
            modified_df['Earliest_Transport_Start'] = None
        if 'Earliest_Installation_Start' not in modified_df.columns:
            modified_df['Earliest_Installation_Start'] = None
        
        print(f"[DEBUG DelayApplier] Applying {len(self.delays)} delays")
        for delay in self.delays:
            module_id = delay.module_id
            if module_id not in self._module_id_to_index:
                continue
            
            # Get current row
            row_idx = modified_df[modified_df['Module_ID'] == module_id].index
            if len(row_idx) == 0:
                continue
            idx = row_idx[0]
            
            # Apply delay based on type and phase
            if delay.delay_type == "DURATION_EXTENSION":
                self._apply_duration_extension(modified_df, idx, delay)
            elif delay.delay_type == "START_POSTPONEMENT":
                self._apply_start_postponement(modified_df, idx, delay)
        
        # Note: We don't need to manually propagate delays through precedence relationships.
        # The optimizer will handle precedence constraints (E) automatically when re-optimizing.
        # We only set lower bounds (Earliest_*), and the optimizer will find a solution
        # that satisfies both precedence constraints and lower bounds.
        
        return modified_df
    
    def _apply_duration_extension(self, df: pd.DataFrame, idx: int, delay: DelayInfo):
        """
        Apply duration extension delay.
        Note: This should only affect tasks that are not yet completed at time tau.
        However, we apply it to the DataFrame regardless, and FixedConstraintsBuilder
        will only use the updated duration if the task is not completed.
        """
        module_id = df.at[idx, 'Module_ID']
        # Check task state for this module and phase
        module_states = self.task_states.get(str(module_id), [])
        task_state = None
        for state in module_states:
            if state.phase == delay.phase:
                task_state = state
                break
        
        print(f"[DEBUG DelayApplier] Applying DURATION_EXTENSION to {module_id}, phase={delay.phase}, delay={delay.delay_hours}h, status={task_state.status if task_state else 'UNKNOWN'}")
        
        if delay.phase == "FABRICATION":
            current_duration = df.at[idx, 'Production_Duration']
            new_duration = current_duration + delay.delay_hours
            df.at[idx, 'Production_Duration'] = new_duration
            print(f"[DEBUG DelayApplier] {module_id} FABRICATION: {current_duration} -> {new_duration}")
        elif delay.phase == "TRANSPORT":
            current_duration = df.at[idx, 'Transport_Duration']
            new_duration = current_duration + delay.delay_hours
            df.at[idx, 'Transport_Duration'] = new_duration
            print(f"[DEBUG DelayApplier] {module_id} TRANSPORT: {current_duration} -> {new_duration}")
        elif delay.phase == "INSTALLATION":
            current_duration = df.at[idx, 'Installation_Duration']
            new_duration = current_duration + delay.delay_hours
            df.at[idx, 'Installation_Duration'] = new_duration
            print(f"[DEBUG DelayApplier] {module_id} INSTALLATION: {current_duration} -> {new_duration}")
    
    def _apply_start_postponement(self, df: pd.DataFrame, idx: int, delay: DelayInfo):
        """
        Apply start postponement delay.
        This sets a lower bound (earliest start time) rather than fixing the exact start time.
        """
        delay_hours = int(delay.delay_hours)
        
        def _safe_compare_lb(new_lb: int, current_lb) -> bool:
            """Safely compare new_lb with current_lb, handling None/NaN/string types"""
            if pd.isna(current_lb) or current_lb is None:
                return True  # No existing lower bound, use new one
            try:
                # Try to convert to numeric if it's a string or other type
                current_lb_num = pd.to_numeric(current_lb, errors='coerce')
                if pd.isna(current_lb_num):
                    return True  # Couldn't convert, use new one
                return new_lb > current_lb_num
            except (TypeError, ValueError):
                return True  # Conversion failed, use new one
        
        if delay.phase == "FABRICATION":
            base_start = df.at[idx, 'Production_Start']
            if pd.notna(base_start):
                new_lb = int(base_start) + delay_hours
                current_lb = df.at[idx, 'Earliest_Production_Start']
                if _safe_compare_lb(new_lb, current_lb):
                    df.at[idx, 'Earliest_Production_Start'] = new_lb
        elif delay.phase == "TRANSPORT":
            transport_start = df.at[idx, 'Transport_Start']
            if pd.notna(transport_start):
                new_lb = int(transport_start) + delay_hours
                current_lb = df.at[idx, 'Earliest_Transport_Start']
                if _safe_compare_lb(new_lb, current_lb):
                    df.at[idx, 'Earliest_Transport_Start'] = new_lb
        elif delay.phase == "INSTALLATION":
            install_start = df.at[idx, 'Installation_Start']
            if pd.notna(install_start):
                new_lb = int(install_start) + delay_hours
                current_lb = df.at[idx, 'Earliest_Installation_Start']
                if _safe_compare_lb(new_lb, current_lb):
                    df.at[idx, 'Earliest_Installation_Start'] = new_lb


class FixedConstraintsBuilder:
    """Builds fixed constraints for re-optimization"""
    
    def __init__(self, task_states: Dict[str, List[TaskState]], 
                 current_time: int, solution_df: pd.DataFrame, 
                 working_calendar_slots: List[datetime],
                 original_solution_df: Optional[pd.DataFrame] = None):
        """
        Args:
            task_states: Task states identified by TaskStateIdentifier
            current_time: Time index representing the actual current time
            solution_df: Solution DataFrame (may contain Earliest_* columns after delays are applied)
            working_calendar_slots: List of datetime objects mapping time indices to real times
            original_solution_df: Original solution DataFrame before delays are applied (for completed tasks)
        """
        self.task_states = task_states
        self.current_time = current_time
        self.solution_df = solution_df
        self.working_calendar_slots = working_calendar_slots
        # Store original solution for completed tasks (to get original durations)
        self.original_solution_df = original_solution_df if original_solution_df is not None else solution_df
        self._module_id_to_index = {
            str(row['Module_ID']): int(row.get('Module_Index', 0))
            for _, row in self.solution_df.iterrows()
        }
        
    def _index_to_datetime(self, idx: int) -> Optional[datetime]:
        """
        Convert time index to datetime.
        
        working_calendar_slots structure:
        - slots[0] = None (placeholder, unused)
        - slots[1] = first working slot (time index 1)
        - slots[2] = second working slot (time index 2)
        - ...
        
        So time index idx directly maps to slots[idx].
        """
        if 1 <= idx < len(self.working_calendar_slots):
            return self.working_calendar_slots[idx]
        return None
    
    def _datetime_to_index(self, dt: datetime) -> Optional[int]:
        """
        Convert datetime to time index.
        
        Skip index 0 (placeholder), start from index 1.
        """
        # Find closest time index, skip index 0 (placeholder)
        for idx, slot in enumerate(self.working_calendar_slots):
            if slot is not None and slot >= dt:
                return idx  # idx directly maps to time index
        return None
    
    def build_fixed_constraints(self) -> Dict[str, any]:
        """
        Build fixed constraints for re-optimization based on current_time.
        Returns a dictionary with:
        - fixed_installation_starts: {module_index: start_time}
        - fixed_production_starts: {module_index: start_time}
        - fixed_arrival_times: {module_index: arrival_time}
        - fixed_durations: {module_index: {phase: duration}}
        - earliest_production_starts: {module_index: earliest_start_time} (lower bounds)
        - earliest_transport_starts: {module_index: earliest_start_time} (lower bounds)
        - earliest_installation_starts: {module_index: earliest_start_time} (lower bounds)
        
        Logic:
        - COMPLETED: Fix start time, duration = original (not used by optimizer)
        - IN_PROGRESS: Fix start time, duration = remaining duration (considering DURATION_EXTENSION)
        - NOT_STARTED: No fixed start (may have lower bound from START_POSTPONEMENT), 
                       duration = modified total duration (considering DURATION_EXTENSION)
        """
        fixed_installation_starts = {}
        fixed_production_starts = {}
        fixed_arrival_times = {}
        fixed_durations = {}
        earliest_production_starts = {}
        earliest_transport_starts = {}
        earliest_installation_starts = {}
        
        for module_id, states in self.task_states.items():
            if module_id not in self._module_id_to_index:
                continue
            
            module_index = self._module_id_to_index[module_id]
            
            # Get solution row (after delays applied)
            row = self.solution_df[self.solution_df['Module_ID'] == module_id]
            if len(row) == 0:
                continue
            row = row.iloc[0]
            
            # Get original solution row (for completed tasks, use original durations)
            original_row = self.original_solution_df[self.original_solution_df['Module_ID'] == module_id]
            if len(original_row) == 0:
                original_row = row
            else:
                original_row = original_row.iloc[0]
            
            # Check each phase
            for state in states:
                if state.phase == "FABRICATION":
                    self._handle_fabrication_phase(
                        state, module_index, row, original_row,
                        fixed_production_starts, fixed_durations
                    )
                    # Extract Earliest_Production_Start lower bound for NOT_STARTED tasks
                    if state.status == "NOT_STARTED" and module_index not in fixed_production_starts:
                        earliest_prod_val = row.get('Earliest_Production_Start')
                        if pd.notna(earliest_prod_val) and earliest_prod_val is not None:
                            # Earliest_* columns contain time indices (int), not datetime
                            # If it's already an int, use it directly; otherwise convert from datetime
                            if isinstance(earliest_prod_val, (int, float)):
                                earliest_prod_idx = int(earliest_prod_val)
                            elif isinstance(earliest_prod_val, datetime):
                                earliest_prod_idx = self._datetime_to_index(earliest_prod_val)
                            else:
                                earliest_prod_idx = None
                            
                            if earliest_prod_idx is not None and earliest_prod_idx > 0:
                                earliest_production_starts[module_index] = earliest_prod_idx
                                print(f"[DEBUG FixedConstraintsBuilder] Module {module_id} (index {module_index}) FABRICATION: Earliest_Production_Start = {earliest_prod_val} -> time_index {earliest_prod_idx}")
                elif state.phase == "TRANSPORT":
                    self._handle_transport_phase(
                        state, module_index, row, original_row,
                        fixed_arrival_times, fixed_durations
                    )
                    # Extract Earliest_Transport_Start lower bound for NOT_STARTED tasks
                    # Note: Transport start is not directly fixed, but arrival time may be
                    # We use Earliest_Transport_Start to constrain when transport can start
                    if state.status == "NOT_STARTED" and module_index not in fixed_arrival_times:
                        earliest_trans_val = row.get('Earliest_Transport_Start')
                        if pd.notna(earliest_trans_val) and earliest_trans_val is not None:
                            # Earliest_* columns contain time indices (int), not datetime
                            # If it's already an int, use it directly; otherwise convert from datetime
                            if isinstance(earliest_trans_val, (int, float)):
                                earliest_trans_idx = int(earliest_trans_val)
                            elif isinstance(earliest_trans_val, datetime):
                                earliest_trans_idx = self._datetime_to_index(earliest_trans_val)
                            else:
                                earliest_trans_idx = None
                            
                            if earliest_trans_idx is not None and earliest_trans_idx > 0:
                                earliest_transport_starts[module_index] = earliest_trans_idx
                                print(f"[DEBUG FixedConstraintsBuilder] Module {module_id} (index {module_index}) TRANSPORT: Earliest_Transport_Start = {earliest_trans_val} -> time_index {earliest_trans_idx}")
                elif state.phase == "INSTALLATION":
                    self._handle_installation_phase(
                        state, module_index, row, original_row,
                        fixed_installation_starts, fixed_durations
                    )
                    # Extract Earliest_Installation_Start lower bound for NOT_STARTED tasks
                    if state.status == "NOT_STARTED" and module_index not in fixed_installation_starts:
                        earliest_install_val = row.get('Earliest_Installation_Start')
                        if pd.notna(earliest_install_val) and earliest_install_val is not None:
                            # Earliest_* columns contain time indices (int), not datetime
                            # If it's already an int, use it directly; otherwise convert from datetime
                            if isinstance(earliest_install_val, (int, float)):
                                earliest_install_idx = int(earliest_install_val)
                            elif isinstance(earliest_install_val, datetime):
                                earliest_install_idx = self._datetime_to_index(earliest_install_val)
                            else:
                                earliest_install_idx = None
                            
                            if earliest_install_idx is not None and earliest_install_idx > 0:
                                earliest_installation_starts[module_index] = earliest_install_idx
                                print(f"[DEBUG FixedConstraintsBuilder] Module {module_id} (index {module_index}) INSTALLATION: Earliest_Installation_Start = {earliest_install_val} -> time_index {earliest_install_idx}")
        
        return {
            'fixed_installation_starts': fixed_installation_starts,
            'fixed_production_starts': fixed_production_starts,
            'fixed_arrival_times': fixed_arrival_times,
            'fixed_durations': fixed_durations,
            'earliest_production_starts': earliest_production_starts,
            'earliest_transport_starts': earliest_transport_starts,
            'earliest_installation_starts': earliest_installation_starts
        }
    
    def _handle_fabrication_phase(self, state: TaskState, module_index: int,
                                  row: pd.Series, original_row: pd.Series,
                                  fixed_production_starts: Dict[int, int],
                                  fixed_durations: Dict[int, Dict[str, float]]):
        """Handle fabrication phase constraints"""
        if state.status == "COMPLETED":
            # Fix start time (for historical record)
            if state.start_time:
                fixed_production_starts[module_index] = state.start_time
            # Duration = original (optimizer doesn't need it, but record for completeness)
            fixed_durations.setdefault(module_index, {})['FABRICATION'] = original_row.get('Production_Duration', 0)
            
        elif state.status == "IN_PROGRESS":
            # Fix start time (task has started)
            actual_start = state.actual_start_time if state.actual_start_time else state.start_time
            if actual_start:
                fixed_production_starts[module_index] = actual_start
            
            # Calculate remaining duration
            # Total duration after DURATION_EXTENSION (should include delay if applied)
            total_duration = row.get('Production_Duration', 0)
            original_duration = original_row.get('Production_Duration', 0)
            # Elapsed time = current_time - actual_start
            elapsed = max(0, self.current_time - actual_start) if actual_start else 0
            # Remaining duration = total - elapsed
            remaining_duration = max(0, total_duration - elapsed)
            print(f"[DEBUG FixedConstraintsBuilder] IN_PROGRESS FABRICATION module_index={module_index}: original={original_duration}, total_after_delay={total_duration}, elapsed={elapsed}, remaining={remaining_duration}")
            fixed_durations.setdefault(module_index, {})['FABRICATION'] = remaining_duration
            
        elif state.status == "NOT_STARTED":
            # No fixed start time (may have lower bound from START_POSTPONEMENT via Earliest_Production_Start)
            # Duration = modified total duration (after DURATION_EXTENSION)
            modified_duration = row.get('Production_Duration', 0)
            fixed_durations.setdefault(module_index, {})['FABRICATION'] = modified_duration
    
    def _handle_transport_phase(self, state: TaskState, module_index: int,
                               row: pd.Series, original_row: pd.Series,
                               fixed_arrival_times: Dict[int, int],
                               fixed_durations: Dict[int, Dict[str, float]]):
        """Handle transport phase constraints"""
        if state.status == "COMPLETED":
            # Fix arrival time (transport completed)
            arrival = row.get('Arrival_Time')
            if arrival is None and state.finish_time:
                arrival = state.finish_time
            if arrival:
                fixed_arrival_times[module_index] = arrival
            # Duration = original (optimizer doesn't need it)
            fixed_durations.setdefault(module_index, {})['TRANSPORT'] = original_row.get('Transport_Duration', 0)
            
        elif state.status == "IN_PROGRESS":
            # Fix start time indirectly via arrival time calculation
            # For transport, we fix the start time by ensuring the relationship is maintained
            # But we use remaining duration
            actual_start = state.actual_start_time if state.actual_start_time else state.start_time
            
            # Calculate remaining duration
            total_duration = row.get('Transport_Duration', 0)
            elapsed = max(0, self.current_time - actual_start) if actual_start else 0
            remaining_duration = max(0, total_duration - elapsed)
            fixed_durations.setdefault(module_index, {})['TRANSPORT'] = remaining_duration
            
            # Note: Transport start is fixed via precedence constraints and production finish
            # We don't directly fix transport start here, but the remaining duration ensures
            # the transport completion time is correctly calculated
            
        elif state.status == "NOT_STARTED":
            # No fixed start time
            # Duration = modified total duration (after DURATION_EXTENSION)
            modified_duration = row.get('Transport_Duration', 0)
            fixed_durations.setdefault(module_index, {})['TRANSPORT'] = modified_duration
    
    def _handle_installation_phase(self, state: TaskState, module_index: int,
                                  row: pd.Series, original_row: pd.Series,
                                  fixed_installation_starts: Dict[int, int],
                                  fixed_durations: Dict[int, Dict[str, float]]):
        """Handle installation phase constraints"""
        if state.status == "COMPLETED":
            # Fix start time (for historical record)
            if state.start_time:
                fixed_installation_starts[module_index] = state.start_time
            # Duration = original (optimizer doesn't need it)
            fixed_durations.setdefault(module_index, {})['INSTALLATION'] = original_row.get('Installation_Duration', 0)
            
        elif state.status == "IN_PROGRESS":
            # Fix start time (task has started)
            actual_start = state.actual_start_time if state.actual_start_time else state.start_time
            if actual_start:
                fixed_installation_starts[module_index] = actual_start
            
            # Calculate remaining duration
            total_duration = row.get('Installation_Duration', 0)
            elapsed = max(0, self.current_time - actual_start) if actual_start else 0
            remaining_duration = max(0, total_duration - elapsed)
            fixed_durations.setdefault(module_index, {})['INSTALLATION'] = remaining_duration
            
        elif state.status == "NOT_STARTED":
            # No fixed start time (may have lower bound from START_POSTPONEMENT via Earliest_Installation_Start)
            # Duration = modified total duration (after DURATION_EXTENSION)
            modified_duration = row.get('Installation_Duration', 0)
            fixed_durations.setdefault(module_index, {})['INSTALLATION'] = modified_duration


def load_delays_from_db(engine: Engine, project_id: int, version_id: Optional[int] = None) -> List[DelayInfo]:
    """Load delay records from database"""
    delay_table = ScheduleDataManager.delay_updates_table_name(project_id)
    
    
    query = f'SELECT * FROM "{delay_table}"'
    if version_id:
        query += f' WHERE version_id = {version_id}'
    
    with engine.begin() as conn:
        df = pd.read_sql(query, conn)
    
    delays = []
    for _, row in df.iterrows():
        delays.append(DelayInfo(
            module_id=str(row['module_id']),
            delay_type=str(row['delay_type']),
            phase=str(row['phase']),
            delay_hours=float(row['delay_hours']),
            detected_at_time=int(row['detected_at_time']),
            detected_at_datetime=str(row['detected_at_datetime']),
            reason=row.get('reason')
        ))
    
    return delays

