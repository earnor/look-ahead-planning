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
    status: str  # "COMPLETED", "IN_PROGRESS", "UPCOMING"
    start_time: Optional[int]  # Time index when phase starts
    finish_time: Optional[int]  # Time index when phase finishes
    progress: float  # 0.0 to 1.0, how much of the phase is completed


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
    """Identifies task states from solution data"""
    
    def __init__(self, solution_df: pd.DataFrame, tau: int,
                 working_calendar_slots: List[datetime]):
        """
        Args:
            solution_df: DataFrame with columns Module_ID, Installation_Start, 
                        Production_Start, Arrival_Time, etc.
            tau: Time index when delay was detected (re-optimization starts from here)
            working_calendar_slots: List of datetime objects mapping time indices to real times
        """
        self.solution_df = solution_df
        self.tau = tau
        self.working_calendar_slots = working_calendar_slots
        # Convert tau (time index) to datetime for comparison
        self.tau_datetime = self._index_to_datetime(tau) if tau > 0 else None
        self._time_to_index_map = self._build_time_map()
    
    def _build_time_map(self) -> Dict[datetime, int]:
        """Build mapping from datetime to time index"""
        return {dt: idx + 1 for idx, dt in enumerate(self.working_calendar_slots)}
    
    def _datetime_to_index(self, dt: datetime) -> Optional[int]:
        """Convert datetime to time index"""
        # Find closest time index
        for idx, slot in enumerate(self.working_calendar_slots):
            if slot >= dt:
                return idx + 1
        return None
    
    def _index_to_datetime(self, idx: int) -> Optional[datetime]:
        """Convert time index to datetime"""
        if 1 <= idx <= len(self.working_calendar_slots):
            return self.working_calendar_slots[idx - 1]
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
        """Identify state for a single phase"""
        if start_idx is None:
            return TaskState(
                module_id=module_id,
                module_index=module_index,
                phase=phase,
                status="UPCOMING",
                start_time=None,
                finish_time=None,
                progress=0.0
            )
        
        # Convert time indices to datetimes
        start_dt = self._index_to_datetime(start_idx)
        finish_dt = self._index_to_datetime(finish_idx) if finish_idx else None
        
        if start_dt is None:
            return None
        
        # Determine status based on tau (delay detection time), not current time
        if self.tau_datetime is None:
            # If tau_datetime is not available, treat all as upcoming
            return TaskState(
                module_id=module_id,
                module_index=module_index,
                phase=phase,
                status="UPCOMING",
                start_time=start_idx,
                finish_time=finish_idx,
                progress=0.0
            )
        
        # Determine status at time τ
        if finish_dt and finish_dt < self.tau_datetime:
            # Completed by time τ
            status = "COMPLETED"
            progress = 1.0
        elif start_dt and start_dt <= self.tau_datetime:
            # In progress at time τ
            status = "IN_PROGRESS"
            if finish_dt:
                total_duration = (finish_dt - start_dt).total_seconds() / 3600
                elapsed = (self.tau_datetime - start_dt).total_seconds() / 3600
                progress = min(1.0, max(0.0, elapsed / total_duration))
            else:
                progress = 0.5  # Unknown progress
        else:
            # Upcoming at time τ
            status = "UPCOMING"
            progress = 0.0
        
        return TaskState(
            module_id=module_id,
            module_index=module_index,
            phase=phase,
            status=status,
            start_time=start_idx,
            finish_time=finish_idx,
            progress=progress
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
        if delay.phase == "FABRICATION":
            current_duration = df.at[idx, 'Production_Duration']
            df.at[idx, 'Production_Duration'] = current_duration + delay.delay_hours
        elif delay.phase == "TRANSPORT":
            current_duration = df.at[idx, 'Transport_Duration']
            df.at[idx, 'Transport_Duration'] = current_duration + delay.delay_hours
        elif delay.phase == "INSTALLATION":
            current_duration = df.at[idx, 'Installation_Duration']
            df.at[idx, 'Installation_Duration'] = current_duration + delay.delay_hours
    
    def _apply_start_postponement(self, df: pd.DataFrame, idx: int, delay: DelayInfo):
        """
        Apply start postponement delay.
        This sets a lower bound (earliest start time) rather than fixing the exact start time.
        """
        delay_hours = int(delay.delay_hours)
        if delay.phase == "FABRICATION":
            base_start = df.at[idx, 'Production_Start']
            if pd.notna(base_start):
                new_lb = base_start + delay_hours
                current_lb = df.at[idx, 'Earliest_Production_Start']
                if pd.isna(current_lb) or new_lb > current_lb:
                    df.at[idx, 'Earliest_Production_Start'] = new_lb
        elif delay.phase == "TRANSPORT":
            transport_start = df.at[idx, 'Transport_Start']
            if pd.notna(transport_start):
                new_lb = transport_start + delay_hours
                current_lb = df.at[idx, 'Earliest_Transport_Start']
                if pd.isna(current_lb) or new_lb > current_lb:
                    df.at[idx, 'Earliest_Transport_Start'] = new_lb
        elif delay.phase == "INSTALLATION":
            install_start = df.at[idx, 'Installation_Start']
            if pd.notna(install_start):
                new_lb = install_start + delay_hours
                current_lb = df.at[idx, 'Earliest_Installation_Start']
                if pd.isna(current_lb) or new_lb > current_lb:
                    df.at[idx, 'Earliest_Installation_Start'] = new_lb


class FixedConstraintsBuilder:
    """Builds fixed constraints for re-optimization"""
    
    def __init__(self, task_states: Dict[str, List[TaskState]], 
                 tau: int, solution_df: pd.DataFrame, original_solution_df: Optional[pd.DataFrame] = None):
        """
        Args:
            task_states: Task states identified by TaskStateIdentifier
            tau: Time index when delay was detected
            solution_df: Solution DataFrame (may contain Earliest_* columns after delays are applied)
            original_solution_df: Original solution DataFrame before delays are applied (for completed tasks)
        """
        self.task_states = task_states
        self.tau = tau
        self.solution_df = solution_df
        # Store original solution for completed tasks (to get original durations)
        self.original_solution_df = original_solution_df if original_solution_df is not None else solution_df
        self._module_id_to_index = {
            str(row['Module_ID']): int(row.get('Module_Index', 0))
            for _, row in self.solution_df.iterrows()
        }
    
    def build_fixed_constraints(self) -> Dict[str, any]:
        """
        Build fixed constraints for re-optimization.
        Returns a dictionary with:
        - fixed_installation_starts: {module_index: start_time}
        - fixed_production_starts: {module_index: start_time}
        - fixed_arrival_times: {module_index: arrival_time}
        - fixed_durations: {module_index: {phase: duration}}
        """
        fixed_installation_starts = {}
        fixed_production_starts = {}
        fixed_arrival_times = {}
        fixed_durations = {}
        
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
                    # Check if task actually started by time tau (considering delays)
                    earliest_start = row.get('Earliest_Production_Start')
                    actual_start_lb = earliest_start if pd.notna(earliest_start) else state.start_time
                    
                    # Only fix if: (1) completed, or (2) in progress AND actually started by tau
                    if state.status == "COMPLETED" or (state.status == "IN_PROGRESS" and actual_start_lb is not None and actual_start_lb <= self.tau):
                        # Fix production start
                        prod_start = row.get('Production_Start')
                        if prod_start:
                            fixed_production_starts[module_index] = prod_start
                    
                    # Fix duration based on status
                    if state.status == "COMPLETED":
                        # Already completed - use original duration (task finished before delay was detected)
                        fixed_durations.setdefault(module_index, {})['FABRICATION'] = original_row.get('Production_Duration', 0)
                    elif state.status == "IN_PROGRESS" and actual_start_lb is not None and actual_start_lb <= self.tau:
                        # In progress - use delayed duration if DURATION_EXTENSION was applied
                        # row contains the modified duration after DelayApplier
                        fixed_durations.setdefault(module_index, {})['FABRICATION'] = row.get('Production_Duration', 0)
                
                elif state.phase == "TRANSPORT":
                    # Check if transport actually started by time tau (considering delays)
                    earliest_start = row.get('Earliest_Transport_Start')
                    actual_start_lb = earliest_start if pd.notna(earliest_start) else state.start_time
                    
                    # Only fix if: (1) completed, or (2) in progress AND actually started by tau
                    if state.status == "COMPLETED" or (state.status == "IN_PROGRESS" and actual_start_lb is not None and actual_start_lb <= self.tau):
                        # Fix arrival time (calculate from Transport_Start if Arrival_Time doesn't exist)
                        arrival = row.get('Arrival_Time')
                        if arrival is None:
                            transport_start = row.get('Transport_Start')
                            transport_duration = row.get('Transport_Duration', 0)
                            if transport_start is not None:
                                arrival = transport_start + transport_duration #通常不应该执行这一步
                        if arrival:
                            fixed_arrival_times[module_index] = arrival
                    
                    # Fix duration based on status
                    if state.status == "COMPLETED":
                        # Already completed - use original duration (task finished before delay was detected)
                        fixed_durations.setdefault(module_index, {})['TRANSPORT'] = original_row.get('Transport_Duration', 0)
                    elif state.status == "IN_PROGRESS" and actual_start_lb is not None and actual_start_lb <= self.tau:
                        # In progress - use delayed duration if DURATION_EXTENSION was applied
                        fixed_durations.setdefault(module_index, {})['TRANSPORT'] = row.get('Transport_Duration', 0)
                
                elif state.phase == "INSTALLATION":
                    # Check if installation actually started by time tau (considering delays)
                    earliest_start = row.get('Earliest_Installation_Start')
                    actual_start_lb = earliest_start if pd.notna(earliest_start) else state.start_time
                    
                    # Only fix if: (1) completed, or (2) in progress AND actually started by tau
                    if state.status == "COMPLETED" or (state.status == "IN_PROGRESS" and actual_start_lb is not None and actual_start_lb <= self.tau):
                        # Fix installation start
                        install_start = row.get('Installation_Start')
                        if install_start:
                            fixed_installation_starts[module_index] = install_start
                    
                    # Fix duration based on status
                    if state.status == "COMPLETED":
                        # Already completed - use original duration (task finished before delay was detected)
                        fixed_durations.setdefault(module_index, {})['INSTALLATION'] = original_row.get('Installation_Duration', 0)
                    elif state.status == "IN_PROGRESS" and actual_start_lb is not None and actual_start_lb <= self.tau:
                        # In progress - use delayed duration if DURATION_EXTENSION was applied
                        fixed_durations.setdefault(module_index, {})['INSTALLATION'] = row.get('Installation_Duration', 0)
        
        return {
            'fixed_installation_starts': fixed_installation_starts,
            'fixed_production_starts': fixed_production_starts,
            'fixed_arrival_times': fixed_arrival_times,
            'fixed_durations': fixed_durations
        }


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

