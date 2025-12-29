"""
临时脚本：分析数据库表结构和内容
"""
import sqlite3

# 连接到数据库
conn = sqlite3.connect("input_database.db")
cursor = conn.cursor()

# 获取所有表
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
all_tables = [row[0] for row in cursor.fetchall()]

print("=" * 80)
print("数据库中的所有表：")
print("=" * 80)
for table in sorted(all_tables):
    print(f"  - {table}")

# 对于 project_id = 1，分析相关表
project_id = 1
print("\n" + "=" * 80)
print(f"分析 Project {project_id} 的表结构：")
print("=" * 80)

tables_to_check = [
    f"raw_schedule_{project_id}",
    f"solution_schedule_{project_id}",
    f"optimization_versions_{project_id}",
    f"delay_updates_{project_id}",
    f"optimization_summary_{project_id}",
    f"factory_inventory_{project_id}",
    f"site_inventory_{project_id}",
]

for table_name in tables_to_check:
    if table_name in all_tables:
        print(f"\n表: {table_name}")
        print("-" * 80)
        
        # 获取列信息
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns = cursor.fetchall()
        print("列结构：")
        for col in columns:
            # PRAGMA table_info 返回: (cid, name, type, notnull, default_value, pk)
            print(f"  - {col[1]}: {col[2]} (nullable: {not col[3]}, pk: {col[5]})")
        
        # 获取行数
        cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        count = cursor.fetchone()[0]
        print(f"\n总行数: {count}")
        
        # 如果是关键表，显示一些示例数据
        if "solution_schedule" in table_name or "optimization_versions" in table_name:
            print("\n示例数据（前5行）：")
            cursor.execute(f'SELECT * FROM "{table_name}" LIMIT 5')
            rows = cursor.fetchall()
            if rows:
                # 获取列名
                col_names = [desc[0] for desc in cursor.description]
                print(f"列名: {', '.join(col_names)}")
                for i, row in enumerate(rows, 1):
                    print(f"行 {i}: {row}")
                
                # 如果是 solution_schedule，检查 version_id 的分布
                if "solution_schedule" in table_name:
                    # 检查是否有 version_id 列
                    col_names_lower = [name.lower() for name in col_names]
                    if 'version_id' in col_names_lower:
                        print("\nversion_id 的分布：")
                        cursor.execute(f'SELECT version_id, COUNT(*) as count FROM "{table_name}" GROUP BY version_id')
                        version_dist = cursor.fetchall()
                        for vid, cnt in version_dist:
                            print(f"  version_id={vid}: {cnt} 行")
                    else:
                        print("\n警告：solution_schedule 表中没有 version_id 列！")
            
            # 如果是 optimization_versions，显示所有版本
            if "optimization_versions" in table_name:
                print("\n所有版本记录：")
                cursor.execute(f'SELECT version_id, version_number, base_version_id, created_at, objective_value, status FROM "{table_name}" ORDER BY version_id')
                versions = cursor.fetchall()
                col_names = [desc[0] for desc in cursor.description]
                print(f"列名: {', '.join(col_names)}")
                for row in versions:
                    print(f"  {row}")

conn.close()
print("\n" + "=" * 80)
print("分析完成")
print("=" * 80)

