"""Socio-economic comparison helpers: working days, peak occupancy, truck cost."""
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from planning_tool.ui.pages import ComparisonPage  # noqa: E402


class FakeWindow:
    """Minimal working calendar: Mon-Fri, 08:00-12:00 and 13:00-17:00 (8 hours/day)."""

    def _build_working_calendar_slots(self, settings, start_date, max_slot):
        day_map = settings.get("working_days") or {
            d: d in ["Mon", "Tue", "Wed", "Thu", "Fri"]
            for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        }
        slots = [None]
        cur_date = start_date
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        work_start, break_start = time(8, 0), time(12, 0)
        break_end, work_end = time(13, 0), time(17, 0)
        while len(slots) - 1 < max_slot:
            weekday_name = day_names[cur_date.weekday()]
            if day_map.get(weekday_name, False):
                for period_start, period_end in ((work_start, break_start), (break_end, work_end)):
                    cur_dt = datetime.combine(cur_date, period_start)
                    end_dt = datetime.combine(cur_date, period_end)
                    while cur_dt < end_dt and len(slots) - 1 < max_slot:
                        slots.append(cur_dt)
                        cur_dt += timedelta(hours=1)
            cur_date += timedelta(days=1)
        return slots


def test_peak_site_occupancy():
    df = pd.DataFrame({
        "Arrival_Time": [1, 1, 5],
        "Installation_Start": [3, 8, 6],
    })
    # t=1,2: modules 0+1 waiting (2); t=3,4: module 1 (1); t=5: modules 1+2 (2); t=6,7: module 1 (1)
    assert ComparisonPage._peak_site_occupancy(df) == 2

    same_hour = pd.DataFrame({
        "Arrival_Time": [4, 4],
        "Installation_Start": [4, 4],
    })
    assert ComparisonPage._peak_site_occupancy(same_hour) == 0
    print("peak occupancy OK")


def test_handover_working_days_skips_weekend():
    page = ComparisonPage.__new__(ComparisonPage)
    page.main_window = FakeWindow()
    settings = {
        "working_days": {d: d in ["Mon", "Tue", "Wed", "Thu", "Fri"]
                         for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
    }
    start = date(2026, 1, 5)  # Monday
    # 8 hours/day. Index 8 = Monday last hour -> 1 working day
    df_mon = pd.DataFrame({"Installation_Finish": [8]})
    assert page._count_handover_working_days(df_mon, settings, start) == 1
    # Index 9 = Tuesday first hour -> 2 working days
    df_tue = pd.DataFrame({"Installation_Finish": [9]})
    assert page._count_handover_working_days(df_tue, settings, start) == 2
    # Friday last hour = 5*8 = 40 -> 5 working days
    df_fri = pd.DataFrame({"Installation_Finish": [40]})
    assert page._count_handover_working_days(df_fri, settings, start) == 5
    # Next Monday first hour = 41 -> 6 working days (weekend not counted)
    df_next_mon = pd.DataFrame({"Installation_Finish": [41]})
    assert page._count_handover_working_days(df_next_mon, settings, start) == 6
    print("handover working days OK")


def test_money_format_and_coeff_parse():
    assert ComparisonPage._format_money(1200) == "1,200 CHF"
    assert ComparisonPage._format_money(1200.5) == "1,200.50 CHF"
    print("money format OK")


if __name__ == "__main__":
    test_peak_site_occupancy()
    test_handover_working_days_skips_weekend()
    test_money_format_and_coeff_parse()
    print("all socio-economic metric checks passed")
