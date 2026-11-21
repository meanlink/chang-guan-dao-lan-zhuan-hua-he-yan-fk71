import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, MeterDevice, MeterReading
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.services.metering import service as meter_service
from src.utils.security import hash_password
from src.utils.serialization import checksum


def seed_meters(db):
    """抄表结算演示数据：每展区一台设备，连续 5 日单调递增累计读数，闸门保持开放。"""
    specs = [('CNT-STAR-01', '星空厅', 'A', (120, 60)),
             ('CNT-WATER-02', '水世界', 'B', (150, 95)),
             ('CNT-MECH-03', '机械坊', 'A', (100, 32))]
    created = 0
    for code, zone, arm, (entry_step, completed_step) in specs:
        if db.scalar(select(MeterDevice).where(MeterDevice.code == code)):
            continue
        dev = MeterDevice(code=code, zone=zone, arm=arm, generation=1,
                          note='演示设备 · 累计计数器跨日不清零', active=True, created_at=time.time())
        db.add(dev)
        db.flush()
        now = time.time()
        for day in range(5):
            entry = 200 + entry_step * day
            completed = 90 + completed_step * day
            db.add(MeterReading(device_id=dev.id, generation=1,
                                reading_date=f'2026-11-{day + 1:02d}',
                                entry_total=entry, completed_total=completed,
                                version=1, status='confirmed', created_at=now, updated_at=now))
        db.flush()
        batch = meter_service._open_recompute_batch(db, dev, '2026-11-01',
                                                    trigger='seed', actor='seed')
        meter_service.run_recompute(db, batch.id)  # 同步结算，落为 active，闸门开放
        created += 1
    if created:
        db.add(Audit(actor='seed', action='demo.meters', detail={'devices': created}, at=time.time()))
        db.commit()

def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        db.commit()
        seed_meters(db)
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,MeterDevice,MeterReading]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()
