.class public Lorg/travelfrog/offline/MainActivity;
.super Landroid/app/Activity;

.field private web:Landroid/webkit/WebView;
.field private pending:[B

.method public constructor <init>()V
    .locals 0
    invoke-direct {p0}, Landroid/app/Activity;-><init>()V
    return-void
.end method

.method public onCreate(Landroid/os/Bundle;)V
    .locals 5
    invoke-super {p0, p1}, Landroid/app/Activity;->onCreate(Landroid/os/Bundle;)V
    invoke-virtual {p0}, Landroid/app/Activity;->getWindow()Landroid/view/Window;
    move-result-object v0
    const/16 v1, 0x400
    invoke-virtual {v0, v1, v1}, Landroid/view/Window;->setFlags(II)V
    new-instance v0, Landroid/webkit/WebView;
    invoke-direct {v0, p0}, Landroid/webkit/WebView;-><init>(Landroid/content/Context;)V
    iput-object v0, p0, Lorg/travelfrog/offline/MainActivity;->web:Landroid/webkit/WebView;
    invoke-virtual {p0, v0}, Landroid/app/Activity;->setContentView(Landroid/view/View;)V
    invoke-virtual {v0}, Landroid/webkit/WebView;->getSettings()Landroid/webkit/WebSettings;
    move-result-object v1
    const/4 v2, 0x1
    invoke-virtual {v1, v2}, Landroid/webkit/WebSettings;->setJavaScriptEnabled(Z)V
    invoke-virtual {v1, v2}, Landroid/webkit/WebSettings;->setDomStorageEnabled(Z)V
    invoke-virtual {v1, v2}, Landroid/webkit/WebSettings;->setAllowFileAccess(Z)V
    invoke-virtual {v1, v2}, Landroid/webkit/WebSettings;->setAllowFileAccessFromFileURLs(Z)V
    invoke-virtual {v1, v2}, Landroid/webkit/WebSettings;->setAllowUniversalAccessFromFileURLs(Z)V
    const/4 v2, 0x0
    invoke-virtual {v1, v2}, Landroid/webkit/WebSettings;->setMediaPlaybackRequiresUserGesture(Z)V
    invoke-virtual {v1, v2}, Landroid/webkit/WebSettings;->setMixedContentMode(I)V
    new-instance v3, Landroid/webkit/WebViewClient;
    invoke-direct {v3}, Landroid/webkit/WebViewClient;-><init>()V
    invoke-virtual {v0, v3}, Landroid/webkit/WebView;->setWebViewClient(Landroid/webkit/WebViewClient;)V
    new-instance v3, Lorg/travelfrog/offline/ConsoleClient;
    invoke-direct {v3}, Lorg/travelfrog/offline/ConsoleClient;-><init>()V
    invoke-virtual {v0, v3}, Landroid/webkit/WebView;->setWebChromeClient(Landroid/webkit/WebChromeClient;)V
    const-string v3, "FrogNative"
    invoke-virtual {v0, p0, v3}, Landroid/webkit/WebView;->addJavascriptInterface(Ljava/lang/Object;Ljava/lang/String;)V
    const-string v3, "file:///android_asset/game/index.html"
    invoke-virtual {v0, v3}, Landroid/webkit/WebView;->loadUrl(Ljava/lang/String;)V
    return-void
.end method

.method public saveImage(Ljava/lang/String;Ljava/lang/String;)V
    .locals 3
    .annotation runtime Landroid/webkit/JavascriptInterface;
    .end annotation
    const/4 v0, 0x0
    invoke-static {p1, v0}, Landroid/util/Base64;->decode(Ljava/lang/String;I)[B
    move-result-object v0
    iput-object v0, p0, Lorg/travelfrog/offline/MainActivity;->pending:[B
    new-instance v0, Landroid/content/Intent;
    const-string v1, "android.intent.action.CREATE_DOCUMENT"
    invoke-direct {v0, v1}, Landroid/content/Intent;-><init>(Ljava/lang/String;)V
    const-string v1, "android.intent.category.OPENABLE"
    invoke-virtual {v0, v1}, Landroid/content/Intent;->addCategory(Ljava/lang/String;)Landroid/content/Intent;
    const-string v1, "image/png"
    invoke-virtual {v0, v1}, Landroid/content/Intent;->setType(Ljava/lang/String;)Landroid/content/Intent;
    const-string v1, "android.intent.extra.TITLE"
    invoke-virtual {v0, v1, p2}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;
    const/16 v1, 0x4d2
    invoke-virtual {p0, v0, v1}, Landroid/app/Activity;->startActivityForResult(Landroid/content/Intent;I)V
    return-void
.end method

.method protected onActivityResult(IILandroid/content/Intent;)V
    .locals 3
    invoke-super {p0, p1, p2, p3}, Landroid/app/Activity;->onActivityResult(IILandroid/content/Intent;)V
    const/16 v0, 0x4d2
    if-ne p1, v0, :done
    const-string v2, "window.__frogSaveComplete(false)"
    const/4 v0, -0x1
    if-ne p2, v0, :notify
    if-eqz p3, :notify
    :try_start
    invoke-virtual {p0}, Landroid/app/Activity;->getContentResolver()Landroid/content/ContentResolver;
    move-result-object v0
    invoke-virtual {p3}, Landroid/content/Intent;->getData()Landroid/net/Uri;
    move-result-object v1
    invoke-virtual {v0, v1}, Landroid/content/ContentResolver;->openOutputStream(Landroid/net/Uri;)Ljava/io/OutputStream;
    move-result-object v0
    iget-object v1, p0, Lorg/travelfrog/offline/MainActivity;->pending:[B
    invoke-virtual {v0, v1}, Ljava/io/OutputStream;->write([B)V
    invoke-virtual {v0}, Ljava/io/OutputStream;->close()V
    const-string v2, "window.__frogSaveComplete(true)"
    :try_end
    .catch Ljava/lang/Exception; {:try_start .. :try_end} :failed
    goto :notify
    :failed
    move-exception v0
    :notify
    const/4 v1, 0x0
    iput-object v1, p0, Lorg/travelfrog/offline/MainActivity;->pending:[B
    iget-object v0, p0, Lorg/travelfrog/offline/MainActivity;->web:Landroid/webkit/WebView;
    invoke-virtual {v0, v2, v1}, Landroid/webkit/WebView;->evaluateJavascript(Ljava/lang/String;Landroid/webkit/ValueCallback;)V
    :done
    return-void
.end method

.method protected onDestroy()V
    .locals 1
    iget-object v0, p0, Lorg/travelfrog/offline/MainActivity;->web:Landroid/webkit/WebView;
    if-eqz v0, :done
    invoke-virtual {v0}, Landroid/webkit/WebView;->destroy()V
    :done
    invoke-super {p0}, Landroid/app/Activity;->onDestroy()V
    return-void
.end method
