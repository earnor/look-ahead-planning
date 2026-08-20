"""Transport delay policy: only postponement before departure, whole-truck duration on the road."""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from planning_tool.rescheduler import (  # noqa: E402
    DelayApplier,
    DelayInfo,
    FixedConstraintsBuilder,
    TaskState,
    allowed_delay_types,
)


def test_allowed_types():
    assert allowed_delay_types("TRANSPORT", "NOT_STARTED") == ["START_POSTPONEMENT"]
    assert allowed_delay_types("TRANSPORT", "IN_PROGRESS") == ["DURATION_EXTENSION"]
    assert allowed_delay_types("TRANSPORT", "COMPLETED") == []
    assert "DURATION_EXTENSION" in allowed_delay_types("FABRICATION", "NOT_STARTED")
    assert "START_POSTPONEMENT" in allowed_delay_types("INSTALLATION", "NOT_STARTED")
    print("allowed types OK")


def _state(mid, index, phase, status, start=None, finish=None):
    return TaskState(
        module_id=mid, module_index=index, phase=phase, status=status,
        start_time=start, finish_time=finish,
        progress=1.0 if status == "COMPLETED" else 0.5,
        actual_start_time=start,
    )


def test_in_progress_duration_hits_whole_truck():
    df = pd.DataFrame({
        "Module_ID": ["A", "B", "C"],
        "Module_Index": [1, 2, 3],
        "Transport_Start": [3, 3, 10],
        "Transport_Duration": [1, 1, 1],
        "Arrival_Time": [4, 4, 11],
        "Production_Start": [1, 1, 8],
        "Production_Duration": [2, 2, 2],
        "Installation_Start": [8, 9, 15],
        "Installation_Duration": [3, 3, 3],
    })
    states = {
        "A": [_state("A", 1, "TRANSPORT", "IN_PROGRESS", 3, 4)],
        "B": [_state("B", 2, "TRANSPORT", "IN_PROGRESS", 3, 4)],
        "C": [_state("C", 3, "TRANSPORT", "NOT_STARTED", 10, 11)],
    }
    delay = DelayInfo("A", "DURATION_EXTENSION", "TRANSPORT", 3, 5, "2026-01-01 10:00:00")
    out = DelayApplier(df, [delay], states).apply_delays()
    assert int(out.loc[out.Module_ID == "A", "Transport_Duration"].iloc[0]) == 4
    assert int(out.loc[out.Module_ID == "B", "Transport_Duration"].iloc[0]) == 4
    assert int(out.loc[out.Module_ID == "C", "Transport_Duration"].iloc[0]) == 1
    assert int(out.loc[out.Module_ID == "A", "Arrival_Time"].iloc[0]) == 7
    assert int(out.loc[out.Module_ID == "B", "Arrival_Time"].iloc[0]) == 7
    print("in-progress duration extends the whole truck OK")


def test_not_started_duration_is_ignored():
    df = pd.DataFrame({
        "Module_ID": ["A"],
        "Module_Index": [1],
        "Transport_Start": [10],
        "Transport_Duration": [1],
        "Arrival_Time": [11],
        "Production_Start": [1],
        "Production_Duration": [2],
        "Installation_Start": [15],
        "Installation_Duration": [3],
    })
    states = {"A": [_state("A", 1, "TRANSPORT", "NOT_STARTED", 10, 11)]}
    delay = DelayInfo("A", "DURATION_EXTENSION", "TRANSPORT", 3, 5, "2026-01-01 10:00:00")
    out = DelayApplier(df, [delay], states).apply_delays()
    assert int(out.loc[0, "Transport_Duration"]) == 1
    print("not-started duration extension ignored OK")


def test_not_started_postponement_sets_earliest_arrival():
    df = pd.DataFrame({
        "Module_ID": ["A"],
        "Module_Index": [1],
        "Transport_Start": [10],
        "Transport_Duration": [1],
        "Arrival_Time": [11],
        "Production_Start": [1],
        "Production_Duration": [2],
        "Installation_Start": [15],
        "Installation_Duration": [3],
    })
    states = {
        "A": [
            _state("A", 1, "FABRICATION", "COMPLETED", 1, 2),
            _state("A", 1, "TRANSPORT", "NOT_STARTED", 10, 11),
            _state("A", 1, "INSTALLATION", "NOT_STARTED", 15, 17),
        ]
    }
    delay = DelayInfo("A", "START_POSTPONEMENT", "TRANSPORT", 3, 5, "2026-01-01 10:00:00")
    out = DelayApplier(df, [delay], states).apply_delays()
    assert int(out.loc[0, "Earliest_Transport_Start"]) == 13
    slots = [None] + list(range(1, 40))
    # Dummy slots as datetimes are not used for these statuses beyond identification
    from datetime import datetime, timedelta
    calendar = [datetime(2026, 1, 1) + timedelta(hours=i) for i in range(40)]
    fixed = FixedConstraintsBuilder(states, 5, out, calendar, df).build_fixed_constraints()
    assert 1 not in fixed["fixed_arrival_times"]
    assert fixed["earliest_arrival_times"][1] == 14  # start 13 + L 1
    print("not-started postponement rebaches via earliest arrival OK")


def test_in_progress_pins_arrival():
    df = pd.DataFrame({
        "Module_ID": ["A"],
        "Module_Index": [1],
        "Transport_Start": [3],
        "Transport_Duration": [4],
        "Arrival_Time": [7],
        "Production_Start": [1],
        "Production_Duration": [2],
        "Installation_Start": [10],
        "Installation_Duration": [3],
    })
    states = {"A": [_state("A", 1, "TRANSPORT", "IN_PROGRESS", 3, 4)]}
    from datetime import datetime, timedelta
    calendar = [datetime(2026, 1, 1) + timedelta(hours=i) for i in range(40)]
    fixed = FixedConstraintsBuilder(states, 5, df, calendar, df).build_fixed_constraints()
    assert fixed["fixed_arrival_times"][1] == 7
    print("in-progress arrival is pinned OK")


if __name__ == "__main__":
    test_allowed_types()
    test_in_progress_duration_hits_whole_truck()
    test_not_started_duration_is_ignored()
    test_not_started_postponement_sets_earliest_arrival()
    test_in_progress_pins_arrival()
    print("transport delay policy OK")
