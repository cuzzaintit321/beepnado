'use strict';
const codes={100:'Continue',101:'Switching Protocols',200:'OK',201:'Created',202:'Accepted',204:'No Content',206:'Partial Content',301:'Moved Permanently',302:'Found',303:'See Other',304:'Not Modified',307:'Temporary Redirect',308:'Permanent Redirect',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',405:'Method Not Allowed',408:'Request Timeout',409:'Conflict',410:'Gone',413:'Content Too Large',415:'Unsupported Media Type',418:"I'm a teapot",422:'Unprocessable Content',429:'Too Many Requests',451:'Unavailable For Legal Reasons',500:'Internal Server Error',501:'Not Implemented',502:'Bad Gateway',503:'Service Unavailable',504:'Gateway Timeout',505:'HTTP Version Not Supported'};
function calculate(tool,values,now=new Date()){
 const n=k=>{const v=Number(values.get(k));if(!Number.isFinite(v))throw Error('Enter a finite number.');return v;};
 const fmt=v=>{if(!Number.isFinite(v))throw Error('Result exceeds numeric range.');return v.toLocaleString('en-US',{maximumFractionDigits:4});};
 switch(tool){
 case 'sla': {const minutes=n('days')*1440*(100-n('pct'))/100;return 'Allowed downtime: '+fmt(minutes)+' minutes ('+fmt(minutes/60)+' hours).';}
 case 'cost':return 'Estimated gross revenue at risk: $'+fmt(n('visits')*n('rate')/100*n('order')*n('minutes')/60);
 case 'cron':{const m=values.get('mode');return m==='interval'?'*/'+n('interval')+' * * * *':n('minute')+' '+n('hour')+' * * '+(m==='weekly'?n('weekday'):'*');}
 case 'json':return JSON.stringify(JSON.parse(values.get('json')),null,2);
 case 'status':{const code=n('status');return code+' '+(codes[code]||'— not in this reference')+'\n'+(['','Informational response','Successful response','Redirection','Client error','Server error'][Math.floor(code/100)]||'');}
 case 'score':{const weights={availability:25,tls:20,latency:20,dns:15,headers:20};const missed=Object.keys(weights).filter(k=>!values.has(k));const score=100-missed.reduce((a,k)=>a+weights[k],0);return score+'/100 verified readiness points.\n'+(missed.length?'Not yet verified: '+missed.join(', ')+'.':'All checklist items marked verified.')+'\nSelf-reported; not a live scan.';}
 case 'ssl':{const t=Date.parse(values.get('expiry')+'T00:00:00Z');if(!Number.isFinite(t))throw Error('Enter a valid expiration date.');const today=Date.UTC(now.getUTCFullYear(),now.getUTCMonth(),now.getUTCDate());const days=(t-today)/86400000;return days+' days until the entered UTC date. '+(days<0?'Date has passed.':days<=n('threshold')?'Within renewal warning threshold.':'Outside renewal warning threshold.');}
 case 'latency':{const parts=String(values.get('samples')).trim().split(/[\s,]+/);if(!parts[0]||parts.length>5000||parts.some(v=>! /^\d+(?:\.\d+)?$/.test(v)||!Number.isFinite(Number(v))))throw Error('Enter 1–5,000 nonnegative decimal measurements.');const a=parts.map(Number).sort((x,y)=>x-y);const l=a.length;const median=l%2?a[(l-1)/2]:a[l/2-1]/2+a[l/2]/2;return 'Samples: '+l+'\nMean: '+fmt(a.reduce((s,v)=>s+v/l,0))+' ms\nMedian: '+fmt(median)+' ms\n95th percentile: '+fmt(a[Math.ceil(.95*l)-1])+' ms';}
 default:throw Error('Unknown tool.');
 }
}
if(typeof module!=='undefined')module.exports={calculate};
if(typeof document!=='undefined')document.querySelectorAll('form[data-tool]').forEach(form=>{form.addEventListener('submit',event=>{event.preventDefault();const out=form.querySelector('output');try{out.textContent=calculate(form.dataset.tool,new FormData(form));}catch(error){out.textContent='Error: '+error.message;}});});
