"""批量升级调度器 —— 按 cabinet 分批，避免同 cabinet 同时断电。

调度策略：
- 同一 cabinet 的 blade 串行执行（升级 + 复位需逐台完成）
- 不同 cabinet 间并行（受 parallel 限制）
- 批次间无等待（前一批全部完成后开始下一批）

由 UpgradeOrchestrator._run_batched() 在 batch 场景下调用。
"""

from collections import defaultdict


def schedule_by_cabinet(blades: list, parallel: int = 8) -> list:
    """将 blade 列表按 cabinet 分组为分批执行计划。

    :param blades: blade dict 列表（须含 cabinet_id 字段）
    :param parallel: 并行度（同时处理的 cabinet 组数）
    :return: list[list[dict]]，每个子列表是一个批次

    策略：
    1. 按 cabinet_id 分组
    2. 每批取 `parallel` 个 cabinet 组的所有 blade
    3. 同一 cabinet 组内的 blade 在同一批次中（由 orchestrator 串行处理）
    """
    by_cabinet = defaultdict(list)
    for blade in blades:
        cab_id = blade.get("cabinet_id")
        if cab_id is not None:
            by_cabinet[cab_id].append(blade)

    cabinet_groups = list(by_cabinet.values())
    batches = []
    for i in range(0, len(cabinet_groups), parallel):
        batch = []
        for group in cabinet_groups[i:i + parallel]:
            batch.extend(group)
        batches.append(batch)
    return batches
