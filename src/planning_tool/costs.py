"""
Monetise a solved schedule against unit rates.

Quantities (working days, trucks, crew count) come from the solution and
from the Settings snapshot stored with that version. Unit rates are
supplied by the Costs page; defaults below match the project rates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Optional

import pandas as pd

from planning_tool.datamanager import ScheduleDataManager

DEFAULT_CRANE_PER_DAY = 1500.0
DEFAULT_CREW_PER_DAY = 1313.0
DEFAULT_COST_PER_TRUCK = 500.0
DEFAULT_OCCUPANT_PER_HOUSEHOLD_DAY = 7.0
DEFAULT_BIODIVERSITY_PER_M2 = 50.0

BuildSlots = Callable[[dict, date, int], list]


@dataclass
class CostRates:
    crane_per_day: Optional[float] = DEFAULT_CRANE_PER_DAY
    crew_per_day: Optional[float] = DEFAULT_CREW_PER_DAY
    cost_per_truck: Optional[float] = DEFAULT_COST_PER_TRUCK
    occupant_per_household_day: Optional[float] = DEFAULT_OCCUPANT_PER_HOUSEHOLD_DAY
    biodiversity_per_m2: Optional[float] = DEFAULT_BIODIVERSITY_PER_M2
    extra_per_day: list[float] = field(default_factory=list)


@dataclass
class ScheduleQuantities:
    working_days: Optional[int] = None
    trucks: int = 0
    crews: int = 1


def _rate(value: Optional[float]) -> float:
    return 0.0 if value is None else float(value)


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
) -> dict:
    extras = sum(_rate(v) for v in rates.extra_per_day)

    def daily_construction(n_crews: int) -> float:
        return (
            _rate(rates.crane_per_day)
            + _rate(rates.crew_per_day) * max(0, int(n_crews))
            + extras
        )

    def times_days(days: Optional[int], unit: Optional[float]) -> Optional[float]:
        if days is None or unit is None:
            return None
        return days * unit

    construction_chosen = times_days(chosen.working_days, daily_construction(chosen.crews))
    construction_original = times_days(
        original.working_days, daily_construction(original.crews)
    )
    batch_chosen = chosen.trucks * _rate(rates.cost_per_truck)
    batch_original = original.trucks * _rate(rates.cost_per_truck)
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
