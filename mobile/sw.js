const CACHE="frog-offline-v1";
self.addEventListener("install",event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(["./","./index.html","./phone.js","./worker.js","./icon.png"])).then(()=>self.skipWaiting())));
self.addEventListener("activate",event=>event.waitUntil(self.clients.claim()));
self.addEventListener("fetch",event=>{
    if(event.request.method!=="GET")return;
    event.respondWith(caches.open(CACHE).then(async cache=>{
        const path=new URL(event.request.url).pathname;
        const program=/\.(html|js|json|webmanifest)$/.test(path)||path.endsWith("/")||path.endsWith("/python-app.zip");
        if(program){
            try{
                const response=await fetch(event.request);
                if(response.ok){await cache.put(event.request,response.clone());return response;}
            }catch(error){}
        }
        const saved=await cache.match(event.request,{ignoreSearch:true});
        if(saved)return saved;
        const response=await fetch(event.request);
        if(response.ok)await cache.put(event.request,response.clone());
        return response;
    }));
});
self.addEventListener("message",event=>{
    if(event.data.type!=="cache-all")return;
    event.waitUntil((async()=>{
        const notify=message=>event.source.postMessage(message);
        try{
            const files=await (await fetch("./offline-files.json")).json();
            const cache=await caches.open(CACHE);let cursor=0,done=0;
            await Promise.all(Array.from({length:6},async()=>{
                while(cursor<files.length){
                    const url=files[cursor++];
                    if(!await cache.match(url)){
                        const response=await fetch(url);if(!response.ok)throw new Error(url);
                        await cache.put(url,response);
                    }
                    done++;if(done%20===0)notify({type:"cache-progress",done,total:files.length});
                }
            }));
            notify({type:"cache-complete"});
        }catch(error){notify({type:"cache-error",message:String(error)});}
    })());
});
