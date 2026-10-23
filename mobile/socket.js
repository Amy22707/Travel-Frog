/* The phone talks to its own protocol worker, without a network server. */
(function () {
    const sockets=new Map(); let identifier=Date.now()*1000;
    const NativeSocket=window.WebSocket;
    class LocalSocket extends EventTarget {
        constructor(url,protocols) {
            super();
            if(!String(url).includes("frog-mobile.local")) return new NativeSocket(url,protocols);
            this.url=String(url);this.readyState=0;this.bufferedAmount=0;this.protocol="";this.extensions="";
            this.binaryType="arraybuffer";this.identifier=++identifier;sockets.set(this.identifier,this);
            parent.postMessage({frogPhone:true,type:"open",socket:this.identifier},location.origin);
        }
        emit(type,event) {this.dispatchEvent(event);if(typeof this["on"+type]==="function")this["on"+type](event);}
        send(data) {
            if(this.readyState!==1) throw new Error("本地运行环境尚未连接。");
            parent.postMessage({frogPhone:true,type:"send",socket:this.identifier,data:String(data)},location.origin);
        }
        close() {
            if(this.readyState===3)return;
            this.readyState=3;parent.postMessage({frogPhone:true,type:"close",socket:this.identifier},location.origin);
            this.emit("close",new CloseEvent("close",{code:1000,wasClean:true}));sockets.delete(this.identifier);
        }
    }
    for(const [key,value] of Object.entries({CONNECTING:0,OPEN:1,CLOSING:2,CLOSED:3})) {
        LocalSocket[key]=value;LocalSocket.prototype[key]=value;
    }
    window.addEventListener("message",event=>{
        if(event.source!==parent||!event.data.frogPhone)return;
        const m=event.data,socket=sockets.get(m.socket);if(!socket)return;
        if(m.type==="opened"){socket.readyState=1;socket.emit("open",new Event("open"));}
        if(m.type==="protocol")socket.emit("message",new MessageEvent("message",{data:m.data}));
    });
    window.WebSocket=LocalSocket;
    window.addEventListener("pagehide",()=>{for(const socket of sockets.values())socket.close();});
    window.FROG_SERVER_URL="ws://frog-mobile.local";
}());
