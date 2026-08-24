# Prefabricated Construction Scheduling Tool

**Prototype software developed by ETH Zurich for the RENOMIZE project.**

This repository is a research prototype. It is not a production product, has not been certified for operational use, and may change without notice.

The mixed-integer program is solved with **SCIP** through [PySCIPOpt](https://github.com/scipopt/PySCIPOpt). SCIP is open source (Apache 2.0 from SCIP 8 onward); no commercial solver licence is required.

---

## Overview

The tool supports **look-ahead scheduling** of prefabricated building modules: factory production, factory storage, truck transport, on-site storage, and installation. Given module durations, installation precedence, resource capacities, and a working calendar, it builds a time-indexed MIP, solves it with SCIP, and shows the resulting schedule in a desktop UI.

When delays are recorded against a live schedule, the tool can **re-optimize from a detection time τ**, keeping completed and in-progress work fixed and planning the remainder.

## Features

- Import a module list from CSV and store it per project in SQLite
- Configure start date, working days, work/break hours, machines, crews, and storage capacities
- Optimize a weighted objective (project duration, transport trips, on-site storage, factory storage)
- Display the schedule (production → factory wait → transport → site wait → installation)
- Record fabrication, transport, or installation delays and re-optimize
- Compare two plan versions (Gantt charts and operational metrics)
- Monetise a chosen schedule against the original plan (construction, transport batches, occupant disruption, biodiversity)
- Export the current schedule to Excel

## Requirements

| Item | Requirement |
| --- | --- |
| Python | 3.11 or newer |
| OS | Windows, macOS, or Linux with a desktop session |
| Solver | [SCIP](https://www.scipopt.org/) via `pyscipopt` |
| GUI | PyQt6 |

Python packages used by the application:

```text
PyQt6
pandas
sqlalchemy
matplotlib
numpy
pyscipopt
openpyxl          # required for Excel export
```

Confirm SCIP is available with:

```bash
python -c "from pyscipopt import Model; print('pyscipopt ok')"
```

## Installation

### 1. Clone and create a virtual environment

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

### 2. Install Python dependencies

```bash
pip install PyQt6 pandas sqlalchemy matplotlib numpy openpyxl pyscipopt
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

A sample file is provided at [`data/test_input.csv`](data/test_input.csv). 

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
3. Solves the MIP with SCIP (default time limit 600 s, MIP gap 1%).
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

Percentage change on operational cards is relative to the **Lower** version. If Lower is zero, the percentage is shown as `n/a`.

### 7. Costs

Schedule Gantt comparison stays on **Comparison**. **Costs** only compares money.

1. Open **Costs**.
2. Enter unit rates once (they apply to both schedules). Rates stay on this page; they are not saved.
3. The **upper** panel is the chosen (new) schedule — pick a version there.
4. The **lower** panel is always **Version 0** (the original plan, before disruption).

| Category | Quantity from the schedule | User input | Formula |
| --- | --- | --- | --- |
| Construction | Working days (start date through latest installation finish, working calendar) | Crane CHF/day, crew CHF/day, optional extra daily terms | days × (crane + crew + extra terms) |
| Batch | Number of truck trips (unique transport batches) | Cost per truck | trucks × cost per truck |
| Disruption to occupants | Working days | Cost per resident per day, number of nearby residents | days × cost/resident/day × residents |
| Biodiversity | Working days | Occupied area (m²), price per m² per day | area × days × price |

Each version panel lists the four categories and a total. The chosen panel also shows the difference versus Version 0.

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
│   └── test_input.csv         # small example
├── src/planning_tool/
│   ├── main.py                # application entry
│   ├── model.py               # SCIP MIP (PySCIPOpt)
│   ├── warm_start.py          # constructive heuristic (horizon + references)
│   ├── rescheduler.py         # delay application and re-opt constraints
│   ├── datamanager.py         # SQLite schema and project tables
│   └── ui/                    # PyQt6 pages, dialogs, widgets
└── tests/                     # scripts for selected policies and metrics
```


## Solver notes

- One time period is one **working hour** on the calendar from Settings (weekends and non-working days are skipped).
- Truck loads are batched (typically 3–5 modules; one partial load is allowed).
- The heuristic chooses \(T\) (so branch-and-bound searches a shorter horizon) and is also given to SCIP as a feasible incumbent.
- Default SCIP limits: time 600 seconds, relative gap 0.01. Emphasis is feasibility, with aggressive primal heuristics and light cutting, so the time is spent improving the incumbent rather than only proving a bound.

## Disclaimer

This software is a **prototype** prepared at ETH Zurich in the context of **RENOMIZE**. It is provided for research and demonstration. ETH Zurich, the authors, and project partners accept no liability for decisions made on the basis of its output.

SCIP is used under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) (SCIP 8 and later). See [scipopt.org](https://www.scipopt.org/) for details.

## Contact

Zhaoyu Wang — [zhaoyu.wang@ibi.baug.ethz.ch](mailto:zhaoyu.wang@ibi.baug.ethz.ch)
Dr. Arnor Elvarsson — [elvarsson@ibi.baug.ethz.ch](mailto:elvarsson@ibi.baug.ethz.ch)