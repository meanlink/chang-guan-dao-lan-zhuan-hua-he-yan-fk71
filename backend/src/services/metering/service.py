"""抄表结算服务层：设备登记、读数补报/更正、整组重算与闸门状态。"""
import time
from fastapi import HTTPException
from sqlalchemy import select
from redis import Redis
from src.config.settings import REDIS_URL
from src.models import (
    MeterDevice, MeterReading, MeterCorrection, SettlementBatch, SettlementLine,
)
from src.constants.enums import BatchStatus, ReadingStatus
from src.services.metering import engine

QUEUE_KEY = 'meter:queue'


# ---------- 序列化 ----------
def device_dict(d):
    return {k: getattr(d, k) for k in ('id', 'code', 'zone', 'arm', 'generation', 'note', 'active', 'created_at')}


def reading_dict(r):
    return {k: getattr(r, k) for k in ('id', 'device_id', 'generation', 'reading_date',
                                       'entry_total', 'completed_total', 'version', 'status',
                                       'created_at', 'updated_at')}


def batch_dict(b):
    return {k: getattr(b, k) for k in ('id', 'device_id', 'generation', 'sequence', 'status',
                                       'affected_dates', 'invalid_reason', 'progress',
                                       'total_dates', 'processed_dates', 'trigger',
                                       'created_by', 'created_at', 'finished_at')}


def line_dict(x):
    return {k: getattr(x, k) for k in ('id', 'batch_id', 'device_id', 'generation', 'reading_date',
                                       'prev_date', 'status', 'entry_count', 'completed_count', 'reason')}


def correction_dict(c):
    return {k: getattr(c, k) for k in ('id', 'reading_id', 'device_id', 'reading_date',
                                       'old_entry', 'old_completed', 'new_entry', 'new_completed',
                                       'reason', 'batch_id', 'actor', 'created_at')}


def _lock_device(db, device_id):
    # PostgreSQL 行锁串行化同一设备的更正与结算；SQLite 方言将 FOR UPDATE 视为空操作
    d = db.scalar(select(MeterDevice).where(MeterDevice.id == device_id).with_for_update())
    if not d:
        raise HTTPException(404, '设备不存在')
    return d


def enqueue(batch_id):
    try:
        r = Redis.from_url(REDIS_URL)
        r.rpush(QUEUE_KEY, batch_id)
        r.close()
    except Exception:
        pass  # SQL 中 recalculating 状态是持久兜底，worker 会轮询


# ---------- 设备 ----------
def create_device(db, data, user):
    code = data.code.strip()
    if db.scalar(select(MeterDevice).where(MeterDevice.code == code)):
        raise HTTPException(409, '设备编码已存在，请确认是否为同一台计数器')
    d = MeterDevice(code=code, zone=data.zone.strip(), arm=data.arm.strip(),
                    generation=data.generation, note=data.note.strip(),
                    active=True, created_at=time.time())
    db.add(d)
    db.flush()
    return d


def replace_device(db, device_id, note, user):
    """换机登记：代次递增；新代次首日读数将作为新基线，不与旧代次取差。"""
    d = _lock_device(db, device_id)
    d.generation += 1
    if note:
        d.note = (d.note + ' / ' if d.note else '') + f'第 {d.generation} 代换机：{note.strip()}'
    db.flush()
    return d


# ---------- 读数补报 / 更正 ----------
def upsert_reading(db, data, user):
    day = engine.iso(data.reading_date)
    engine.validate_totals(data.entry_total, data.completed_total)
    d = _lock_device(db, data.device_id)
    existing = db.scalar(select(MeterReading).where(
        MeterReading.device_id == d.id,
        MeterReading.generation == d.generation,
        MeterReading.reading_date == day,
    ))
    now = time.time()
    is_correction = False
    if existing:
        if existing.entry_total == data.entry_total and existing.completed_total == data.completed_total:
            return {'reading': existing, 'batch': _latest_batch(db, d.id, d.generation),
                    'changed': False}
        is_correction = True
        correction = MeterCorrection(
            reading_id=existing.id, device_id=d.id, reading_date=day,
            old_entry=existing.entry_total, old_completed=existing.completed_total,
            new_entry=data.entry_total, new_completed=data.completed_total,
            reason=data.reason.strip(), actor=user.email, created_at=now,
        )
        db.add(correction)
        existing.entry_total = data.entry_total
        existing.completed_total = data.completed_total
        existing.version += 1
        existing.status = ReadingStatus.corrected.value
        existing.updated_at = now
        reading = existing
    else:
        reading = MeterReading(
            device_id=d.id, generation=d.generation, reading_date=day,
            entry_total=data.entry_total, completed_total=data.completed_total,
            version=1, status=ReadingStatus.confirmed.value,
            created_at=now, updated_at=now,
        )
        db.add(reading)
        db.flush()

    batch = _open_recompute_batch(db, d, day, trigger='correction' if is_correction else 'backfill',
                                  actor=user.email, reason=data.reason)
    if is_correction:
        correction.batch_id = batch.id
    # 首个代次的首次读数（初始结算）在请求事务内同步完成；其余走 worker 重算
    prior_batches = db.scalar(select(SettlementBatch).where(
        SettlementBatch.device_id == d.id,
        SettlementBatch.generation == d.generation,
        SettlementBatch.id != batch.id,
    ))
    if prior_batches is None:
        run_recompute(db, batch.id)
    else:
        db.commit()
        enqueue(batch.id)
    return {'reading': reading, 'batch': batch, 'changed': True}


def _open_recompute_batch(db, device, changed_date, trigger, actor, reason=''):
    """登记一个重算批次；同代次已在排队的批次立即判过期，由版本戳兜底拒绝写回。"""
    readings = list(db.scalars(select(MeterReading).where(
        MeterReading.device_id == device.id,
        MeterReading.generation == device.generation,
    ).order_by(MeterReading.reading_date)))
    dates = [r.reading_date for r in readings]
    affected = engine.affected_dates(dates, changed_date)
    last_seq = db.scalar(select(SettlementBatch.sequence).where(
        SettlementBatch.device_id == device.id,
        SettlementBatch.generation == device.generation,
    ).order_by(SettlementBatch.sequence.desc()).limit(1)) or 0
    for pending in db.scalars(select(SettlementBatch).where(
        SettlementBatch.device_id == device.id,
        SettlementBatch.generation == device.generation,
        SettlementBatch.status == BatchStatus.recalculating.value,
    )):
        pending.status = BatchStatus.stale.value
        pending.invalid_reason = '读数再次变更，该重算批次已过期，执行结果将拒绝写回'
        pending.finished_at = time.time()
    batch = SettlementBatch(
        device_id=device.id, generation=device.generation, sequence=last_seq + 1,
        status=BatchStatus.recalculating.value,
        input_versions={str(r.id): r.version for r in readings},
        affected_dates=affected, progress='pending', total_dates=len(affected),
        processed_dates=0, trigger=trigger, created_by=actor, created_at=time.time(),
    )
    db.add(batch)
    db.flush()
    return batch


def request_recompute(db, device_id, reason, user):
    d = _lock_device(db, device_id)
    readings = list(db.scalars(select(MeterReading).where(
        MeterReading.device_id == d.id,
        MeterReading.generation == d.generation,
    )))
    if not readings:
        raise HTTPException(409, '该设备代次尚无确认读数，无法重算')
    changed_date = min(r.reading_date for r in readings)
    batch = _open_recompute_batch(db, d, changed_date, trigger='manual', actor=user.email, reason=reason)
    db.commit()
    enqueue(batch.id)
    return batch


# ---------- 重算执行（worker / 同步复用） ----------
def run_recompute(db, batch_id):
    batch = db.get(SettlementBatch, batch_id)
    # 重试幂等：已结束的批次直接返回，不重复生成人数或结算
    if not batch or batch.status != BatchStatus.recalculating.value:
        return batch
    device = db.scalar(select(MeterDevice).where(
        MeterDevice.id == batch.device_id).with_for_update())
    batch.progress = 'snapshotting'
    readings = list(db.scalars(select(MeterReading).where(
        MeterReading.device_id == batch.device_id,
        MeterReading.generation == batch.generation,
    ).order_by(MeterReading.reading_date)))
    current_versions = {str(r.id): r.version for r in readings}
    # 过期执行保护：依据的读数版本已变化，拒绝写回
    if current_versions != batch.input_versions:
        batch.status = BatchStatus.stale.value
        batch.invalid_reason = '上游读数已更新，执行依据版本过期，结果拒绝写回'
        batch.progress = 'rejected'
        batch.finished_at = time.time()
        db.commit()
        _ensure_followup(db, batch.device_id, batch.generation)
        return batch
    batch.progress = 'settling'
    payload = [{'reading_date': r.reading_date, 'generation': r.generation,
                'entry_total': r.entry_total, 'completed_total': r.completed_total} for r in readings]
    lines = engine.settle(payload)
    now = time.time()
    # 整组一次性切换：旧批次判失效（历史行保留归档），新行同事务写入
    for old in db.scalars(select(SettlementBatch).where(
        SettlementBatch.device_id == batch.device_id,
        SettlementBatch.generation == batch.generation,
        SettlementBatch.id != batch.id,
        SettlementBatch.status.in_([BatchStatus.active.value, BatchStatus.recalculating.value]),
    )):
        old.status = BatchStatus.superseded.value
        old.invalid_reason = old.invalid_reason or '上游读数更新后整组重算，旧结算已失效'
        old.finished_at = now
    for index, line in enumerate(lines, start=1):
        db.add(SettlementLine(
            batch_id=batch.id, device_id=batch.device_id, generation=batch.generation,
            reading_date=line['reading_date'], prev_date=line['prev_date'],
            status=line['status'], entry_count=line['entry_count'],
            completed_count=line['completed_count'], reason=line['reason'], created_at=now,
        ))
        batch.processed_dates = index
    batch.status = BatchStatus.active.value
    batch.progress = 'done'
    batch.invalid_reason = ''
    batch.finished_at = now
    db.commit()
    return batch


def _ensure_followup(db, device_id, generation):
    """过期批次之后若无在途/有效批次，则按当前读数补一个重算批次并入队。"""
    pending = db.scalar(select(SettlementBatch).where(
        SettlementBatch.device_id == device_id,
        SettlementBatch.generation == generation,
        SettlementBatch.status.in_([BatchStatus.recalculating.value, BatchStatus.active.value]),
    ))
    if pending:
        return
    device = db.get(MeterDevice, device_id)
    readings = list(db.scalars(select(MeterReading).where(
        MeterReading.device_id == device_id,
        MeterReading.generation == generation,
    )))
    if not readings:
        return
    batch = SettlementBatch(
        device_id=device_id, generation=generation,
        sequence=(db.scalar(select(SettlementBatch.sequence).where(
            SettlementBatch.device_id == device_id).order_by(
            SettlementBatch.sequence.desc()).limit(1)) or 0) + 1,
        status=BatchStatus.recalculating.value,
        input_versions={str(r.id): r.version for r in readings},
        affected_dates=engine.affected_dates([r.reading_date for r in readings],
                                             min(r.reading_date for r in readings)),
        progress='pending', trigger='auto-followup', created_by='system', created_at=time.time(),
    )
    db.add(batch)
    db.commit()
    enqueue(batch.id)


def recover(db):
    """中断恢复：把遗留在重算中的批次重新入队，沿用已确认读数，不重复生成。"""
    recovered = []
    for batch in db.scalars(select(SettlementBatch).where(
        SettlementBatch.status == BatchStatus.recalculating.value,
    )):
        recovered.append(batch.id)
        enqueue(batch.id)
    return recovered


# ---------- 查询 ----------
def _latest_batch(db, device_id, generation):
    return db.scalar(select(SettlementBatch).where(
        SettlementBatch.device_id == device_id,
        SettlementBatch.generation == generation,
    ).order_by(SettlementBatch.sequence.desc()).limit(1))


def list_readings(db, device_id):
    return list(db.scalars(select(MeterReading).where(
        MeterReading.device_id == device_id).order_by(MeterReading.reading_date)))


def list_batches(db, device_id=None):
    stmt = select(SettlementBatch).order_by(SettlementBatch.id.desc())
    if device_id:
        stmt = stmt.where(SettlementBatch.device_id == device_id)
    return list(db.scalars(stmt.limit(200)))


def list_corrections(db, device_id=None):
    stmt = select(MeterCorrection).order_by(MeterCorrection.id.desc())
    if device_id:
        stmt = stmt.where(MeterCorrection.device_id == device_id)
    return list(db.scalars(stmt.limit(200)))


def get_batch_lines(db, batch_id):
    batch = db.get(SettlementBatch, batch_id)
    if not batch:
        raise HTTPException(404, '结算批次不存在')
    lines = list(db.scalars(select(SettlementLine).where(
        SettlementLine.batch_id == batch_id).order_by(SettlementLine.reading_date)))
    return batch, lines


def current_lines(db, device_id):
    device = db.get(MeterDevice, device_id)
    if not device:
        raise HTTPException(404, '设备不存在')
    batch = _latest_batch(db, device_id, device.generation)
    if not batch:
        return None, []
    lines = list(db.scalars(select(SettlementLine).where(
        SettlementLine.batch_id == batch.id).order_by(SettlementLine.reading_date)))
    return batch, lines


def gate_status(db):
    """结算闸门：重算中或存在缺日/断档/未解除隔离时，拦截新分析与简报。"""
    devices = list(db.scalars(select(MeterDevice)))
    blockers, recalculating, device_states = [], [], []
    for d in devices:
        batch = _latest_batch(db, d.id, d.generation)
        if not batch:
            continue
        if batch.status == BatchStatus.recalculating.value:
            blockers.append(f'设备 {d.code} 第 {d.generation} 代结算重算中（{batch.progress}）')
            recalculating.append(batch_dict(batch))
            continue
        if batch.status == BatchStatus.stale.value:
            blockers.append(f'设备 {d.code} 第 {d.generation} 代存在过期执行，等待重新结算')
            recalculating.append(batch_dict(batch))
            continue
        lines = list(db.scalars(select(SettlementLine).where(SettlementLine.batch_id == batch.id)))
        counts = {'quarantined_days': 0, 'missing_days': 0, 'broken_days': 0,
                  'entry_count': 0, 'completed_count': 0, 'settled_days': 0}
        for x in lines:
            if x.status == 'quarantined':
                counts['quarantined_days'] += 1
            elif x.status == 'missing':
                counts['missing_days'] += 1
            elif x.status == 'broken':
                counts['broken_days'] += 1
            elif x.status == 'active' and x.entry_count is not None:
                counts['settled_days'] += 1
                counts['entry_count'] += x.entry_count
                counts['completed_count'] += x.completed_count or 0
        if counts['quarantined_days']:
            blockers.append(f'设备 {d.code} 有 {counts["quarantined_days"]} 个隔离日待处理')
        if counts['missing_days']:
            blockers.append(f'设备 {d.code} 有 {counts["missing_days"]} 个缺日待补报')
        if counts['broken_days']:
            blockers.append(f'设备 {d.code} 有 {counts["broken_days"]} 个断档待补报')
        device_states.append({'device': device_dict(d), 'batch': batch_dict(batch), 'counts': counts})
    return {
        'blocked': bool(blockers),
        'reasons': blockers,
        'recalculating': recalculating,
        'devices': device_states,
        'device_count': len(devices),
    }


def ensure_gate_open(db):
    status = gate_status(db)
    if status['blocked']:
        raise HTTPException(409, '抄表结算闸门未开放：' + '；'.join(status['reasons'])
                            + '。请先补报读数或解除隔离后再发起新分析/简报')
