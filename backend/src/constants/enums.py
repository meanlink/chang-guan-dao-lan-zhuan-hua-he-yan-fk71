from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'
class LineStatus(str, Enum):
    # 有效结算行：由相邻确认读数差生成
    active = 'active'
    # 缺日：该自然日尚无读数，等待补报
    missing = 'missing'
    # 断档：与上一确认读数不相邻，无法取差，待补报
    broken = 'broken'
    # 隔离：代次内读数倒退，或完成增量超过进区增量
    quarantined = 'quarantined'
    # 失效：上游读数更正后被整组替换的历史结算行，保留归档
    superseded = 'superseded'
class BatchStatus(str, Enum):
    # 重算中：受影响日期正在切换，闸门拦截新分析与简报
    recalculating = 'recalculating'
    # 当前有效批次
    active = 'active'
    # 过期执行：执行时读数版本已变化，结果被拒绝写回
    stale = 'stale'
    # 已被更新批次整组取代
    superseded = 'superseded'
class ReadingStatus(str, Enum):
    confirmed = 'confirmed'
    corrected = 'corrected'
