/* Run the local desktop client in an isolated browser profile. */
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {spawn} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const playwright = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const edition = process.argv[2] || 'normal';
const web = path.join(root, 'release/TravelFrog/web/game');
const data = path.join(root, '.build/qa-browser', edition);
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
    service = spawn(path.join(root, 'release/TravelFrog/FrogServer.exe'), args, {cwd: root, windowsHide:true, stdio:'ignore'});
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
    if (edition === 'gallery') {
        await page.evaluate(()=>core.PageManage.getInstance().addViewControl(AlbumController,core.ViewLayerType.WindowLayer));
        await page.waitForTimeout(5000);
        await page.screenshot({path:path.join(root, `.build/qa-desktop-${edition}-album.png`)});
    }
    console.log(JSON.stringify({edition,errors,state},null,2));
    if(errors.length) process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{
    if(browser) await browser.close();
    if(service) service.kill();
    server.close();
});
