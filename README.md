# Look-ahead Planning Tool

**Prototype software developed at ETH Zurich for the RENOMIZE project.**

This repository is a research prototype. It is not a production product, has not been certified for operational use, and may change without notice.

The mixed-integer program is solved with **Gurobi Optimizer** under a **Gurobi Educational / Academic license**. You must have a valid Gurobi educational license installed on the machine that runs the solver. Commercial use of Gurobi requires a separate commercial license from Gurobi Optimization, LLC.

---

## Overview

The tool supports **look-ahead scheduling** of prefabricated building modules: factory production, truck transport, on-site storage, and installation. Given module durations, installation precedence, resource capacities, and a working calendar, it builds a time-indexed MIP, solves it with Gurobi, and shows the resulting schedule in a desktop UI.

When delays are recorded against a live schedule, the tool can **re-optimize from a detection time τ**, keeping completed and in-progress work fixed and planning the remainder.

## Features

- Import a module list from CSV and store it per project in SQLite
- Configure start date, working days, work/break hours, machines, crews, and storage capacities
- Optimize a weighted objective (project duration, transport trips, on-site storage, factory storage)
- Display the schedule (production → factory wait → transport → site wait → installation)
- Record fabrication, transport, or installation delays and re-optimize
- Compare two plan versions (Gantt charts, operational metrics, socio-economic indicators)
- Export the current schedule to Excel

## Requirements

| Item | Requirement |
| --- | --- |
| Python | 3.11 or newer |
| OS | Windows, macOS, or Linux with a desktop session |
| Solver | [Gurobi](https://www.gurobi.com/) with a **valid educational/academic license** |
| GUI | PyQt6 |

Python packages used by the application:

```text
PyQt6
pandas
sqlalchemy
matplotlib
numpy
gurobipy
openpyxl          # required for Excel export
```

`gurobipy` only works after Gurobi and its license file are installed. Confirm with:

```bash
python -c "import gurobipy; print(gurobipy.gurobi.version())"
```

## Installation

### 1. Gurobi educational license

1. Install Gurobi from [https://www.gurobi.com/downloads/](https://www.gurobi.com/downloads/).
2. Request an academic/educational license and activate it (`grbgetkey`, or follow Gurobi’s current academic instructions).
3. Verify that `gurobi_cl` (or the Gurobi Python API) starts without a license error.

### 2. Clone and create a virtual environment

```bash
git clone <repository-url>
cd look-ahead-planning

python -m venv .venv
```

Windows (PowerShell):

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
source .venv/bin/activate
```

### 3. Install Python dependencies

```bash
pip install PyQt6 pandas sqlalchemy matplotlib numpy openpyxl gurobipy
```

The application package lives under `src/planning_tool`. Set `PYTHONPATH` to `src` (see below), or install the project in editable mode if you use Poetry:

```bash
pip install -e .
```

## Running the application

From the repository root, with the virtual environment activated:

**Windows (PowerShell)**

```powershell
$env:PYTHONPATH = "src"
python src/planning_tool/main.py
```

**macOS / Linux**

```bash
PYTHONPATH=src python src/planning_tool/main.py
```

The SQLite database is `data/input_database.db`. It is created automatically on first launch.

## How to use

Work through the sidebar pages in this order.

### 1. Upload — create a project

1. Open **Upload**.
2. Drop or select a CSV file (see [Input format](#input-format)).
3. Enter a project name and confirm.

The raw table is stored as read-only. Later optimization writes separate solution tables; it does not modify the uploaded CSV data.

A sample file is provided at [`data/test_input.csv`](data/test_input.csv). [`data/Rapla_Stage1_input.csv`](data/Rapla_Stage1_input.csv) is a larger example.

### 2. Settings — calendar, resources, weights

Open **Settings** and save before the first **Calculate**.

| Setting | Meaning |
| --- | --- |
| Project start date | Calendar date of time index 1 (first working hour) |
| Working days | Weekdays that contain working hours (default Mon–Fri) |
| Work / break times | Daily working windows; each hour is one time index |
| Machine count | Parallel factory production capacity |
| Crew count | Parallel on-site installation capacity |
| Onsite / factory storage | Maximum modules that may wait at site / factory |
| Objective weights | Relative priorities; they should sum to 1 |

Default weights: duration **0.4**, transport **0.1**, on-site storage **0.4**, factory storage **0.1**.

Each objective term is divided by a reference taken from a constructive heuristic on the **first** optimization of the project, then kept, so later versions stay comparable.

### 3. Schedule — optimize

1. Open **Schedule**.
2. Click **Calculate**.

The solver:

1. Builds a working-hour calendar from Settings.
2. Runs a constructive heuristic to size the horizon \(T\) and to obtain objective reference values.
3. Solves the MIP with Gurobi (default time limit 120 s, MIP gap 20%).
4. Stores the result as **Version 0**.

If no feasible solution is found, no version is written. Check capacities, precedence, and the start date.

The schedule table shows each module’s planned times. Status is derived from “now” versus fabrication start and installation finish.

**Export** writes an Excel workbook of the current version (requires `openpyxl`).

### 4. Record delays and re-optimize

On **Schedule**, double-click a delay cell for **Fabrication**, **Transport**, or **Installation**.

| Delay type | Typical use |
| --- | --- |
| `DURATION_EXTENSION` | The phase takes longer than planned |
| `START_POSTPONEMENT` | The phase cannot start at the planned time |

**Transport** is restricted by progress at detection time τ:

- Not departed → start postponement only (the truck is re-batched).
- On the road → duration extension for the **whole truck** (same planned arrival slot, longer travel).
- Already arrived → no transport delay.

Enter delay hours, detection time τ, and an optional reason, then run **Calculate** again. Completed work is frozen; in-progress work keeps its leftover duration; unfixed tasks cannot start before τ. A new version is stored, linked to the base plan.

### 5. Dashboard

**Dashboard** shows project start, forecast completion, and how many modules currently sit in factory or site storage.

### 6. Comparison

1. Open **Comparison**.
2. Choose an **Upper** and a **Lower** version.
3. Click **Compare**.

Operational metrics (hours / truck bunches):

- Construction hours
- Factory storage module hours
- Site storage module hours
- Transport bunch number

**Socio-economic impact** (coefficients are typed on this page; they are not saved to the database):

| Indicator | How it is computed |
| --- | --- |
| Handover delay | Working days from project start to latest installation finish (working calendar, not hours ÷ 8). If “cost per delayed day” is set, days are also shown as CHF. |
| Transportation cost | Number of truck trips × “cost per truck” |
| Peak site occupancy | Peak number of modules waiting on site (arrived, installation not yet started). Not converted to money. |

Percentage change on operational cards is relative to the **Lower** version. If Lower is zero, the percentage is shown as `n/a`.

## Input format

CSV, UTF-8, header row required.

| Column | Required | Description |
| --- | --- | --- |
| `Module ID` or `Module_ID` | yes | Unique module identifier |
| `Installation Duration` | yes | Installation length in **working hours** |
| `Production Duration` | yes | Factory production length in **working hours** |
| `Transportation Duration` | yes | Transport length in **working hours** |
| `Installation Precedence` | yes | Predecessor module IDs; empty if none. Multiple predecessors: comma-separated (`VS-02-21,VS-02-22`) |

Durations are positive numbers in the same hour grid as the working calendar (one time index = one working hour).

Example:

```csv
Module_ID,Installation Duration,Production Duration,Transportation Duration,Installation Precedence
VS-02-31,3,2,1,
VS-02-21,1,2,1,
VS-01-2,3,4,1,VS-02-21
```

## Project layout

```text
look-ahead-planning/
├── data/
│   ├── input_database.db      # SQLite store (created at runtime)
│   ├── test_input.csv         # small example
│   └── Rapla_Stage1_input.csv
├── src/planning_tool/
│   ├── main.py                # application entry
│   ├── model.py               # Gurobi MIP
│   ├── warm_start.py          # constructive heuristic (horizon + references)
│   ├── rescheduler.py         # delay application and re-opt constraints
│   ├── datamanager.py         # SQLite schema and project tables
│   └── ui/                    # PyQt6 pages, dialogs, widgets
└── tests/                     # scripts for selected policies and metrics
```

## Tests

From the repository root, with `PYTHONPATH=src` and Gurobi available where a script solves a model:

```bash
python tests/check_transport_delay_policy.py
python tests/check_socio_economic_metrics.py
python tests/check_reopt_horizon.py
```

`check_reopt_horizon.py` calls Gurobi.

## Solver notes

- One time period is one **working hour** on the calendar from Settings (weekends and non-working days are skipped).
- Truck loads are batched (typically 3–5 modules; one partial load is allowed).
- The heuristic is used to choose \(T\) and objective reference values. It is **not** injected as a MIP warm start.
- Default Gurobi limits: `TimeLimit = 120` seconds, `MIPGap = 0.2`. A run may stop at the time limit with a feasible but not proven-optimal solution.

## Disclaimer

This software is a **prototype** prepared at ETH Zurich in the context of **RENOMIZE**. It is provided for research and demonstration. ETH Zurich, the authors, and project partners accept no liability for decisions made on the basis of its output.

Use of Gurobi is subject to the [Gurobi End User License Agreement](https://www.gurobi.com/eula/). This prototype was developed with a Gurobi educational license and does not grant you any Gurobi rights.

## Contact

Zhaoyu Wang — [wangzha@ethz.ch](mailto:wangzha@ethz.ch)
