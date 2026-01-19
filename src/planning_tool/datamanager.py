# data_manager.py
from __future__ import annotations
import pandas as pd
from sqlalchemy import text, Engine

class ScheduleDataManager:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.ensure_schema()

    def ensure_schema(self):
        """全局只建一次的公共表结构"""
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS projects (
              project_id     INTEGER PRIMARY KEY,
              project_name   TEXT NOT NULL UNIQUE
            );
            """)

    # --------- 各类表名约定（一个 project_id 对应一套表） ---------

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


    # --------- 第一次导入：用 CSV 建 raw 表 + 建该项目的其余表 ---------

    def create_project_from_csv(self, project_name: str, csv_path: str) -> int:
        """
        Create a new project from a CSV file.
        - 在 projects 表中创建一条项目记录并获得 project_id
        - 创建该项目的 raw_schedule_{project_id} 表并导入 CSV
        - 创建该项目的 solution/summary/factory/site 等子表（先是空表骨架）
        - 将 raw 表设为只读
        """
        # 1) 在 projects 表注册项目，获得 project_id
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO projects(project_name) VALUES (:n)",
                {"n": project_name}
            )
            project_id = conn.execute(text(
                "SELECT project_id FROM projects WHERE project_name=:n"
            ), {"n": project_name}).scalar_one()

        # 2) 建 raw 表并导入 CSV
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

        # 3 把 raw 设成只读（通过 SQLite 触发器）
        with self.engine.begin() as conn:
            for op in ("INSERT", "UPDATE", "DELETE"):
                conn.exec_driver_sql(f"""
                CREATE TRIGGER IF NOT EXISTS trg_no_{op.lower()}_{raw}
                BEFORE {op} ON "{raw}"
                BEGIN
                    SELECT RAISE(ABORT, 'raw table is read-only');
                END;
                """)
        
        # 4) 创建延迟和版本管理表
        self._ensure_delay_and_version_tables(project_id)

        return project_id
    
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
                    FOREIGN KEY (base_version_id) REFERENCES "{versions_table}"(version_id)
                );
            """)
            
            # Add project_start_datetime column if it doesn't exist (for existing tables)
            try:
                conn.exec_driver_sql(f'ALTER TABLE "{versions_table}" ADD COLUMN project_start_datetime TEXT')
            except Exception:
                # Column already exists, ignore
                pass
            
            # Create index on version_number for faster queries (UNIQUE constraint already creates an index, but keep this for clarity)
            conn.exec_driver_sql(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_version_number_{project_id} 
                ON "{versions_table}"(version_number);
            """)

    # --------- 查询 / 元数据 ---------

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
    
    def delete_version(self, project_id: int, version_id: int) -> bool:
        """
        Delete a specific version and all its associated data.
        
        This will:
        1. Delete the version record from optimization_versions table
        2. Delete solution data for this version from solution_schedule table
        3. Delete summary data for this version from optimization_summary table
        4. Note: Delay records are kept (they may be referenced by other versions or serve as history)
        
        Args:
            project_id: The ID of the project
            version_id: The ID of the version to delete
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with self.engine.begin() as conn:
                from sqlalchemy import inspect
                inspector = inspect(self.engine)
                table_names = inspector.get_table_names()
                
                # Get table names
                solution_table = ScheduleDataManager.solution_table_name(project_id)
                summary_table = ScheduleDataManager.summary_table_name(project_id)
                versions_table = ScheduleDataManager.optimization_versions_table_name(project_id)
                
                # Check if tables exist
                if versions_table not in table_names:
                    print(f"Version table {versions_table} does not exist")
                    return False
                
                # Check if version exists and get version_number for logging
                version_check_query = text(f'SELECT version_number FROM "{versions_table}" WHERE version_id = :version_id')
                version_number = conn.execute(version_check_query, {"version_id": version_id}).scalar()
                
                if version_number is None:
                    print(f"Version with version_id {version_id} does not exist")
                    return False
                
                # Delete solution data for this version
                if solution_table in table_names:
                    delete_solution_query = text(f'DELETE FROM "{solution_table}" WHERE version_id = :version_id')
                    conn.execute(delete_solution_query, {"version_id": version_id})
                
                # Delete summary data for this version
                if summary_table in table_names:
                    delete_summary_query = text(f'DELETE FROM "{summary_table}" WHERE version_id = :version_id')
                    conn.execute(delete_summary_query, {"version_id": version_id})
                
                # Delete delay records associated with this version
                delay_table = ScheduleDataManager.delay_updates_table_name(project_id)
                if delay_table in table_names:
                    delete_delays_query = text(f'DELETE FROM "{delay_table}" WHERE version_id = :version_id')
                    conn.execute(delete_delays_query, {"version_id": version_id})
                
                # Delete the version record itself
                # Note: We need to handle foreign key constraints if other versions reference this version as base_version_id
                # First, check if any other versions depend on this version
                dependent_versions_query = text(f'SELECT version_id, version_number FROM "{versions_table}" WHERE base_version_id = :version_id')
                dependent_versions = conn.execute(dependent_versions_query, {"version_id": version_id}).fetchall()
                
                if dependent_versions:
                    # Set base_version_id to NULL for dependent versions (they become independent)
                    # Or optionally, we could prevent deletion if there are dependent versions
                    # For now, we'll set base_version_id to NULL to allow deletion
                    update_dependent_query = text(f'UPDATE "{versions_table}" SET base_version_id = NULL WHERE base_version_id = :version_id')
                    conn.execute(update_dependent_query, {"version_id": version_id})
                
                # Now delete the version record
                delete_version_query = text(f'DELETE FROM "{versions_table}" WHERE version_id = :version_id')
                conn.execute(delete_version_query, {"version_id": version_id})
                
                print(f"Successfully deleted version {version_number} (version_id: {version_id})")
            
            return True
        except Exception as e:
            print(f"Error deleting version {version_id} for project {project_id}: {e}")
            import traceback
            traceback.print_exc()
            return False