// Metadata only: never claim, take over, create a Space, or operate a Page.
const spaces=(await listTaskSpaces()).filter(s=>s.name?.startsWith('千川巡航 · '));
const metadata=spaces.map(s=>({id:s.id,name:s.name,ownership:s.ownership}));
const fs=await import('node:fs/promises');
const path=await import('node:path');
const target=globalThis.__qcxReleaseRoot
  ?path.join(globalThis.__qcxReleaseRoot,'data/monitoring/ego-control-ownership-latest.json')
  :new URL('../../data/monitoring/ego-control-ownership-latest.json',import.meta.url);
try{await fs.writeFile(target,JSON.stringify({at:new Date().toISOString(),spaces:metadata}), 'utf8');}catch{}
console.log('QCX_OWNERSHIP_V1:'+JSON.stringify(metadata));
