/* Run the local desktop client in an isolated browser profile. */
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {spawn} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const playwright = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const edition = process.argv[2] || 'normal';
const release=path.join(root,'release',edition==='gallery'?'TravelFrogGallery':'TravelFrogNormal');
const web = path.join(release, 'web/game');
const travelerTest=process.argv.includes('--traveler');
const data = path.join(root, travelerTest?'.build/qa-courtyard':'.build/qa-browser', edition);
const port = edition === 'gallery' ? 52455 : 52454;
const types = {'.html':'text/html', '.js':'text/javascript', '.json':'application/json', '.png':'image/png', '.jpg':'image/jpeg'};
const server = http.createServer((req, res) => {
    const relative = decodeURIComponent(new URL(req.url, 'http://local').pathname);
    const file = path.join(web, relative === '/' ? 'index.html' : relative);
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {res.writeHead(404); res.end(); return;}
    res.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream');
    fs.createReadStream(file).pipe(res);
});
let browser, service;
(async () => {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const args = ['--data-dir', data, '--port', String(port)];
    if (edition === 'gallery') args.push('--gallery');
    service = spawn(path.join(release, 'FrogServer.exe'), args, {cwd: root, windowsHide:true, stdio:'ignore'});
    browser = await playwright.chromium.launch({channel:'msedge', headless:true});
    const page = await browser.newPage({viewport:{width:520, height:850}});
    const errors=[];
    page.on('pageerror', e => errors.push(String(e)));
    page.on('console', m => {if(m.type()==='error') errors.push(m.text());});
    page.on('requestfailed', req => errors.push(req.url() + ': ' + req.failure()?.errorText));
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html?edition=${edition}&serverPort=${port}`);
    await page.waitForFunction(()=>typeof RES !== 'undefined' && !!RES.getRes('mainout_season41_bg_png'), undefined, {timeout:60000});
    await page.waitForTimeout(1500);
    await page.screenshot({path:path.join(root, `.build/qa-desktop-${edition}.png`)});
    const state=await page.evaluate(()=>{
        const season=RES.getResourceInfo('season41_eab');
        const result={url:location.href,channel:GameConfig.channel,isAPP:GameConfig.isAPP,
            bundle:season,loaded:!!RES.getRes('season41_eab'),background:!!RES.getRes('mainout_season41_bg_png')};
        return result;
    });
    // The original start screen waits for a gesture before showing the courtyard.
    await page.mouse.click(260, 485);
    await page.waitForTimeout(5000);
    await page.screenshot({path:path.join(root, `.build/qa-desktop-${edition}-courtyard.png`)});
    if(travelerTest){
        await page.waitForFunction(()=>{
            const control=core.PageManage.getInstance().getControl(MainOutController,core.ViewLayerType.SceneLayer);
            return control&&control.view&&control.view.visitorView_Dragonbones;
        },undefined,{timeout:30000});
        state.traveler=await page.evaluate(()=>{
            const model=core.ModelManage.getInstance().getModel(VisitorModel);
            const v=model.getVisitorData();
            const province=v.getProvince();
            const view=new VisitorView(null);view.open();
            const end=core.Time.getServerTime()-1;
            core.SocketManage.getInstance().send('visit_set_expire_time',null,end);
            v.expire_time=end;
            const scene=core.PageManage.getInstance().getControl(MainOutController,core.ViewLayerType.SceneLayer).view;
            scene.updateVisitorGift();
            if(!scene.i_gift.visible)throw Error('Expired visit gift not shown');
            const gift=new VisitorGiftView(null);gift.open();
            model.open();
            if(!model.getAcquireList().includes(province))throw Error('Province flower not collected');
            return {name:v.name,province,food:v.food,flower:true};
        });
        await page.waitForFunction(()=>!core.ModelManage.getInstance().getModel(VisitorModel).getVisitorData());
        await page.waitForTimeout(500);
        const saved=JSON.parse(fs.readFileSync(path.join(data,edition==='gallery'?'gallery_state.json':'state.json'),'utf8'));
        const visit=saved.sync_pushes.find(x=>x.cmd==='visit.load').data;
        if(visit.visitor||!visit.acquire.includes(state.traveler.province))throw Error('Visitor claim did not persist');
        if(saved.offline_meta.gift_box&&saved.offline_meta.gift_box.specialtys?.length)throw Error('Unexpected giftbox change');
    }
    if (edition === 'gallery') {
        await page.evaluate(()=>core.PageManage.getInstance().addViewControl(AlbumController,core.ViewLayerType.WindowLayer));
        await page.waitForTimeout(5000);
        await page.screenshot({path:path.join(root, `.build/qa-desktop-${edition}-album.png`)});
        await page.evaluate(()=>{
            core.PageManage.getInstance().removeControl(AlbumController,core.ViewLayerType.WindowLayer);
            core.PageManage.getInstance().addViewControl(ToolBagViewController,core.ViewLayerType.NoticeLayer,core.RemoveViewType.Retain,{});
        });
        await page.waitForTimeout(1200);
        const before=await page.evaluate(()=>{
            const view=core.PageManage.getInstance().getControl(ToolBagViewController,core.ViewLayerType.NoticeLayer).view;
            const list=view.list,point=list.localToGlobal(list.width/2,list.height/2),stage=egret.MainContext.instance.stage;
            const canvas=document.querySelector('canvas').getBoundingClientRect();
            return {scroll:list.scrollV,x:canvas.left+point.x*canvas.width/stage.stageWidth,y:canvas.top+point.y*canvas.height/stage.stageHeight};
        });
        await page.mouse.move(before.x,before.y);
        await page.mouse.wheel(0,500);
        await page.waitForTimeout(200);
        const after=await page.evaluate(()=>core.PageManage.getInstance().getControl(ToolBagViewController,core.ViewLayerType.NoticeLayer).view.list.scrollV);
        if(after<=before.scroll)throw new Error(`Mouse wheel did not scroll tools: ${before.scroll} -> ${after}`);
        state.mouseWheel={before:before.scroll,after};
        await page.screenshot({path:path.join(root,`.build/qa-desktop-${edition}-tools.png`)});
        await page.evaluate(()=>core.PageManage.getInstance().removeControl(ToolBagViewController,core.ViewLayerType.NoticeLayer));
        for(const name of ['FurnitureViewController','HandCraftViewController','CameraViewControl','MomentViewControl']){
            await page.evaluate(name=>core.PageManage.getInstance().addViewControl(window[name],core.ViewLayerType.WindowLayer),name);
            await page.waitForTimeout(1200);
            await page.evaluate(name=>core.PageManage.getInstance().removeControl(window[name],core.ViewLayerType.WindowLayer),name);
        }
    }
    console.log(JSON.stringify({edition,errors,state},null,2));
    if(errors.length) process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{
    if(browser) await browser.close();
    if(service) service.kill();
    server.close();
});
