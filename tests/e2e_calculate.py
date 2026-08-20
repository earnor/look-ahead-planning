"""Offscreen end-to-end run: create a project from CSV and run Calculate."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from PyQt6.QtCore import QDate  # noqa: E402

import planning_tool.main as pm  # noqa: E402
from planning_tool.ui.pages import SettingsPage  # noqa: E402

# Message boxes would block the run; record them instead.
popups = []
for name in ("warning", "critical", "information"):
    def _capture(parent, title, text, *a, _n=name, **kw):
        popups.append((_n, title, text))
        print(f"[popup:{_n}] {title}: {text[:200]}")
        return QMessageBox.StandardButton.Ok
    setattr(QMessageBox, name, staticmethod(_capture))

app = QApplication.instance() or QApplication(sys.argv)
win = pm.MainWindow()

settings_page = win.stack.widget(win.page_index["settings"])
assert isinstance(settings_page, SettingsPage)
settings_page.start_datetime.setDate(QDate(2026, 1, 12))
settings_page.crew_count.setText("2")
settings_page.machine_count.setText("2")
print("default site storage:", settings_page.site_storage.text())
print("default factory storage:", settings_page.factory_storage.text())
print("has target_datetime widget:", hasattr(settings_page, "target_datetime"))
assert settings_page.site_storage.text() == "10"
assert settings_page.factory_storage.text() == "10"
assert not hasattr(settings_page, "target_datetime")

weights = {k: v.text() for k, v in settings_page.cost_inputs.items()}
print("default objective weights:", weights)
assert weights == {
    "w_duration": "0.4",
    "w_transport": "0.1",
    "w_site_storage": "0.4",
    "w_factory_storage": "0.1",
}

csv_path = str(ROOT / "data" / "Rapla_Stage1_input.csv")
project_id = win.mgr.create_project_from_csv("warm_start_e2e", csv_path)
win.current_project_id = project_id
print("project id:", project_id)

win.on_calculate_clicked()

sol_table = win.mgr.solution_table_name(project_id)
df = pd.read_sql_table(sol_table, win.engine)
print("solution rows:", len(df))
print(df.head(10).to_string())

versions = pd.read_sql_table(win.mgr.optimization_versions_table_name(project_id), win.engine)
print(versions.to_string())

loads = df.groupby("Arrival_Time").size()
print("truck loads:\n", loads.to_string())
assert loads.max() <= 7, "a truck exceeds 7 modules"
assert (loads < 5).sum() <= 1, "more than one partial load"

reference = win.mgr.get_normalization_reference(project_id)
print("stored objective reference:", reference)
assert reference is not None
assert reference["ref_duration"] > 0 and reference["ref_transport"] > 0
assert reference["ref_site_storage"] + reference["ref_factory_storage"] > 0

# The reference must survive later runs, otherwise objective values would not be
# comparable between versions. Mark it and check the second run leaves it alone.
marked = {k: v + 1 for k, v in reference.items()}
win.mgr.set_normalization_reference(project_id, marked)
win.on_calculate_clicked()
assert win.mgr.get_normalization_reference(project_id) == marked, "reference was overwritten"

win.mgr.delete_project(project_id)
print("popups:", popups)
print("E2E OK")
