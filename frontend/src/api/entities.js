import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const meters={
 gate:()=>api('/meters/gate'),
 devices:()=>api('/meters/devices'),
 createDevice:data=>api('/meters/devices',{method:'POST',body:JSON.stringify(data)}),
 replace:(id,reason)=>api('/meters/devices/'+id+'/replace',{method:'POST',body:JSON.stringify({reason})}),
 readings:id=>api('/meters/devices/'+id+'/readings'),
 settlement:id=>api('/meters/devices/'+id+'/settlement'),
 addReading:data=>api('/meters/readings',{method:'POST',body:JSON.stringify(data)}),
 batches:deviceId=>api('/meters/batches'+(deviceId?('?device_id='+deviceId):'')),
 batchLines:id=>api('/meters/batches/'+id+'/lines'),
 recompute:(id,reason)=>api('/meters/devices/'+id+'/recompute',{method:'POST',body:JSON.stringify({reason})}),
 corrections:deviceId=>api('/meters/corrections'+(deviceId?('?device_id='+deviceId):''))
};
