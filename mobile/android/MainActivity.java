package org.travelfrog.offline;

import android.app.Activity;
import android.content.Intent;
import android.database.Cursor;
import android.os.Bundle;
import android.provider.OpenableColumns;
import android.util.Base64;
import android.util.Log;
import android.webkit.ConsoleMessage;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.view.WindowManager;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import org.json.JSONObject;

public class MainActivity extends Activity {
    private WebView web;
    private AssetServer server;
    private byte[] pending;

    public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN, WindowManager.LayoutParams.FLAG_FULLSCREEN);
        try {
            String packageName = getPackageName();
            boolean gallery = packageName.endsWith(".gallery");
            int port = packageName.startsWith("org.travelfrog.china.") ? 53182 : 53180;
            if (gallery) port++;
            server = new AssetServer(getAssets(), port);
            server.start();
            web = new WebView(this);
            setContentView(web);
            WebSettings settings = web.getSettings();
            settings.setJavaScriptEnabled(true);
            settings.setDomStorageEnabled(true);
            settings.setDatabaseEnabled(true);
            settings.setMediaPlaybackRequiresUserGesture(false);
            web.setWebViewClient(new WebViewClient());
            web.setWebChromeClient(new WebChromeClient() {
                public boolean onConsoleMessage(ConsoleMessage message) {
                    Log.i("FrogPhone", message.message());
                    return true;
                }
            });
            web.addJavascriptInterface(this, "FrogNative");
            web.loadUrl("http://127.0.0.1:" + port + "/index.html?edition=" + (gallery ? "gallery" : "normal"));
        } catch (Exception error) {
            android.widget.TextView message = new android.widget.TextView(this);
            message.setText("游戏启动失败：" + error.toString());
            setContentView(message);
            Log.e("FrogPhone", "Startup failed", error);
        }
    }

    @JavascriptInterface
    public void pickSave() {
        runOnUiThread(new Runnable() {
            public void run() {
                Intent intent = new Intent("android.intent.action.OPEN_DOCUMENT");
                intent.addCategory("android.intent.category.OPENABLE");
                intent.setType("*/*");
                startActivityForResult(intent, 110);
            }
        });
    }

    @JavascriptInterface
    public void saveDocument(String encoded, final String name, final String mime) {
        pending = Base64.decode(encoded, Base64.DEFAULT);
        runOnUiThread(new Runnable() {
            public void run() {
                Intent intent = new Intent("android.intent.action.CREATE_DOCUMENT");
                intent.addCategory("android.intent.category.OPENABLE");
                intent.setType(mime);
                intent.putExtra("android.intent.extra.TITLE", name);
                startActivityForResult(intent, 111);
            }
        });
    }

    @JavascriptInterface
    public void saveImage(String encoded, String name) {
        saveDocument(encoded, name, "image/png");
    }

    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (result != RESULT_OK || data == null) {
            if (request == 111) {
                pending = null;
                web.evaluateJavascript("window.PhoneNativeSaved(false)", null);
            }
            return;
        }
        try {
            if (request == 110) {
                String name = "import.frogsave";
                Cursor cursor = getContentResolver().query(data.getData(), null, null, null, null);
                if (cursor != null) {
                    if (cursor.moveToFirst()) name = cursor.getString(cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME));
                    cursor.close();
                }
                InputStream input = getContentResolver().openInputStream(data.getData());
                ByteArrayOutputStream output = new ByteArrayOutputStream();
                byte[] buffer = new byte[16384];
                int count;
                while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                input.close();
                String encoded = Base64.encodeToString(output.toByteArray(), Base64.NO_WRAP);
                web.evaluateJavascript("window.PhoneNativeImport(" + JSONObject.quote(encoded) + "," + JSONObject.quote(name) + ")", null);
            } else if (request == 111) {
                OutputStream output = getContentResolver().openOutputStream(data.getData());
                output.write(pending);
                output.close();
                pending = null;
                web.evaluateJavascript("window.PhoneNativeSaved(true)", null);
            }
        } catch (Exception error) {
            if (request == 111) {
                pending = null;
                web.evaluateJavascript("window.PhoneNativeSaved(false)", null);
            }
            Log.e("FrogPhone", "File operation failed", error);
            android.widget.Toast.makeText(this, "文件操作失败：" + error.getMessage(), android.widget.Toast.LENGTH_LONG).show();
        }
    }

    protected void onDestroy() {
        if (web != null) web.destroy();
        if (server != null) server.close();
        super.onDestroy();
    }
}
