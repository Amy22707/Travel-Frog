using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class FrogWindow
{
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);

    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length != 3) return 2;
        var url = new Uri(args[0]);
        var profile = args[1];
        var gallery = args[2] == "gallery";
        var title = "旅行青蛙·中国之旅" + (gallery ? "图鉴版" : "");
        SetCurrentProcessExplicitAppUserModelID(gallery ? "TravelFrog.Gallery" : "TravelFrog.Normal");
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        using (var window = new Form())
        using (var webview = new WebView2())
        {
            var available = Screen.FromPoint(Cursor.Position).WorkingArea;
            window.Text = title;
            window.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            window.StartPosition = FormStartPosition.CenterScreen;
            window.Size = new Size(Math.Min(520, available.Width), Math.Min(850, available.Height));
            window.MinimumSize = new Size(320, 400);
            webview.Dock = DockStyle.Fill;
            window.Controls.Add(webview);
            window.Shown += async delegate
            {
                try
                {
                    var environment = await CoreWebView2Environment.CreateAsync(null, profile);
                    if (window.IsDisposed) return;
                    await webview.EnsureCoreWebView2Async(environment);
                    if (window.IsDisposed) return;
                    var settings = webview.CoreWebView2.Settings;
                    settings.AreDefaultContextMenusEnabled = false;
                    settings.IsStatusBarEnabled = false;
                    webview.Source = url;
                }
                catch (Exception error)
                {
                    if (window.IsDisposed) return;
                    Console.Error.WriteLine(error);
                    var message = error is WebView2RuntimeNotFoundException
                        ? "请安装 Microsoft Edge WebView2 Runtime 后重新打开游戏。"
                        : "无法打开游戏窗口。\n" + error.Message;
                    MessageBox.Show(window, message, title, MessageBoxButtons.OK, MessageBoxIcon.Error);
                    window.Close();
                }
            };
            Application.Run(window);
        }
        return 0;
    }
}
