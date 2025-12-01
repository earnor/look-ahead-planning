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
        raw = self.raw_table_name(project_id)
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

        return project_id

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
                    self.raw_table_name(project_id),
                    self.solution_table_name(project_id),
                    self.summary_table_name(project_id),
                    self.factory_inventory_table_name(project_id),
                    self.site_inventory_table_name(project_id),
                ]
                
                # Drop all project tables (if they exist)
                for table_name in tables_to_drop:
                    conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{table_name}"')
                
                # Delete triggers for raw table (if they exist)
                raw_table = self.raw_table_name(project_id)
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
