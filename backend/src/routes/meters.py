from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from src.config.database import get_db
from src.models import MeterDevice
from src.schemas.meter import DeviceInput, ReadingInput, RecomputeInput
from src.middlewares.auth import current_user, can_write, admin_only
from src.services.audit import record
from src.services.metering import service
router = APIRouter(prefix='/meters')


def _validate(fn, *args, **kwargs):
    """服务层按 ValueError 报告口径/格式错误，路由层统一转为 422。"""
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get('/gate')
def gate(db=Depends(get_db), u=Depends(current_user)):
    return service.gate_status(db)


@router.get('/devices')
def devices(db=Depends(get_db), u=Depends(current_user)):
    return [service.device_dict(d) for d in db.scalars(select(MeterDevice).order_by(MeterDevice.id))]


@router.post('/devices', status_code=201)
def create_device(data: DeviceInput, db=Depends(get_db), u=Depends(can_write)):
    d = service.create_device(db, data, u)
    record(db, u.email, 'meter.device.create', {'id': d.id, 'code': d.code, 'generation': d.generation})
    db.commit()
    return service.device_dict(d)


@router.post('/devices/{id}/replace', status_code=201)
def replace_device(id: int, data: RecomputeInput, db=Depends(get_db), u=Depends(can_write)):
    d = service.replace_device(db, id, data.reason, u)
    record(db, u.email, 'meter.device.replace', {'id': d.id, 'generation': d.generation})
    db.commit()
    return service.device_dict(d)


@router.get('/devices/{id}/readings')
def readings(id: int, db=Depends(get_db), u=Depends(current_user)):
    rows = service.list_readings(db, id)
    return [service.reading_dict(r) for r in rows]


@router.get('/devices/{id}/settlement')
def settlement(id: int, db=Depends(get_db), u=Depends(current_user)):
    batch, lines = service.current_lines(db, id)
    return {
        'batch': service.batch_dict(batch) if batch else None,
        'lines': [service.line_dict(x) for x in lines],
    }


@router.post('/readings', status_code=201)
def add_reading(data: ReadingInput, db=Depends(get_db), u=Depends(can_write)):
    out = _validate(service.upsert_reading, db, data, u)
    record(db, u.email, 'meter.reading.upsert',
           {'device_id': data.device_id, 'date': data.reading_date,
            'batch': out['batch'].id, 'changed': out['changed']})
    # 服务层内部可能已提交结算；这里统一再提交，确保审计留痕不随会话关闭丢失
    db.commit()
    return {'reading': service.reading_dict(out['reading']),
            'batch': service.batch_dict(out['batch']),
            'changed': out['changed']}


@router.get('/batches')
def batches(device_id: int | None = None, db=Depends(get_db), u=Depends(current_user)):
    return [service.batch_dict(b) for b in service.list_batches(db, device_id)]


@router.get('/batches/{id}/lines')
def batch_lines(id: int, db=Depends(get_db), u=Depends(current_user)):
    batch, lines = service.get_batch_lines(db, id)
    return {'batch': service.batch_dict(batch), 'lines': [service.line_dict(x) for x in lines]}


@router.post('/devices/{id}/recompute', status_code=201)
def recompute(id: int, data: RecomputeInput, db=Depends(get_db), u=Depends(admin_only)):
    batch = service.request_recompute(db, id, data.reason, u)
    record(db, u.email, 'meter.recompute.request', {'id': batch.id})
    db.commit()  # 服务内已提交重算批次；补提交确保审计留痕持久化
    return service.batch_dict(batch)


@router.get('/corrections')
def corrections(device_id: int | None = None, db=Depends(get_db), u=Depends(current_user)):
    return [service.correction_dict(c) for c in service.list_corrections(db, device_id)]
