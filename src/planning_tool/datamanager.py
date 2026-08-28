# data_manager.py
from __future__ import annotations
import json
import pandas as pd
from sqlalchemy import text, Engine, inspect

class ScheduleDataManager:
    # Optional columns kept for older projects. The solver objective is now
    # monetised (construction-day cost and transport-batch cost) and no longer
    # divides terms by these references.
    NORMALIZATION_COLUMNS = (
        "ref_duration",
        "ref_transport",
        "ref_site_storage",
        "ref_factory_storage",
    )

    def __init__(self, engine: Engine):
        self.engine = engine
        self.ensure_schema()

    def ensure_schema(self):
        """sharedtemplate"""
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS projects (
              project_id     INTEGER PRIMARY KEY,
              project_name   TEXT NOT NULL UNIQUE
            );
            """)

            existing = {
                row[1] for row in conn.exec_driver_sql("PRAGMA table_info(projects)").fetchall()
            }
            for column in self.NORMALIZATION_COLUMNS:
                if column not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE projects ADD COLUMN {column} REAL")
            if "start_datetime" not in existing:
                conn.exec_driver_sql(
                    "ALTER TABLE projects ADD COLUMN start_datetime TEXT"
                )

    def get_normalization_reference(self, project_id: int) -> dict | None:
        """Stored reference values, or None if this project has never been optimized."""
        columns = ", ".join(self.NORMALIZATION_COLUMNS)
        with self.engine.begin() as conn:
            row = conn.execute(
                text(f"SELECT {columns} FROM projects WHERE project_id = :pid"),
                {"pid": project_id},
            ).fetchone()

        if row is None or any(value is None for value in row):
            return None
        return dict(zip(self.NORMALIZATION_COLUMNS, (float(v) for v in row)))

    def set_normalization_reference(self, project_id: int, reference: dict) -> None:
        assignments = ", ".join(f"{c} = :{c}" for c in self.NORMALIZATION_COLUMNS)
        params = {c: float(reference[c]) for c in self.NORMALIZATION_COLUMNS}
        params["pid"] = project_id
        with self.engine.begin() as conn:
            conn.execute(
                text(f"UPDATE projects SET {assignments} WHERE project_id = :pid"),
                params,
            )

    def get_project_start_date(self, project_id: int) -> str | None:
        """Stored start date for this project, or None if it has not been set."""
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT start_datetime FROM projects WHERE project_id = :pid"
                ),
                {"pid": project_id},
            ).fetchone()
        if row is None or row[0] is None:
            return None
        value = str(row[0]).strip()
        if not value or value.lower() == "mm/dd/yyyy":
            return None
        return value

    def set_project_start_date(self, project_id: int, start_str: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE projects SET start_datetime = :start_datetime "
                    "WHERE project_id = :pid"
                ),
                {"start_datetime": start_str, "pid": project_id},
            )

    # --------- table names ---------

    @staticmethod
    def raw_table_name(project_id: int) -> str:
        """raw_schedule_{project_id}: Input data from user's file (read-only)"""
        return f"raw_schedule_{project_id}"

    @staticmethod
    def solution_table_name(project_id: int) -> str:
        """solution_schedule_{project_id}: Optimization solution results"""
        return f"solution_schedule_{project_id}"

    @staticmethod
    def summary_table_name(project_id: int) -> str:
        """optimization_summary_{project_id}: Project-level summary statistics"""
        return f"optimization_summary_{project_id}"

    @staticmethod
    def factory_inventory_table_name(project_id: int) -> str:
        """factory_inventory_{project_id}: Factory inventory levels over time"""
        return f"factory_inventory_{project_id}"

    @staticmethod
    def site_inventory_table_name(project_id: int) -> str:
        """site_inventory_{project_id}: Site inventory levels over time"""
        return f"site_inventory_{project_id}"
    
    @staticmethod
    def delay_updates_table_name(project_id: int) -> str:
        """delay_updates_{project_id}: Delay records for re-optimization"""
        return f"delay_updates_{project_id}"
    
    @staticmethod
    def optimization_versions_table_name(project_id: int) -> str:
        """optimization_versions_{project_id}: Version history of optimizations"""
        return f"optimization_versions_{project_id}"


    # --------- first import: create raw table + other tables for the project ---------

    def create_project_from_csv(self, project_name: str, csv_path: str) -> int:
        """
        Create a new project from a CSV file.
        - Insert a project row and obtain project_id
        - Create raw_schedule_{project_id} and import the CSV
        - Create empty solution/summary/factory/site tables for the project
        - Make the raw table read-only
        """
        # 1) Register the project and obtain project_id
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO projects(project_name) VALUES (:n)",
                {"n": project_name}
            )
            project_id = conn.execute(text(
                "SELECT project_id FROM projects WHERE project_name=:n"
            ), {"n": project_name}).scalar_one()

        # 2) Create the raw table and import the CSV
        raw = ScheduleDataManager.raw_table_name(project_id)
        df = pd.read_csv(csv_path)
        df.to_sql(
            raw,
            self.engine,
            if_exists="replace",
            index=False,
            method="multi",
            chunksize=1000,
        )

        # 3) Make the raw table read-only via SQLite triggers
        with self.engine.begin() as conn:
            for op in ("INSERT", "UPDATE", "DELETE"):
                conn.exec_driver_sql(f"""
                CREATE TRIGGER IF NOT EXISTS trg_no_{op.lower()}_{raw}
                BEFORE {op} ON "{raw}"
                BEGIN
                    SELECT RAISE(ABORT, 'raw table is read-only');
                END;
                """)
        
        # 4) Create delay and version tables
        self._ensure_delay_and_version_tables(project_id)

        return project_id

    def _raw_column_map(self, project_id: int) -> dict[str, str]:
        raw = self.raw_table_name(project_id)
        inspector = inspect(self.engine)
        if raw not in inspector.get_table_names():
            raise ValueError("This project has no input table. Upload a CSV first.")
        columns = [col["name"] for col in inspector.get_columns(raw)]

        def pick(*names: str) -> str | None:
            for name in names:
                if name in columns:
                    return name
            return None

        id_col = pick("Module_ID", "Module ID")
        install_col = pick("Installation Duration")
        prod_col = pick("Production Duration")
        trans_col = pick("Transportation Duration")
        pred_col = pick("Installation Precedence")
        missing = [
            label
            for label, col in (
                ("Module ID", id_col),
                ("Installation Duration", install_col),
                ("Production Duration", prod_col),
                ("Transportation Duration", trans_col),
            )
            if col is None
        ]
        if missing:
            raise ValueError(
                "The input table is missing required columns:\n"
                + "\n".join(f"    - {name}" for name in missing)
            )
        return {
            "raw": raw,
            "module_id": id_col,
            "installation": install_col,
            "production": prod_col,
            "transport": trans_col,
            "precedence": pred_col,
        }

    def list_raw_module_ids(self, project_id: int) -> list[str]:
        mapping = self._raw_column_map(project_id)
        raw = mapping["raw"]
        id_col = mapping["module_id"]
        df = pd.read_sql_table(raw, self.engine)
        ids = []
        for value in df[id_col].tolist():
            if pd.isna(value):
                continue
            text_id = str(value).strip()
            if text_id:
                ids.append(text_id)
        return ids

    def add_raw_module(
        self,
        project_id: int,
        *,
        module_id: str,
        installation_duration: int,
        production_duration: int,
        transportation_duration: int,
        precedence: str | None = None,
    ) -> None:
        """Append one module to the project's input table for the next Calculate."""
        module_id = (module_id or "").strip()
        if not module_id:
            raise ValueError("Module name cannot be empty.")
        for label, value in (
            ("Installation Duration", installation_duration),
            ("Production Duration", production_duration),
            ("Transportation Duration", transportation_duration),
        ):
            if int(value) < 1:
                raise ValueError(f"{label} must be a positive whole number.")

        mapping = self._raw_column_map(project_id)
        existing = self.list_raw_module_ids(project_id)
        if module_id in existing:
            raise ValueError(f'Module "{module_id}" already exists.')

        preds = [p.strip() for p in str(precedence or "").split(",") if p.strip()]
        if module_id in preds:
            raise ValueError("A module cannot precede itself.")
        unknown = [p for p in preds if p not in existing]
        if unknown:
            raise ValueError(
                "These predecessor IDs are not in the project:\n"
                + "\n".join(f"    - {p}" for p in unknown)
            )
        precedence_value = ", ".join(preds) if preds else None

        raw = mapping["raw"]
        columns = [
            mapping["module_id"],
            mapping["installation"],
            mapping["production"],
            mapping["transport"],
        ]
        params = {
            "module_id": module_id,
            "installation": int(installation_duration),
            "production": int(production_duration),
            "transport": int(transportation_duration),
        }
        placeholders = [":module_id", ":installation", ":production", ":transport"]
        if mapping["precedence"]:
            columns.append(mapping["precedence"])
            placeholders.append(":precedence")
            params["precedence"] = precedence_value

        quoted_cols = ", ".join(f'"{col}"' for col in columns)
        insert_sql = f'INSERT INTO "{raw}" ({quoted_cols}) VALUES ({", ".join(placeholders)})'
        trigger = f"trg_no_insert_{raw}"
        with self.engine.begin() as conn:
            conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")
            conn.execute(text(insert_sql), params)
            conn.exec_driver_sql(
                f"""
                CREATE TRIGGER IF NOT EXISTS {trigger}
                BEFORE INSERT ON "{raw}"
                BEGIN
                    SELECT RAISE(ABORT, 'raw table is read-only');
                END;
                """
            )

    def _ensure_delay_and_version_tables(self, project_id: int):
        """Create delay_updates and optimization_versions tables for a project"""
        delay_table = ScheduleDataManager.delay_updates_table_name(project_id)
        versions_table = ScheduleDataManager.optimization_versions_table_name(project_id)
        
        with self.engine.begin() as conn:
            # Create delay_updates table
            conn.exec_driver_sql(f"""
                CREATE TABLE IF NOT EXISTS "{delay_table}" (
                    delay_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_id TEXT NOT NULL,
                    delay_type TEXT NOT NULL CHECK(delay_type IN ('DURATION_EXTENSION', 'START_POSTPONEMENT')),
                    phase TEXT NOT NULL CHECK(phase IN ('FABRICATION', 'TRANSPORT', 'INSTALLATION')),
                    delay_hours REAL NOT NULL,
                    detected_at_time INTEGER NOT NULL,
                    detected_at_datetime TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    version_id INTEGER,
                    FOREIGN KEY (version_id) REFERENCES "{versions_table}"(version_id)
                );
            """)
            
            # Create optimization_versions table
            conn.exec_driver_sql(f"""
                CREATE TABLE IF NOT EXISTS "{versions_table}" (
                    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_number INTEGER NOT NULL UNIQUE,
                    base_version_id INTEGER,
                    reoptimize_from_time INTEGER,
                    delay_ids TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    objective_value REAL,
                    status INTEGER,
                    project_start_datetime TEXT,
                    settings_json TEXT,
                    FOREIGN KEY (base_version_id) REFERENCES "{versions_table}"(version_id)
                );
            """)
            
            # Add project_start_datetime column if it doesn't exist (for existing tables)
            try:
                conn.exec_driver_sql(f'ALTER TABLE "{versions_table}" ADD COLUMN project_start_datetime TEXT')
            except Exception:
                # Column already exists, ignore
                pass

            self._ensure_column(conn, versions_table, "settings_json", "TEXT")
            
            # Create index on version_number for faster queries (UNIQUE constraint already creates an index, but keep this for clarity)
            conn.exec_driver_sql(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_version_number_{project_id} 
                ON "{versions_table}"(version_number);
            """)

    @staticmethod
    def _ensure_column(conn, table_name: str, column: str, ddl: str) -> None:
        existing = {
            row[1] for row in conn.exec_driver_sql(f'PRAGMA table_info("{table_name}")').fetchall()
        }
        if column not in existing:
            conn.exec_driver_sql(f'ALTER TABLE "{table_name}" ADD COLUMN {column} {ddl}')

    def ensure_delay_and_version_tables(self, project_id: int) -> None:
        """Public wrapper so Calculate / Costs can migrate existing projects."""
        self._ensure_delay_and_version_tables(project_id)

    @staticmethod
    def serialize_calculate_settings(settings: dict | None) -> str:
        return json.dumps(dict(settings or {}), default=str)

    @staticmethod
    def parse_calculate_settings(raw) -> dict:
        if raw is None:
            return {}
        try:
            if pd.isna(raw):
                return {}
        except (TypeError, ValueError):
            pass
        if isinstance(raw, dict):
            return raw
        text_value = str(raw).strip()
        if not text_value:
            return {}
        try:
            data = json.loads(text_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def crew_count_from_settings(settings: dict | None, default: int = 1) -> int:
        if not settings:
            return default
        raw = settings.get("crew_count", default)
        try:
            return max(0, int(float(raw)))
        except (TypeError, ValueError):
            return default

    # --------- queries / metadata ---------

    def list_projects(self):
        """Return all projects as a list of dicts sorted by project_id."""
        with self.engine.begin() as conn:
            rows = conn.execute(
                text("SELECT project_id, project_name FROM projects ORDER BY project_id")
            ).fetchall()
        return [
            {"project_id": row[0], "project_name": row[1]}
            for row in rows
        ]
    
    def delete_project(self, project_id: int) -> bool:
        """
        Delete a project and all its associated tables.
        
        This will:
        1. Drop all project-specific tables (raw, solution, summary, factory_inventory, site_inventory)
        2. Delete the project record from projects table
        
        Args:
            project_id: The ID of the project to delete
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with self.engine.begin() as conn:
                # Get all table names for this project
                tables_to_drop = [
                    ScheduleDataManager.raw_table_name(project_id),
                    ScheduleDataManager.solution_table_name(project_id),
                    ScheduleDataManager.summary_table_name(project_id),
                    ScheduleDataManager.factory_inventory_table_name(project_id),
                    ScheduleDataManager.site_inventory_table_name(project_id),
                    ScheduleDataManager.delay_updates_table_name(project_id),
                    ScheduleDataManager.optimization_versions_table_name(project_id),
                ]
                
                # Drop all project tables (if they exist)
                for table_name in tables_to_drop:
                    conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{table_name}"')
                
                # Delete triggers for raw table (if they exist)
                raw_table = ScheduleDataManager.raw_table_name(project_id)
                for op in ("INSERT", "UPDATE", "DELETE"):
                    conn.exec_driver_sql(f'DROP TRIGGER IF EXISTS trg_no_{op.lower()}_{raw_table}')
                
                # Delete the project record
                conn.exec_driver_sql(
                    "DELETE FROM projects WHERE project_id = :pid",
                    {"pid": project_id}
                )
            
            return True
        except Exception as e:
            print(f"Error deleting project {project_id}: {e}")
            return False
