/* Verify a phone-local game session with no Python server process. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium,webkit}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),{execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),folder=path.join(root,'release/TravelFrogMobile');
const edition=process.argv.includes('--normal')?'normal':'gallery';
let disconnected=false;
const types={'.js':'text/javascript','.html':'text/html','.wasm':'application/wasm','.json':'application/json','.webmanifest':'application/manifest+json','.png':'image/png'};
const server=http.createServer((req,res)=>{
 if(disconnected){req.socket.destroy();return;}
 const pathname=new URL(req.url,'http://local').pathname;
 const file=path.join(folder,pathname==='/'?'index.html':pathname);
 if(!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}
 res.setHeader('Content-Type',types[path.extname(file)]||'application/octet-stream');fs.createReadStream(file).pipe(res);
});
let browser;
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=process.argv.includes('--webkit')?await webkit.launch({headless:true}):await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:430,height:860},hasTouch:true});
 const page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(String(e)));
 page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 page.on('worker',worker=>worker.on('console',m=>{if(m.type()==='error')errors.push(m.text());}));
 await page.goto(`http://127.0.0.1:${server.address().port}/index.html?edition=${edition}`);
 await page.waitForFunction(()=>!document.querySelector('#import').disabled,undefined,{timeout:120000});
 if(process.argv.includes('--create'))await page.locator('#create').click();
 else await page.locator('#file').setInputFiles(path.join(root,process.argv.includes('--traveler')?'.build/qa-courtyard':'.build/qa-browser',edition,edition==='gallery'?'gallery_state.json':'state.json'));
 await page.waitForFunction(()=>!document.querySelector('#play').disabled,undefined,{timeout:60000});
 async function enter(){
  await page.locator('#play').click();
  await page.frameLocator('#game').locator('canvas').waitFor({timeout:60000});
  await page.waitForFunction(()=>{
   const game=document.querySelector('#game').contentWindow;
   return game.GameConfig&&game.GameConfig.loadComplete&&game.core.ModelManage.getInstance().getModel(game.UserModel).uid;
  },undefined,{timeout:120000});
 }
 async function rename(name){
  const game=page.frames().find(f=>f.url().includes('/game/index.html'));
  await game.evaluate(name=>new Promise((resolve,reject)=>{
   const socket=new WebSocket('ws://frog-mobile.local');
   socket.onopen=()=>socket.send(JSON.stringify({cmd:'client.set_name',session:900001,data:{name}}));
   socket.onmessage=event=>{const reply=JSON.parse(event.data);if(reply.session===900001){socket.close();reply.data.code===0?resolve():reject(Error('rename failed'));}};
   socket.onerror=reject;
  }),name);
  await page.locator('#manage').click();
  await page.waitForFunction(name=>document.querySelector('#progress').textContent.includes(name),name,{timeout:20000});
 }
 if(process.argv.includes('--lifecycle')){await enter();await rename('审查-'+edition+'-A');}
 const downloadPromise=page.waitForEvent('download');
 await page.locator('#export').click();
 const download=await downloadPromise;
 const exported=path.join(root,`.build/qa-mobile-${edition}-export.frogsave`);
 await download.saveAs(exported);
 if(process.argv.includes('--lifecycle')){
  await enter();await rename('审查-'+edition+'-B');
  await page.locator('#file').setInputFiles(exported);
  await page.waitForFunction(()=>document.querySelector('#progress').textContent.includes('-A'),undefined,{timeout:60000});
  await page.waitForFunction(()=>!document.querySelector('#previous').disabled);
  const previousPromise=page.waitForEvent('download');await page.locator('#previous').click();
  const previous=await previousPromise,previousPath=path.join(root,`.build/qa-mobile-${edition}-before-import.frogsave`);
  await previous.saveAs(previousPath);
  const name=execFileSync('python',['-X','utf8','-c',"import json,zipfile,sys; z=zipfile.ZipFile(sys.argv[1]); s=json.loads(z.read('state.json')); print(next(x['data']['frog']['name'] for x in s['sync_pushes'] if x['cmd']=='client.load_role'))",previousPath],{encoding:'utf8',windowsHide:true}).trim();
  assert.equal(name,'审查-'+edition+'-B');
  await page.evaluate(async()=>{
   await navigator.serviceWorker.ready;
   const names=await caches.keys(),cache=await caches.open(names.find(n=>n.startsWith('frog-offline-')));
   await cache.put(new URL('phone.js',location.href),new Response('throw new Error("STALE_APP_CODE")',{headers:{'Content-Type':'text/javascript'}}));
  });
  await page.reload();
  await page.waitForFunction(()=>!document.querySelector('#play').disabled,undefined,{timeout:120000});
  assert((await page.locator('#progress').textContent()).includes('-A'));
 }
 if(process.argv.includes('--offline')){
  await page.locator('#cache').click();
  await page.waitForFunction(()=>document.querySelector('#cache').textContent==='离线资源已下载',undefined,{timeout:180000});
  disconnected=true;
  await page.reload();
  await page.waitForFunction(()=>!document.querySelector('#play').disabled,undefined,{timeout:120000});
 }
 await enter();
 if(process.argv.includes('--viewport')){
  const game=page.frames().find(f=>f.url().includes('/game/index.html'));
  assert(await page.locator('h1 img').evaluate(img=>img.complete&&img.naturalWidth>0));
  await game.evaluate(()=>{window.__viewportQA=true;});
  await page.setViewportSize({width:850,height:420});
  await page.locator('#rotate').waitFor({state:'visible'});
  assert.equal(await page.locator('#game').isVisible(),false);
  await page.setViewportSize({width:360,height:850});
  await page.locator('#game').waitFor({state:'visible'});
  assert.equal(await game.evaluate(()=>window.__viewportQA),true);
  const shifted=await page.locator('#gameViewport').evaluate(node=>{node.scrollLeft=70;return node.scrollLeft;});
  assert(shifted>0,'narrow view must scroll horizontally');
  console.log('Portrait restored without a reload; narrow viewport scrolls horizontally.');
 }
 if(process.argv.includes('--traveler')){
  const game=page.frames().find(f=>f.url().includes('/game/index.html'));
  await game.waitForFunction(()=>{
   const scene=core.PageManage.getInstance().getControl(MainOutController,core.ViewLayerType.SceneLayer);
   return scene&&scene.view&&scene.view.visitorView_Dragonbones;
  },undefined,{timeout:30000});
  console.log('Phone-local courtyard traveler rendered:',await game.evaluate(()=>core.ModelManage.getInstance().getModel(VisitorModel).getVisitorData().name));
 }
 await page.waitForTimeout(5000);
 await page.screenshot({path:path.join(root,'.build/qa-mobile-gallery.png')});
 console.log(JSON.stringify({edition,errors,status:await page.locator('#progress').textContent(),lifecycle:process.argv.includes('--lifecycle'),offline:process.argv.includes('--offline')},null,2));
 if(errors.length)process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.close();});
