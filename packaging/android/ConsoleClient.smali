.class public Lorg/travelfrog/offline/ConsoleClient;
.super Landroid/webkit/WebChromeClient;

.method public constructor <init>()V
    .locals 0
    invoke-direct {p0}, Landroid/webkit/WebChromeClient;-><init>()V
    return-void
.end method

.method public onConsoleMessage(Landroid/webkit/ConsoleMessage;)Z
    .locals 2
    const-string v0, "FrogOffline"
    invoke-virtual {p1}, Landroid/webkit/ConsoleMessage;->message()Ljava/lang/String;
    move-result-object v1
    invoke-static {v0, v1}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    const/4 v0, 0x1
    return v0
.end method
