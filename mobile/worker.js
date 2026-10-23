/* Python protocol worker; all runtime files are packaged locally. */
importScripts("./pyodide/pyodide.js");
let py, ready, syncing = Promise.resolve();
self.persistFiles = function () {
    syncing = syncing.then(() => new Promise((resolve, reject) => py.FS.syncfs(false, error => error ? reject(error) : resolve())));
    return syncing;
};
self.postProtocol = function (socket, data) { postMessage({type:"protocol", socket, data}); };
async function initialize(edition) {
    py = await loadPyodide({indexURL:"./pyodide/"});
    const response = await fetch("./python-app.zip");
    if (!response.ok) throw new Error("本地游戏逻辑未能读取。");
    py.unpackArchive(new Uint8Array(await response.arrayBuffer()), "zip", {extractDir:"/app"});
    py.FS.mkdirTree("/persistent/"+edition);
    py.FS.mount(py.FS.filesystems.IDBFS, {}, "/persistent/"+edition);
    await new Promise((resolve,reject)=>py.FS.syncfs(true,error=>error?reject(error):resolve()));
    await py.runPythonAsync("import sys; sys.path.insert(0, '/app'); from mobile import runtime");
    const result = await py.runPythonAsync(`runtime.configure(${JSON.stringify(edition)})`);
    postMessage({type:"ready", data:JSON.parse(result)});
}
self.onmessage = async function (event) {
    const m=event.data;
    try {
        if(m.type==="init") { ready=initialize(m.edition); await ready; return; }
        await ready;
        if(m.type==="open") {
            py.runPythonAsync(`await runtime.serve(${m.socket})`).catch(error=>postMessage({type:"error",message:String(error)}));
            postMessage({type:"opened",socket:m.socket});
        } else if(m.type==="send") {
            py.globals.set("incoming_frame",m.data);
            py.runPython(`runtime.receive(${m.socket}, incoming_frame)`);
        } else if(m.type==="close") {
            py.runPython(`runtime.disconnect(${m.socket})`);
        } else if(m.type==="create") {
            const result=await py.runPythonAsync("await runtime.create()");
            postMessage({type:"state",data:JSON.parse(result)});
        } else if(m.type==="import") {
            const filename=m.name.endsWith(".jsonl")?"import.jsonl":m.name.endsWith(".json")?"import.json":"import.frogsave";
            py.FS.writeFile("/tmp/"+filename,new Uint8Array(m.bytes));
            const result=await py.runPythonAsync(`await runtime.load_file('${filename}')`);
            postMessage({type:"state",data:JSON.parse(result)});
        } else if(m.type==="export") {
            const proxy=await py.runPythonAsync("await runtime.export()");
            const bytes=new Uint8Array(proxy.toJs()); proxy.destroy();
            postMessage({type:"export",bytes:bytes.buffer},[bytes.buffer]);
        }
    } catch(error) { postMessage({type:"error",message:String(error)}); }
};
