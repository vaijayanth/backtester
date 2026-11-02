const {chromium}=require('playwright');const fs=require('fs');
const ST=JSON.parse(fs.readFileSync('/tmp/site4/supertrend_full.json'));
const MS=JSON.parse(fs.readFileSync('/tmp/site4/market_snapshot.json'));
const FNO=JSON.parse(fs.readFileSync('/tmp/site4/supertrend.json'));
(async()=>{const b=await chromium.launch();let fail=0;const ok=(c,m)=>{console.log(c?'PASS':'FAIL',m);if(!c)fail++;};
async function open(clock){const ctx=await b.newContext({viewport:{width:1440,height:900}});const p=await ctx.newPage();await p.clock.install({time:new Date(clock)});
 const errs=[];p.on('pageerror',e=>errs.push(e.message));p._errs=errs;
 await p.goto('http://localhost:8210/index.html');await p.clock.runFor(3000);await p.waitForTimeout(1200);return p;}
async function banner(p,pg){await p.evaluate(pg=>document.querySelector(`.nav button[data-page="${pg}"]`).click(),pg);await p.clock.runFor(2000);await p.waitForTimeout(800);
 return p.evaluate(pg=>{const e=document.querySelector(`#page-${pg} > .stale-banner`);return e?{shown:!e.hidden,text:e.innerText}:{shown:false,text:''}},pg);}
for(const [label,clock,expectStale] of [['SUNDAY noon (feed Fri 30 Oct = latest close)','2026-11-01T06:30:00Z',false],['FRIDAY 20:00 IST (before the 9pm cutoff; expected Thu 29)','2026-10-30T14:30:00Z',false],['TUESDAY 10:00 IST (expected Mon 2 Nov)','2026-11-03T04:30:00Z',true],['MONDAY 22:00 IST (expected Mon 2 Nov)','2026-11-02T16:30:00Z',true]]){
 const p=await open(clock);console.log('\n==',label);
 for(const pg of ['dashboard','dh252','dhcc','dividend']){const r=await banner(p,pg);ok(r.shown===expectStale,`${pg}: banner ${r.shown?'SHOWN':'hidden'} (expected ${expectStale?'shown':'hidden'})`);}
 // IVP also has hand-uploaded sources (Sensibull snapshot 15 days old, no positions upload) which are legitimately stale: check only the PRICE line
 const iv=await banner(p,'ivp');ok(/Price \/ Supertrend/.test(iv.text)===expectStale,`ivp: price-feed alert ${/Price \/ Supertrend/.test(iv.text)?'SHOWN':'absent'} (expected ${expectStale?'shown':'absent'}); upload alerts present: ${/Sensibull/.test(iv.text)}`);
 const side=await p.evaluate(()=>document.getElementById('sidebar-status').classList.contains('stale'));ok(side===true,`sidebar flag ${side} (IVP uploads stale in every scenario, so always on)`);
 // zswing price check
 await p.evaluate(async()=>{await eqFetchSupertrend();staleCheckPrices('page-zswing','Prices (Supertrend, 52-week high)')});
 const z=await banner(p,'zswing');ok(z.shown===expectStale,`zswing: banner ${z.shown?'SHOWN':'hidden'}`);
 ok(p._errs.length===0,'no page errors '+p._errs.slice(0,2));await p.context().close();}
// manual-upload kinds
const p=await open('2026-11-03T04:30:00Z');console.log('\n== manual uploads (Tue 3 Nov 10:00 IST)');
await p.evaluate(()=>document.querySelector('.nav button[data-page="opttrades"]').click());await p.clock.runFor(1500);
const tests=await p.evaluate(()=>{const out={};
 const J=(k,l,i,o)=>staleJudge(k,l,i,o).stale;
 out.sensibull_3d=J('age','S',new Date(Date.now()-3*864e5).toISOString(),{maxAgeDays:8});
 out.sensibull_9d=J('age','S',new Date(Date.now()-9*864e5).toISOString(),{maxAgeDays:8});
 out.pnl_none=J('age','P',null,{maxAgeDays:8});
 out.pos_before_close=J('close','Pos','2026-11-02T09:00:00Z');   // Mon 14:30 IST: before the 15:30 close
 out.pos_after_close=J('close','Pos','2026-11-02T11:00:00Z');    // Mon 16:30 IST: after the close
 out.session_missing_date=J('session','X',null,{dataDate:null});
 return out});
ok(tests.sensibull_3d===false,'Sensibull 3 days old -> fine');ok(tests.sensibull_9d===true,'Sensibull 9 days old -> STALE');ok(tests.pnl_none===true,'no P&L upload -> STALE');
ok(tests.pos_before_close===true,'positions uploaded before Mon close -> STALE');ok(tests.pos_after_close===false,'positions uploaded after Mon close -> fine');ok(tests.session_missing_date===true,'feed without a data date -> STALE');
// banner rendering for an age source on the opttrades page
await p.evaluate(()=>renderFreshnessStat('ot-data-asof',new Date(Date.now()-10*864e5).toISOString(),'P&L file last uploaded',24*8,{kind:'age',maxAgeDays:8}));
const ob=await p.evaluate(()=>{const e=document.querySelector('#page-opttrades > .stale-banner');return {shown:!e.hidden,text:e.innerText.slice(0,200)}});ok(ob.shown&&/10 days ago/.test(ob.text),'opttrades banner: '+ob.text.split('\n')[1]);
await p.evaluate(()=>renderFreshnessStat('ot-data-asof',new Date(Date.now()-1*864e5).toISOString(),'P&L file last uploaded',24*8,{kind:'age',maxAgeDays:8}));
ok(await p.evaluate(()=>document.querySelector('#page-opttrades > .stale-banner').hidden),'banner clears when the upload is refreshed');
console.log(fail?`\nFAILURES ${fail}`:'\nALL PASS');await b.close()})();
