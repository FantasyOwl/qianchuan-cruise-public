'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
let JSDOM;try{({JSDOM}=require('jsdom'));}catch{({JSDOM}=require('../data/browser-control-test-deps/node_modules/jsdom'));}
const {Panel,DEFAULT}=require('../static/branding.js');
const PNG='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jT1kAAAAASUVORK5CYII=';
function fixture(){
 const dom=new JSDOM(fs.readFileSync(require.resolve('../static/cruise.html'),'utf8'));
 const doc=dom.window.document,calls=[],dialog=doc.querySelector('#branding-dialog');
 dialog.showModal=()=>dialog.setAttribute('open','');dialog.close=()=>{dialog.removeAttribute('open');dialog.dispatchEvent(new dom.window.Event('close'));};
 let saved={...DEFAULT};const app={doc,$:s=>doc.querySelector(s),note:()=>{},api:async(path,body)=>{calls.push({path,body});if(body)saved={...body};return {ok:true,branding:{...saved}};}};
 const panel=new Panel(app);return {app,panel,doc,dom,calls,get saved(){return saved;}};
}
const settle=async()=>{for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));};
test('branding edits remain a preview until saved and survive a new load without any ad endpoints',async()=>{
 const f=fixture();await f.panel.load();f.panel.open();let name=f.doc.querySelector('#branding-name');
 name.value='我的品牌';name.dispatchEvent(new f.dom.window.Event('input',{bubbles:true}));f.panel.draft.icon_data_url=PNG;f.panel.preview();
 assert.equal(f.doc.querySelector('#brand-name').textContent,DEFAULT.name);assert.equal(f.doc.querySelector('#branding-preview-name').textContent,'我的品牌');
 f.doc.querySelector('#branding-form').dispatchEvent(new f.dom.window.Event('submit',{bubbles:true,cancelable:true}));await settle();
 assert.equal(f.doc.querySelector('#brand-name').textContent,'我的品牌');assert.equal(f.doc.querySelector('#brand-icon').getAttribute('src'),PNG);
 assert.equal(f.doc.querySelector('#brand-icon').hidden,false);assert.equal(f.doc.title,'我的品牌 · 多账户调控');
 const next=new Panel(f.app);await next.load();assert.equal(next.saved.name,'我的品牌');
 assert.deepEqual(f.calls.map(c=>c.path),['/api/branding','/api/branding','/api/branding']);
 assert.deepEqual(f.calls[1].body,{name:'我的品牌',subtitle:DEFAULT.subtitle,icon_data_url:PNG});
});
test('cancel and reset do not save or change the header and malicious names render as text',async()=>{
 const f=fixture();await f.panel.load();f.panel.open();f.panel.draft.name='<img src=x onerror=alert(1)>';f.panel.preview();
 assert.equal(f.doc.querySelector('#branding-preview-name img'),null);f.doc.querySelector('#branding-cancel').click();
 assert.equal(f.doc.querySelector('#brand-name').textContent,DEFAULT.name);assert.equal(f.calls.length,1);
 f.panel.open();f.panel.draft.icon_data_url=PNG;f.panel.preview();f.doc.querySelector('#branding-reset').click();
 assert.deepEqual(f.panel.draft,DEFAULT);assert.equal(f.calls.length,1);
});
test('invalid and oversized uploads preserve the existing icon and display a useful message',async()=>{
 const f=fixture();await f.panel.load();f.panel.open();f.panel.draft.icon_data_url=PNG;
 await f.panel.image(new f.dom.window.File(['<svg/>'],'bad.svg',{type:'image/svg+xml'}));
 assert.match(f.doc.querySelector('#branding-feedback').textContent,/PNG、JPG或WebP/);assert.equal(f.panel.draft.icon_data_url,PNG);
 await f.panel.image({type:'image/png',size:2*1024*1024+1});assert.match(f.doc.querySelector('#branding-feedback').textContent,/2MB/);
 assert.equal(f.doc.querySelector('#branding-save').disabled,false);assert.equal(f.calls.length,1);
});
