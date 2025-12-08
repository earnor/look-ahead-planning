# Version ID 逻辑说明

## 当前实现状态

### version_id 为 NULL 的情况：
1. **首次计算**：用户点击 Calculate 按钮进行第一次优化
   - `save_results_to_db()` 调用时，`version_id` 参数为 `None`（默认值）
   - 保存的记录的 `version_id` 字段为 `NULL`
   - 使用 `if_exists='replace'` 替换旧数据

2. **重新计算**：用户修改设置后再次点击 Calculate
   - 同样 `version_id=None`
   - 使用 `if_exists='replace'` 替换所有旧数据
   - **注意**：会丢失之前的计算结果

### version_id 有值的情况（未来实现）：
1. **重优化（Re-optimization）**：当有延迟信息时进行重优化
   - 应该在 `optimization_versions` 表中创建新版本记录
   - 获得自增的 `version_id`
   - 在 `save_results_to_db()` 中传递这个 `version_id`
   - 使用 `if_exists='append'` 追加新版本，保留历史

## 数据库表结构

### solution_schedule_{project_id} 表：
- 包含 `version_id` 列（可为 NULL）
- `version_id = NULL`：初始计算或重新计算的结果
- `version_id = 整数`：重优化版本的 ID

### optimization_versions_{project_id} 表：
- `version_id`：主键，自增
- `version_number`：版本号（1, 2, 3...）
- `base_version_id`：基于哪个版本重优化（外键）
- `reoptimize_from_time`：从哪个时间点开始重优化
- `delay_ids`：关联的延迟记录 ID

## 当前代码调用链

```python
# main.py
scheduler.save_results_to_db(
    self.engine,
    self.current_project_id,
    module_id_mapping=index_to_id
    # version_id 参数未传递，默认为 None
)

# model.py
def save_results_to_db(..., version_id: Optional[int] = None):
    if version_id is None:
        # 使用 replace，清除所有旧数据
        results_df.to_sql(..., if_exists='replace')
    else:
        # 使用 append，追加新版本
        results_df.to_sql(..., if_exists='append')
```

## 建议的改进

1. **首次计算**：version_id = NULL，使用 replace
2. **重新计算（无延迟）**：应该先清除所有 NULL 版本，然后 replace
3. **重优化（有延迟）**：
   - 创建新版本记录（version_id = 新ID）
   - 使用 append 保存新版本
   - 保留所有历史版本

