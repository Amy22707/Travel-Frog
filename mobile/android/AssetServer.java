package org.travelfrog.offline;

import android.content.res.AssetManager;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URLDecoder;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class AssetServer extends Thread {
    private final AssetManager assets;
    private final ServerSocket server;
    private final ExecutorService pool = Executors.newCachedThreadPool();
    private volatile boolean running = true;

    public AssetServer(AssetManager assets, int port) throws Exception {
        this.assets = assets;
        server = new ServerSocket(port, 32, InetAddress.getByName("127.0.0.1"));
    }

    public void run() {
        while (running) {
            try {
                final Socket socket = server.accept();
                pool.execute(new Runnable() { public void run() { respond(socket); } });
            } catch (Exception error) {
                if (running) android.util.Log.e("FrogPhone", "Local resource request failed", error);
            }
        }
    }

    private void respond(Socket socket) {
        try {
            socket.setSoTimeout(15000);
            BufferedReader reader = new BufferedReader(new InputStreamReader(socket.getInputStream(), "UTF-8"));
            String request = reader.readLine();
            if (request == null) return;
            String header;
            while ((header = reader.readLine()) != null && header.length() > 0) {}
            String[] parts = request.split(" ");
            String file = URLDecoder.decode(parts[1].split("\\?")[0], "UTF-8");
            if (file.equals("/")) file = "/index.html";
            OutputStream output = socket.getOutputStream();
            if (file.contains("..")) return;
            InputStream input;
            try { input = assets.open("mobile" + file); }
            catch (Exception missing) { output.write("HTTP/1.1 404 Not Found\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".getBytes("UTF-8")); return; }
            String mime = "application/octet-stream";
            if (file.endsWith(".html")) mime = "text/html; charset=utf-8";
            else if (file.endsWith(".js")) mime = "text/javascript; charset=utf-8";
            else if (file.endsWith(".json")) mime = "application/json";
            else if (file.endsWith(".wasm")) mime = "application/wasm";
            else if (file.endsWith(".png")) mime = "image/png";
            output.write(("HTTP/1.1 200 OK\r\nContent-Type: " + mime + "\r\nConnection: close\r\n\r\n").getBytes("UTF-8"));
            byte[] buffer = new byte[32768];
            int count;
            while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            input.close();
            output.flush();
        } catch (Exception error) {
            android.util.Log.w("FrogPhone", "Resource connection ended", error);
        } finally { try { socket.close(); } catch (Exception ignored) {} }
    }

    public void close() {
        running = false;
        try { server.close(); } catch (Exception ignored) {}
        pool.shutdownNow();
    }
}
