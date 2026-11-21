"""抄表结算纯函数引擎。

累计计数器跨日不清零，直接按读数导入会重复计算客流。本引擎以相邻确认读数之差
生成每日进区与完成人数，并处理换机代次、缺日补报、倒退与口径异常隔离。

所有函数均为无副作用纯函数，便于单元测试与重算复用；数据库/并发逻辑在 services 层。
"""
from datetime import date, timedelta


def iso(value):
    """校验并归一化 ISO 日期字符串 (YYYY-MM-DD)。"""
    if not isinstance(value, str):
        raise ValueError('日期必须为 YYYY-MM-DD 字符串')
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError('日期格式必须为 YYYY-MM-DD')


def validate_totals(entry_total, completed_total):
    """累计读数必须为非负整数，且完成累计不超过进区累计。"""
    if isinstance(entry_total, bool) or isinstance(completed_total, bool):
        raise ValueError('读数必须为非负整数，不能为布尔值')
    if not isinstance(entry_total, int) or not isinstance(completed_total, int):
        raise ValueError('读数必须为非负整数')
    if entry_total < 0 or completed_total < 0:
        raise ValueError('累计读数不能为负数')
    if completed_total > entry_total:
        raise ValueError('完成讲解累计不能超过进区累计')


def _d(day):
    return date.fromisoformat(day)


def settle(readings):
    """按设备代次内的确认读数序列生成结算行。

    入参 readings: 同一设备同一代次的读数 dict 列表，每项含
        reading_date / generation / entry_total / completed_total。
    返回按日期排序的结算行 dict 列表：
        - active：相邻日读数差得到的有效进区/完成增量
        - missing：自然日缺读数，等待补报
        - broken：与上一确认读数不相邻，无法取差
        - quarantined：代次内倒退，或完成增量超过进区增量，隔离不参与口径
    代次首日（含换机后首日）只建立基线，不产生增量。
    """
    rows = sorted(readings, key=lambda r: r['reading_date'])
    lines = []
    previous = None
    for cur in rows:
        day = cur['reading_date']
        gen = cur['generation']
        entry, completed = cur['entry_total'], cur['completed_total']
        if previous is None:
            lines.append(_line(day, None, gen, 'active', None, None,
                               '代次首日基线读数，无相邻日不产生增量'))
            previous = cur
            continue
        prev_day = _d(previous['reading_date'])
        cur_day = _d(day)
        # 中间缺日：为每个没有读数的自然日登记缺日行
        cursor = prev_day + timedelta(days=1)
        while cursor < cur_day:
            lines.append(_line(cursor.isoformat(), previous['reading_date'], gen,
                               'missing', None, None, '该自然日缺少读数，等待补报'))
            cursor += timedelta(days=1)
        if cur_day != prev_day + timedelta(days=1):
            lines.append(_line(day, previous['reading_date'], gen, 'broken', None, None,
                               '与上一确认读数不相邻，无法按相邻日取差，待缺日补报'))
        else:
            entry_delta = entry - previous['entry_total']
            completed_delta = completed - previous['completed_total']
            if entry_delta < 0 or completed_delta < 0:
                lines.append(_line(day, previous['reading_date'], gen, 'quarantined',
                                   entry_delta, completed_delta,
                                   '代次内累计读数倒退，已隔离待人工核对'))
            elif completed_delta > entry_delta:
                lines.append(_line(day, previous['reading_date'], gen, 'quarantined',
                                   entry_delta, completed_delta,
                                   '完成增量超过进区增量，已隔离待人工核对'))
            else:
                lines.append(_line(day, previous['reading_date'], gen, 'active',
                                   entry_delta, completed_delta, ''))
        previous = cur
    return lines


def _line(day, prev_day, generation, status, entry_count, completed_count, reason):
    return {
        'reading_date': day,
        'prev_date': prev_day,
        'generation': generation,
        'status': status,
        'entry_count': entry_count,
        'completed_count': completed_count,
        'reason': reason,
    }


def affected_dates(existing_dates, changed_date):
    """上游读数更正/补报后，受影响需要重算的日期窗口。

    改动某日读数只会改变该日及其之后链式结算的行；返回 changed_date 起至
    最后一个已登记读数日之间的全部自然日（含缺日）。
    """
    dates = sorted(existing_dates)
    if not dates:
        return [iso(changed_date)]
    start = _d(iso(changed_date))
    end = _d(dates[-1])
    if start > end:
        return [start.isoformat()]
    out = []
    cursor = start
    while cursor <= end:
        out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def has_blocking_issue(lines):
    """存在隔离/缺日/断档行时口径不可信，用于拦截新分析与简报。"""
    return any(line['status'] in ('quarantined', 'missing', 'broken') for line in lines)


def summarize(lines):
    """汇总当前有效（active）结算行的进区/完成人数；异常行不计入口径。"""
    valid = [x for x in lines if x['status'] == 'active' and x['entry_count'] is not None]
    return {
        'settled_days': len(valid),
        'entry_count': sum(x['entry_count'] for x in valid),
        'completed_count': sum(x['completed_count'] for x in valid),
        'quarantined_days': sum(1 for x in lines if x['status'] == 'quarantined'),
        'missing_days': sum(1 for x in lines if x['status'] == 'missing'),
        'broken_days': sum(1 for x in lines if x['status'] == 'broken'),
    }
