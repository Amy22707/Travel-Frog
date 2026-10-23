/* Verify a phone-local game session with no Python server process. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium,webkit}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),folder=path.join(root,'release/TravelFrogMobile');
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
 await page.goto(`http://127.0.0.1:${server.address().port}/index.html?edition=gallery`);
 await page.waitForFunction(()=>!document.querySelector('#import').disabled,undefined,{timeout:120000});
 await page.locator('#file').setInputFiles(path.join(root,'.build/qa-browser/gallery/gallery_state.json'));
 await page.waitForFunction(()=>!document.querySelector('#play').disabled,undefined,{timeout:60000});
 const downloadPromise=page.waitForEvent('download');
 await page.locator('#export').click();
 const download=await downloadPromise;
 await download.saveAs(path.join(root,'.build/qa-mobile-export.frogsave'));
 if(process.argv.includes('--offline')){
  await page.locator('#cache').click();
  await page.waitForFunction(()=>document.querySelector('#cache').textContent==='离线资源已下载',undefined,{timeout:180000});
  disconnected=true;
  await page.reload();
  await page.waitForFunction(()=>!document.querySelector('#play').disabled,undefined,{timeout:120000});
 }
 await page.locator('#play').click();
 const frame=page.frameLocator('#game');
 await frame.locator('canvas').waitFor({timeout:60000});
 await page.waitForFunction(()=>{
  const game=document.querySelector('#game').contentWindow;
  return game.GameConfig&&game.GameConfig.loadComplete&&game.core.ModelManage.getInstance().getModel(game.UserModel).uid;
 },undefined,{timeout:120000});
 await page.waitForTimeout(5000);
 await page.screenshot({path:path.join(root,'.build/qa-mobile-gallery.png')});
 console.log(JSON.stringify({errors,status:await page.locator('#progress').textContent(),offline:process.argv.includes('--offline')},null,2));
 if(errors.length)process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.close();});
