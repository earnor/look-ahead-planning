"""
Monetise a solved schedule against unit rates.

Quantities (working days, trucks, crew count) come from the solution and
from the Settings snapshot stored with that version. Unit rates are
supplied by the Costs page; defaults below match the project rates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable, Optional

import pandas as pd

from planning_tool.datamanager import ScheduleDataManager

DEFAULT_CRANE_PER_DAY = 1500.0
DEFAULT_CREW_PER_DAY = 1313.0
DEFAULT_COST_PER_TRUCK = 500.0
DEFAULT_OCCUPANT_PER_HOUSEHOLD_DAY = 7.0
DEFAULT_BIODIVERSITY_PER_M2 = 50.0
DEFAULT_CREW_COUNT = 2
DEFAULT_CONSTRUCTION_DAY_COST = (
    DEFAULT_CRANE_PER_DAY + DEFAULT_CREW_PER_DAY * DEFAULT_CREW_COUNT
)
DEFAULT_TRANSPORT_BATCH_COST = DEFAULT_COST_PER_TRUCK


def construction_day_cost_for_crews(
    n_crews: int,
    crane_per_day: Optional[float] = None,
    crew_per_day: Optional[float] = None,
    extra_per_day: Optional[list[float]] = None,
) -> float:
    """Crane + working-crew cost + extra daily terms for this many on-site crews."""
    crane = DEFAULT_CRANE_PER_DAY if crane_per_day is None else float(crane_per_day)
    crew = DEFAULT_CREW_PER_DAY if crew_per_day is None else float(crew_per_day)
    extras = sum(float(v or 0) for v in (extra_per_day or []))
    return crane + crew * max(0, int(n_crews)) + extras


def rebase_construction_day_cost(
    current: float,
    old_n: int,
    new_n: int,
    crane_per_day: Optional[float] = None,
    crew_per_day: Optional[float] = None,
    extra_per_day: Optional[list[float]] = None,
) -> float:
    """Move the crew portion with headcount; keep any extra the user added."""
    extra = float(current) - construction_day_cost_for_crews(
        old_n, crane_per_day, crew_per_day, extra_per_day
    )
    return extra + construction_day_cost_for_crews(
        new_n, crane_per_day, crew_per_day, extra_per_day
    )

BuildSlots = Callable[[dict, date, int], list]


@dataclass
class CostRates:
    crane_per_day: Optional[float] = DEFAULT_CRANE_PER_DAY
    crew_per_day: Optional[float] = DEFAULT_CREW_PER_DAY
    cost_per_truck: Optional[float] = DEFAULT_COST_PER_TRUCK
    occupant_per_household_day: Optional[float] = DEFAULT_OCCUPANT_PER_HOUSEHOLD_DAY
    biodiversity_per_m2: Optional[float] = DEFAULT_BIODIVERSITY_PER_M2
    extra_per_day: list[float] = field(default_factory=list)
    construction_day_cost: Optional[float] = None
    transport_batch_cost: Optional[float] = None


def daily_construction_from_rates(n_crews: int, rates: Optional[CostRates] = None) -> float:
    rates = rates or CostRates()
    return construction_day_cost_for_crews(
        n_crews,
        crane_per_day=rates.crane_per_day,
        crew_per_day=rates.crew_per_day,
        extra_per_day=rates.extra_per_day,
    )


def transport_batch_from_rates(rates: Optional[CostRates] = None) -> float:
    rates = rates or CostRates()
    if rates.cost_per_truck is None:
        return DEFAULT_TRANSPORT_BATCH_COST
    return float(rates.cost_per_truck)


@dataclass
class ScheduleQuantities:
    working_days: Optional[int] = None
    trucks: int = 0
    crews: int = 1


def _rate(value: Optional[float]) -> float:
    return 0.0 if value is None else float(value)


def parse_cost_setting(settings: Optional[dict], key: str, default: float) -> float:
    if not settings:
        return float(default)
    raw = settings.get(key, default)
    try:
        text = str(raw).strip()
        return float(text) if text else float(default)
    except (TypeError, ValueError):
        return float(default)


def _parse_clock(value: Optional[str], default: time) -> time:
    if not value:
        return default
    for fmt in ("%I:%M %p", "%H:%M"):
        try:
            return datetime.strptime(str(value).strip(), fmt).time()
        except ValueError:
            continue
    return default


def hours_per_working_day(settings: Optional[dict]) -> int:
    """Working hours in one calendar workday, matching the Project Variables clock."""
    settings = settings or {}
    work_start = _parse_clock(settings.get("work_start_time"), time(8, 0))
    work_end = _parse_clock(settings.get("work_end_time"), time(17, 0))
    break_start = _parse_clock(settings.get("break_start_time"), time(12, 0))
    break_end = _parse_clock(settings.get("break_end_time"), time(13, 0))
    hours = 0
    today = date.today()
    for period_start, period_end in ((work_start, break_start), (break_end, work_end)):
        cur = datetime.combine(today, period_start)
        end = datetime.combine(today, period_end)
        while cur < end:
            hours += 1
            cur += timedelta(hours=1)
    return max(1, hours)


def parse_project_start_date(
    start_datetime_str: Optional[str],
    settings: Optional[dict] = None,
) -> Optional[date]:
    candidates = [start_datetime_str]
    if settings:
        candidates.append(settings.get("start_datetime"))
    for src in candidates:
        if not src or str(src).strip().lower() in ("", "mm/dd/yyyy"):
            continue
        try:
            return datetime.strptime(str(src).strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
    return None


def merge_settings(live: Optional[dict], stored: Optional[dict]) -> dict:
    """Live Settings as fallback; version snapshot overwrites what Calculate used."""
    merged = dict(live or {})
    merged.update(stored or {})
    return merged


def truck_count(solution_df: pd.DataFrame) -> int:
    if solution_df is None or solution_df.empty:
        return 0
    transport_start = solution_df.get("Transport_Start")
    if transport_start is None:
        return 0
    return int(len(transport_start.dropna().unique()))


def crew_count(settings: Optional[dict], default: int = 1) -> int:
    return ScheduleDataManager.crew_count_from_settings(settings, default=default)


def count_handover_working_days(
    solution_df: pd.DataFrame,
    settings: Optional[dict],
    start_date: Optional[date],
    build_slots: Optional[BuildSlots],
) -> Optional[int]:
    """Working days from project start through latest installation finish."""
    if solution_df is None or solution_df.empty or start_date is None or not settings:
        return None
    if build_slots is None or "Installation_Finish" not in solution_df.columns:
        return None
    finishes = solution_df["Installation_Finish"].dropna()
    if finishes.empty:
        return None
    finish_idx = int(float(finishes.max()))
    if finish_idx < 1:
        return None
    try:
        slots = build_slots(settings, start_date, finish_idx)
    except Exception:
        return None
    if finish_idx >= len(slots) or slots[finish_idx] is None:
        return None
    used_dates = {
        slots[idx].date()
        for idx in range(1, finish_idx + 1)
        if idx < len(slots) and slots[idx] is not None
    }
    return len(used_dates)


def quantities_from_solution(
    solution_df: pd.DataFrame,
    settings: Optional[dict],
    start_date: Optional[date],
    build_slots: Optional[BuildSlots],
) -> ScheduleQuantities:
    return ScheduleQuantities(
        working_days=count_handover_working_days(
            solution_df, settings, start_date, build_slots
        ),
        trucks=truck_count(solution_df),
        crews=crew_count(settings, default=1),
    )


def compute_monetised_costs(
    chosen: ScheduleQuantities,
    original: ScheduleQuantities,
    rates: CostRates,
    households: Optional[float] = None,
    area_m2: Optional[float] = None,
    chosen_rates: Optional[CostRates] = None,
    original_rates: Optional[CostRates] = None,
) -> dict:
    extras = sum(_rate(v) for v in rates.extra_per_day)
    chosen_rates = chosen_rates or rates
    original_rates = original_rates or rates

    def daily_construction(n_crews: int, schedule_rates: CostRates) -> float:
        if schedule_rates.construction_day_cost is not None:
            return _rate(schedule_rates.construction_day_cost)
        return (
            _rate(schedule_rates.crane_per_day)
            + _rate(schedule_rates.crew_per_day) * max(0, int(n_crews))
            + extras
        )

    def truck_rate(schedule_rates: CostRates) -> float:
        if schedule_rates.transport_batch_cost is not None:
            return _rate(schedule_rates.transport_batch_cost)
        return _rate(schedule_rates.cost_per_truck)

    def times_days(days: Optional[int], unit: Optional[float]) -> Optional[float]:
        if days is None or unit is None:
            return None
        return days * unit

    construction_chosen = times_days(
        chosen.working_days, daily_construction(chosen.crews, chosen_rates)
    )
    construction_original = times_days(
        original.working_days, daily_construction(original.crews, original_rates)
    )
    batch_chosen = chosen.trucks * truck_rate(chosen_rates)
    batch_original = original.trucks * truck_rate(original_rates)
    occupant_unit = (
        None
        if households is None
        else _rate(rates.occupant_per_household_day) * float(households)
    )
    occupants_chosen = times_days(chosen.working_days, occupant_unit)
    occupants_original = times_days(original.working_days, occupant_unit)
    biodiversity = (
        None
        if area_m2 is None
        else float(area_m2) * _rate(rates.biodiversity_per_m2)
    )

    def add_total(*parts: Optional[float]) -> float:
        return sum(0.0 if part is None else part for part in parts)

    return {
        "construction_chosen": construction_chosen,
        "construction_original": construction_original,
        "batch_chosen": batch_chosen,
        "batch_original": batch_original,
        "occupants_chosen": occupants_chosen,
        "occupants_original": occupants_original,
        "biodiversity_chosen": biodiversity,
        "biodiversity_original": biodiversity,
        "total_chosen": add_total(
            construction_chosen, batch_chosen, occupants_chosen, biodiversity
        ),
        "total_original": add_total(
            construction_original,
            batch_original,
            occupants_original,
            biodiversity,
        ),
    }
