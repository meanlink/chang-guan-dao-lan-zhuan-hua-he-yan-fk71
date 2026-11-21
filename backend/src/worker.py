import time, json, logging
from pathlib import Path
from sqlalchemy import select
from redis import Redis
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models import Job, Dataset, Report, SettlementBatch
from src.services.business import analyze
from src.services.reports import deliver
from src.services.metering import service as meter_service
from src.services.audit import record
logging.basicConfig(level=logging.INFO)
def execute(job_id):
    with Session() as db:
        j=db.get(Job,job_id)
        if not j or j.status!='queued':return
        j.status='running'; db.commit()
        try:
            d=db.get(Dataset,j.dataset_id)
            result=analyze(d.records,j.params)
            json.dumps(result,allow_nan=False)
            j.result=result;j.status='completed';j.finished_at=time.time()
            record(db,'worker','job.completed',{'id':j.id})
        except Exception as e:
            logging.exception('Analysis failed')
            j.status='failed';j.error=str(e)[:990];j.finished_at=time.time()
            record(db,'worker','job.failed',{'id':j.id,'error':j.error})
        db.commit()
def execute_meter(batch_id):
    # 幂等：服务层按批次状态短路，重试不会重复生成人数或结算
    with Session() as db:
        before=db.get(SettlementBatch,batch_id)
        if not before:return
        meter_service.run_recompute(db,batch_id)
        after=db.get(SettlementBatch,batch_id)
        if after and after.status in ('active','stale'):
            record(db,'worker','meter.batch.'+after.status,{'id':batch_id})
            db.commit()
def tick():
    with Session() as db:
        queued=list(db.scalars(select(Job.id).where(Job.status=='queued').limit(3)))
    for job_id in queued:execute(job_id)
    # 抄表重算兜底：Redis 消息丢失时按 SQL 状态拾取，中断恢复沿用已确认读数
    with Session() as db:
        meter_pending=list(db.scalars(select(SettlementBatch.id).where(
            SettlementBatch.status=='recalculating').limit(3)))
    for batch_id in meter_pending:execute_meter(batch_id)
    with Session() as db:
        for r in db.scalars(select(Report).where(Report.enabled==True,Report.next_run<=time.time())):
            d=deliver(db,r)
            if d:record(db,'worker','report.delivered',{'id':r.id,'delivery':d.id})
        db.commit()
def main():
    r=Redis.from_url(REDIS_URL,socket_timeout=3)
    # This Compose stack has a single worker. A crash must not strand its jobs.
    with Session() as db:
        for j in db.scalars(select(Job).where(Job.status=='running')):
            j.status='queued';record(db,'worker','job.recovered',{'id':j.id})
        recovered=meter_service.recover(db)
        for batch_id in recovered:record(db,'worker','meter.recovered',{'id':batch_id})
        db.commit()
    while True:
        Path('/tmp/heartbeat').touch()
        try:
            item=r.blpop('analysis:queue',timeout=1)
            if item:execute(int(item[1]))
            meter_item=r.blpop(meter_service.QUEUE_KEY,timeout=1)
            if meter_item:execute_meter(int(meter_item[1]))
            tick()
        except Exception:
            logging.exception('Worker retry');time.sleep(2)
if __name__=='__main__':main()
