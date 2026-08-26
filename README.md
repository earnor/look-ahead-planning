# Prefabricated Construction Scheduling Tool

**Prototype software developed by ETH Zurich for the RENOMIZE project.**

This repository is a research prototype. It is not a production product, has not been certified for operational use, and may change without notice.

The scheduler is a constraint program solved with **OR-Tools CP-SAT**. Production, installation, and storage are interval variables with cumulative resource constraints; truck batching stays discrete. CP-SAT is open source (Apache 2.0); no commercial solver licence is required.

---

## Overview

The tool supports **look-ahead scheduling** of prefabricated building modules: factory production, factory storage, truck transport, on-site storage, and installation. Given module durations, installation precedence, resource capacities, and a working calendar, it builds a CP-SAT model (interval variables and cumulatives), solves it with OR-Tools, and shows the resulting schedule in a desktop UI.

When delays are recorded against a live schedule, the tool can **re-optimize from a detection time τ**, keeping completed and in-progress work fixed and planning the remainder.

## Features

- Import a module list from CSV and store it per project in SQLite
- Add a module later (name, three durations, installation precedence) so the next Calculate includes it
- Configure the project start date, working days, work/break hours, machines, crews, and storage capacities
- Optimize a weighted objective (project duration, transport batches, on-site storage, factory storage)
- Display the schedule (production → factory wait → transport → site wait → installation)
- Record production, transport, or installation delays and re-optimize
- Compare two plan versions (Gantt charts and operational metrics)
- Monetise two schedule versions (construction, transport batches, occupant disruption, biodiversity)
- Export the current schedule to Excel

## Requirements

| Item | Requirement |
| --- | --- |
| Python | 3.11 or newer |
| OS | Windows, macOS, or Linux with a desktop session |
| Solver | [OR-Tools CP-SAT](https://developers.google.com/optimization) (`ortools`) |
| GUI | PyQt6 |

Python packages used by the application:

```text
PyQt6
pandas
sqlalchemy
matplotlib
numpy
ortools
openpyxl          # required for Excel export
```

Confirm CP-SAT is available with:

```bash
python -c "from ortools.sat.python import cp_model; print('ortools ok')"
```

## Installation

### 1. Clone and create a virtual environment

```bash
git clone https://github.com/earnor/look-ahead-planning.git
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
pip install PyQt6 pandas sqlalchemy matplotlib numpy openpyxl ortools
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

### 1. Upload Data — create a project

1. Open **Upload Data**.
2. Optionally click **Download example CSV** to get a template.
3. Drop or select a CSV file (see [Input format](#input-format)).
4. Enter a project name and confirm.

The uploaded table is stored as read-only. Later optimization writes separate solution tables; it does not rewrite the CSV. **Add Module** on the Schedule page is the supported way to append rows for the next Calculate.

### 2. Project Variables — calendar, resources, weights

Open **Project Variables** and save before the first **Calculate**.

| Setting | Meaning |
| --- | --- |
| Project start date | Calendar date of time index 1 (first working hour). Stored per project. Locked after the first successful Calculate. |
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
2. Optionally click **Add Module** and enter a module name, production / transportation / installation durations (working hours), and comma-separated predecessor IDs. The new row is stored in the project input table and is included the next time you click Calculate (Currently, the precedence relationships of uploaded modules cannot be modified).
3. Click **Calculate**.

The solver:

1. Builds a working-hour calendar from Project Variables.
2. Runs a constructive heuristic to size the horizon \(T\) and to obtain objective reference values.
3. Solves the CP-SAT model with OR-Tools (default time limit 120 s, relative gap 20%).
4. Stores the first successful run as **Version 0**.

A later **Calculate** with no pending delay does **not** replace Version 0. It writes a new version with the current Project Variables (for example a different crew count). Delay re-optimization still creates a new version as well.

If no feasible solution is found, no version is written. Check capacities, precedence, and the start date.

The schedule table shows each module’s planned times. Status is derived from “now” versus production start and installation finish.

**Export** writes an Excel workbook of the current version (requires `openpyxl`).

### 4. Record delays and re-optimize

On **Schedule**, double-click a delay cell for **Production**, **Transport**, or **Installation**.

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

**Dashboard** shows:

- **Planned vs Actual** — share of modules on the latest version whose installation finish is at or before now
- **Critical Tasks** — distinct modules that have any delay record (pending or already applied)
- **Start Date**, **Forecast Completion**, and how many modules currently sit in factory or site storage

### 6. Comparison

1. Open **Comparison**.
2. Choose an **Upper** and a **Lower** version.
3. Click **Compare**.

Operational metrics (working-hour indices / batches):

- Construction hours
- Factory storage module hours
- Site storage module hours
- Transport batch number

Percentage change on operational cards is relative to the **Lower** version. If Lower is zero, the percentage is shown as `n/a`.

### 7. Costs

**Costs** compares monetised values. Unit costs live on this page only; they are not written to the database.

1. Open **Costs**.
2. Enter unit costs (they apply to both panels). Defaults are Swiss-market figures in **CHF**; replace them with your own costs if needed.
3. Optionally switch the currency label to **EUR**. This changes the displayed abbreviation only; there is no exchange-rate conversion.
4. Pick a version in the **upper** panel (the chosen / new schedule) and one in the **lower** panel. The default is the latest version versus **Version 0**. The upper total also shows the difference versus the lower panel.

Nearby households and occupied area stay empty until you enter them. Occupant and biodiversity money is then shown as “—”. Crew counts come from the Project Variables snapshot stored with each Calculate, not from the live page.

**Default unit costs**

| Input | Default | Basis |
| --- | --- | --- |
| Crane cost | **1500** CHF/day | Daily hire of a crane with an operator (Flottek GmbH, n.d.; Rentit AG, n.d.) |
| Working crew cost | **1313** CHF/crew/day | Employer cost for a four-person crew (one foreman and three workers), including social contributions, pensions and insurance at 22.5% (Schweizerischer Baumeisterverband, 2026, p. 2) |
| Cost per truck | **500** CHF/truck | Indicative price for about a two-hour haul; replace with the actual value (Roger Rohner Transport GmbH, n.d.) |
| Occupant cost | **7** CHF/household/day | Default for monetising disruption to neighbouring households (Çelik et al., 2019) |
| Biodiversity restore price | **50** CHF/m² | Default restoration unit price for lawn, for example turf reinstatement in Switzerland (Ofri, 2026) |

You can add extra daily construction terms on the page. They are added to crane and crew cost before multiplying by working days.

| Category | Quantity from results / settings | User input | Formula |
| --- | --- | --- | --- |
| Construction | Working days (project start through latest installation finish, working calendar) and crew count from that version’s Project Variables | Crane CHF/day, crew CHF/crew/day, optional extra daily terms | days × (crane + crew_rate × crews + extras) |
| Batch | Number of truck trips | Cost per truck | trucks × cost per truck |
| Disruption to occupants | Working days | Cost per household per day, number of nearby households | days × cost/household/day × households |
| Biodiversity | Occupied area (m²) | Restore price per m² | area × price |

Each version panel lists the four categories and a total.

## Input format

CSV, UTF-8, header row required.

| Column | Required | Description |
| --- | --- | --- |
| `Module ID` or `Module_ID` | yes | Unique module identifier |
| `Installation Duration` | yes | Installation length in **working hours** |
| `Production Duration` | yes | Factory production length in **working hours** |
| `Transportation Duration` | yes | Transport length in **working hours** |
| `Installation Precedence` | yes | Predecessor module IDs; empty if none. Multiple predecessors: comma-separated (`VS-02-21,VS-02-22`) |

Durations are non-negative whole numbers on the same hour grid as the working calendar (one time index = one working hour).

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
│   ├── model.py               # CP-SAT scheduler (OR-Tools intervals)
│   ├── warm_start.py          # constructive heuristic (horizon + references)
│   ├── rescheduler.py         # delay application and re-opt constraints
│   ├── datamanager.py         # SQLite schema and project tables
│   ├── costs.py               # unit-rate defaults and monetised cost formulas
│   └── ui/                    # PyQt6 pages, dialogs, widgets
│       └── costs_page.py      # Costs page
└── tests/                     # scripts for selected policies and metrics
```

## Solver notes

- One time period is one **working hour** on the calendar from Project Variables (weekends and non-working days are skipped).
- Truck loads are batched (typically 3–5 modules; one partial load is allowed).
- The heuristic chooses \(T\) (so search stays on a short horizon) and is also given to CP-SAT as a solution hint.
- Default CP-SAT limits: time **120** seconds, relative gap **0.2** (20%). Production, installation, site storage, and factory storage are cumulative constraints on interval variables.

## Disclaimer

This software is a **prototype** prepared at ETH Zurich in the context of **RENOMIZE**. It is provided for research and demonstration. ETH Zurich, the authors, and project partners accept no liability for decisions made on the basis of its output.

OR-Tools is used under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0). See the [OR-Tools documentation](https://developers.google.com/optimization) for details.

## Contact

Zhaoyu Wang — [zhaoyu.wang@ibi.baug.ethz.ch](mailto:zhaoyu.wang@ibi.baug.ethz.ch)
Dr. Arnor Elvarsson — [elvarsson@ibi.baug.ethz.ch](mailto:elvarsson@ibi.baug.ethz.ch)

## References

Çelik, T., Arayici, Y., & Budayan, C. (2019). Assessing the social cost of housing projects on the built environment: Analysis and monetization of the adverse impacts incurred on the neighbouring communities. *Environmental Impact Assessment Review, 77*, 1–10. https://doi.org/10.1016/j.eiar.2019.03.001

Flottek GmbH. (n.d.). *Vermietung*. https://flottek.ch/vermietung.html

Ofri. (2026, August 11). *Neuen Rasen anlegen – Kosten und Preise in der Schweiz*. https://www.ofri.ch/kosten/rasen-anlegen

Rentit AG. (n.d.). *LKW-Kran bis 37m Hubhöhe mit Bedienung*. https://www.rentit.ch/vermietung/hebetechnik/kran/detail/ak-37-150

Roger Rohner Transport GmbH. (n.d.). *Preise*. https://www.rogerrohner.ch/pricing/

Schweizerischer Baumeisterverband. (2026). *Zusatzvereinbarung Lohn LMV 2026, Anhang I: Tabellen der Mindestlöhne für 2026*. https://shop.baumeister.swiss/shop/document_download.php?document=Anhang+I+-+Lohntabelle+2026.pdf
