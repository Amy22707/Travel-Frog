(function () {
const params=new URLSearchParams(location.search);
const edition=params.get("edition")==="gallery"?"gallery":"normal";
const frame=document.querySelector("#game");
const status=document.querySelector("#progress");
const loadingArt=document.querySelector("#loadingArt");
const stopLoading=()=>{loadingArt.hidden=true;document.querySelector("#home").classList.remove("booting");};
if(location.protocol==="file:"){
    stopLoading();
    const link=document.createElement("a");
    link.href=`http://127.0.0.1:52360/?edition=${edition}`;
    link.textContent="打开本机游戏";
    status.replaceChildren("请通过网页地址打开游戏。启动本机网页服务后，点击",link,"。");
    return;
}
let worker;
try{worker=new Worker("./worker.js");}
catch(error){stopLoading();status.textContent="本地运行环境启动失败："+error.message;return;}
let startupTimer=setTimeout(()=>{
    status.textContent="启动超时：请确认网页服务仍在运行，再刷新重试。存档不会被清除。";
    stopLoading();
},60000);
let available=false;
document.querySelector("#editionName").hidden=edition!=="gallery";
document.querySelector("#manifest").href=`manifest-${edition}.webmanifest`;
function showState(data){
    available=data.exists;
    status.textContent=available?data.name:"";
    document.querySelector("#play").disabled=!available;
    document.querySelector("#export").disabled=!available;
    document.querySelector("#previous").disabled=!data.hasBackup;
    document.querySelector("#create").disabled=available;
    document.querySelector("#import").disabled=false;
}
worker.onmessage=event=>{
    const m=event.data;
    if(m.type==="ready"||m.type==="state"){clearTimeout(startupTimer);startupTimer=null;stopLoading();showState(m.data);}
    else if(m.type==="protocol"||m.type==="opened")frame.contentWindow.postMessage({...m,frogPhone:true},location.origin);
    else if(m.type==="error"){clearTimeout(startupTimer);startupTimer=null;stopLoading();status.textContent=m.message;document.querySelector("#home").style.display="block";}
    else if(m.type==="export"){
        const bytes=new Uint8Array(m.bytes);
        const filename=`frog-${edition}${m.previous?"-before-import":""}.frogsave`;
        if(window.FrogNative){
            let text="";for(let i=0;i<bytes.length;i+=8192)text+=String.fromCharCode(...bytes.subarray(i,i+8192));
            FrogNative.saveDocument(btoa(text),filename,"application/octet-stream");
        }else{
            const url=URL.createObjectURL(new Blob([bytes],{type:"application/octet-stream"}));
            const link=document.createElement("a");link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
        }
    }
};
worker.onerror=event=>{clearTimeout(startupTimer);startupTimer=null;stopLoading();status.textContent="本地运行环境启动失败："+(event.message||"请确认网页服务仍在运行，再刷新重试。");};
window.addEventListener("message",event=>{
    if(event.source!==frame.contentWindow||!event.data.frogPhone)return;
    worker.postMessage(event.data);
});
function importBytes(bytes,name){worker.postMessage({type:"import",bytes,name},[bytes]);}
window.PhoneNativeImport=function(encoded,name){
    const text=atob(encoded),bytes=Uint8Array.from(text,c=>c.charCodeAt(0));importBytes(bytes.buffer,name);
};
window.PhoneNativeSaved=function(success){
    if(frame.contentWindow.__frogSaveComplete)frame.contentWindow.__frogSaveComplete(success);
};
const landscape=matchMedia("(orientation: landscape)");
function openInPortrait(){
    if(document.body.classList.contains("playing")&&!landscape.matches&&!frame.hasAttribute("src")){
        frame.src=`game/index.html?edition=${edition}&mobile=1`;
    }
}
landscape.addEventListener("change",openInPortrait);
document.querySelector("#play").onclick=()=>{
    document.querySelector("#home").style.display="none";
    document.body.classList.add("playing");
    document.querySelector("#manage").style.display="block";
    openInPortrait();
};
document.querySelector("#manage").onclick=()=>{
    document.body.classList.remove("playing");
    frame.removeAttribute("src");document.querySelector("#manage").style.display="none";
    document.querySelector("#home").style.display="block";
    worker.postMessage({type:"info"});
};
document.querySelector("#create").onclick=()=>{status.textContent="正在创建存档……";worker.postMessage({type:"create"});};
document.querySelector("#import").onclick=()=>{
    if(available&&!confirm("现有进度会自动备份。导入新的存档吗？"))return;
    if(window.FrogNative)FrogNative.pickSave();else document.querySelector("#file").click();
};
document.querySelector("#file").onchange=async event=>{const file=event.target.files[0];if(file)importBytes(await file.arrayBuffer(),file.name);event.target.value="";};
document.querySelector("#export").onclick=()=>worker.postMessage({type:"export"});
document.querySelector("#previous").onclick=()=>worker.postMessage({type:"export-previous"});
if(!window.FrogNative&&"serviceWorker" in navigator){
    navigator.serviceWorker.register("./sw.js").then(()=>navigator.serviceWorker.ready).then(registration=>{
        const button=document.querySelector("#cache");button.hidden=false;
        button.onclick=()=>{button.disabled=true;registration.active.postMessage({type:"cache-all"});};
        navigator.serviceWorker.addEventListener("message",event=>{
            if(event.data.type==="cache-progress")button.textContent=`离线资源 ${event.data.done}/${event.data.total}`;
            if(event.data.type==="cache-complete")button.textContent="离线资源已下载";
            if(event.data.type==="cache-error"){button.textContent="下载未完成，点击重试";button.disabled=false;}
        });
    }).catch(error=>{status.textContent="离线缓存需要 HTTPS 网站或本机环境："+error.message;});
}
if(navigator.locks)navigator.locks.request("frog-mobile-"+edition,{ifAvailable:true},async lock=>{
    if(!lock){clearTimeout(startupTimer);startupTimer=null;stopLoading();worker.terminate();status.textContent="这个版本已在另一个窗口中打开，请关闭旧窗口后刷新。";return;}
    worker.postMessage({type:"init",edition});await new Promise(()=>{});
}).catch(error=>{clearTimeout(startupTimer);startupTimer=null;stopLoading();status.textContent="无法打开本地存档："+error.message;});else worker.postMessage({type:"init",edition});
})();
