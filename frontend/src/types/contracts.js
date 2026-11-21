/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule */
/** @typedef {{id:number,code:string,zone:string,arm:string,generation:number,note:string,active:boolean}} MeterDevice */
/** @typedef {{id:number,device_id:number,generation:number,reading_date:string,entry_total:number,completed_total:number,version:number,status:string}} MeterReading */
/** @typedef {{id:number,device_id:number,generation:number,sequence:number,status:string,affected_dates:string[],invalid_reason:string,progress:string,total_dates:number,processed_dates:number,trigger:string}} SettlementBatch */
/** @typedef {{id:number,batch_id:number,device_id:number,reading_date:string,prev_date:?string,status:string,entry_count:?number,completed_count:?number,reason:string}} SettlementLine */
/** @typedef {{blocked:boolean,reasons:string[],recalculating:Object[],device_count:number}} MeterGate */
export {};
