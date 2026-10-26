// Execute the actual console handlers with a minimal DOM and mocked HTTP.
const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
const ids = [...fs.readFileSync('server/static/index.html','utf8').matchAll(/id="([^"]+)"/g)].map(m=>m[1]);
const elements = Object.fromEntries(ids.map(id=>[id,{hidden:true,disabled:false,textContent:'',value:'',replaceChildren(){},querySelectorAll(){return []}}]));
let online = false; const requests=[];
const context = vm.createContext({console,AbortSignal,URL,localStorage:{getItem(){return ''}},document:{getElementById(id){assert.ok(elements[id],id);return elements[id]},addEventListener(){}},setInterval(){},clearInterval(){},clearTimeout(){},fetch:async(url,options)=>{
 requests.push(url);
 if(url==='/api/status')return {ok:true,json:async()=>({agent_online:online})};
 return {ok:true,json:async()=>({id:'task-1'})};
}});
vm.runInContext(fs.readFileSync('server/static/app.js','utf8'),context);
(async()=>{
 vm.runInContext('controls()',context);
 assert.equal(elements.quick.disabled,false);
 await elements.quick.onclick();
 assert.match(elements.error.textContent,/没有在线/);
 assert.equal(requests.includes('/api/tasks'),false);
 online=true;
 await elements.quick.onclick();
 assert.deepEqual(requests.slice(-3),['/api/status','/api/tasks','/api/tasks/task-1/capture']);
 assert.equal(elements.quick.disabled,false);
 vm.runInContext('busy=true; controls()',context);
 assert.equal(elements.quick.disabled,true);
 console.log('Console quick checks passed: offline explanation, no offline task creation, fresh online check, capture dispatch, busy guard.');
})().catch(e=>{console.error(e);process.exitCode=1});
