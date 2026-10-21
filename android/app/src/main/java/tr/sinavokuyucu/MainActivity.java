package tr.sinavokuyucu;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.DhcpInfo;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.core.content.FileProvider;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * Sınav Okuyucu telefon uygulaması.
 * Aynı Wi-Fi'deki bilgisayarda çalışan Sınav Okuyucu programını bulur ve arayüzünü açar.
 * Kağıt fotoğrafları yalnızca o bilgisayara gönderilir; internete çıkmaz.
 */
public class MainActivity extends Activity {
    private static final int REQ_CAMERA = 1;
    private static final int REQ_PICK = 2;
    private static final int REQ_PERM_CAMERA = 3;
    private static final int DISCOVERY_PORT = 47800;
    private static final byte[] DISCOVERY_ASK = "SINAV_OKUYUCU_NEREDE".getBytes(StandardCharsets.US_ASCII);
    private static final int PRIMARY = 0xFF1D4F7C;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private SharedPreferences prefs;
    private WebView web;
    private View connectView;
    private TextView statusText;
    private ProgressBar progress;
    private EditText addrInput;
    private Button retryBtn;
    private Button connectBtn;

    private String serverUrl;
    private ValueCallback<Uri[]> fileCallback;
    private Uri cameraUri;
    private volatile int searchId = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences("ayarlar", MODE_PRIVATE);
        getWindow().setStatusBarColor(PRIMARY);
        cleanCameraCache();

        FrameLayout root = new FrameLayout(this);
        web = new WebView(this);
        setupWebView();
        web.setVisibility(View.GONE);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        connectView = buildConnectView();
        root.addView(connectView, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        if (savedInstanceState != null) {
            web.restoreState(savedInstanceState);
            String cam = savedInstanceState.getString("kamera");
            if (cam != null) cameraUri = Uri.parse(cam);
        }
        startSearch();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
        if (cameraUri != null) outState.putString("kamera", cameraUri.toString());
    }

    @Override
    protected void onPause() {
        super.onPause();
        android.webkit.CookieManager.getInstance().flush();  // eşleştirme çerezi kalıcı olsun
    }

    // ------------------------------------------------------------------ bağlantı ekranı

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private static final int TEXT = 0xFF1B2230;
    private static final int MUTED = 0xFF687284;
    private static final int LINE = 0xFFCDD2DA;
    private static final int BG = 0xFFF4F5F7;

    private GradientDrawable shape(int fill, int stroke) {
        GradientDrawable d = new GradientDrawable();
        d.setCornerRadius(dp(6));
        d.setColor(fill);
        if (stroke != 0) d.setStroke(dp(1), stroke);
        return d;
    }

    private Button button(String text, boolean primary) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextSize(15);
        b.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        b.setTextColor(primary ? Color.WHITE : TEXT);
        b.setBackground(shape(primary ? PRIMARY : Color.WHITE, primary ? 0 : LINE));
        b.setStateListAnimator(null);
        return b;
    }

    private LinearLayout.LayoutParams lp(int height, int top) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                height == 0 ? ViewGroup.LayoutParams.WRAP_CONTENT : dp(height));
        p.topMargin = dp(top);
        return p;
    }

    private View buildConnectView() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(20), dp(56), dp(20), dp(24));

        TextView title = new TextView(this);
        title.setText("Sınav Okuyucu");
        title.setTextSize(22);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        title.setTextColor(TEXT);
        box.addView(title);

        TextView sub = new TextView(this);
        sub.setText("Bilgisayardaki programa bağlanır. Kağıt fotoğrafları yalnızca o bilgisayara gönderilir.");
        sub.setTextSize(13);
        sub.setTextColor(MUTED);
        box.addView(sub, lp(0, 4));

        // durum satırı: küçük dönen gösterge + metin
        LinearLayout statusRow = new LinearLayout(this);
        statusRow.setOrientation(LinearLayout.HORIZONTAL);
        statusRow.setGravity(Gravity.TOP);
        statusRow.setPadding(dp(14), dp(14), dp(14), dp(14));
        statusRow.setBackground(shape(Color.WHITE, 0xFFE1E4E9));
        progress = new ProgressBar(this);
        LinearLayout.LayoutParams pl = new LinearLayout.LayoutParams(dp(18), dp(18));
        pl.rightMargin = dp(10);
        pl.topMargin = dp(1);
        statusRow.addView(progress, pl);
        statusText = new TextView(this);
        statusText.setTextSize(14);
        statusText.setTextColor(TEXT);
        statusText.setLineSpacing(0, 1.25f);
        statusRow.addView(statusText, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        box.addView(statusRow, lp(0, 28));

        retryBtn = button("Tekrar ara", true);
        retryBtn.setOnClickListener(v -> startSearch());
        box.addView(retryBtn, lp(46, 12));

        TextView hint = new TextView(this);
        hint.setText("Bulunamazsa, bilgisayardaki programın ana sayfasında yazan adresi girin");
        hint.setTextSize(12);
        hint.setTextColor(MUTED);
        box.addView(hint, lp(0, 28));

        addrInput = new EditText(this);
        addrInput.setHint("192.168.1.106:8000");
        addrInput.setTextSize(15);
        addrInput.setTextColor(TEXT);
        addrInput.setHintTextColor(0xFF9AA3B2);
        addrInput.setBackground(shape(Color.WHITE, LINE));
        addrInput.setPadding(dp(12), 0, dp(12), 0);
        addrInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        addrInput.setImeOptions(EditorInfo.IME_ACTION_GO);
        addrInput.setSingleLine(true);
        addrInput.setOnEditorActionListener((v, actionId, e) -> {
            manualConnect();
            return true;
        });
        String saved = prefs.getString("url", "");
        if (!saved.isEmpty()) addrInput.setText(saved.replace("http://", ""));
        box.addView(addrInput, lp(46, 6));

        connectBtn = button("Bağlan", false);
        connectBtn.setOnClickListener(v -> manualConnect());
        box.addView(connectBtn, lp(46, 8));

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(BG);
        scroll.setFillViewport(true);
        scroll.addView(box);
        return scroll;
    }

    private void showConnect(String msg, boolean busy) {
        connectView.setVisibility(View.VISIBLE);
        web.setVisibility(View.GONE);
        statusText.setText(msg);
        progress.setVisibility(busy ? View.VISIBLE : View.INVISIBLE);
        retryBtn.setEnabled(!busy);
        connectBtn.setEnabled(!busy);
    }

    private void startSearch() {
        final int id = ++searchId;
        showConnect("Bilgisayar aranıyor…", true);
        new Thread(() -> {
            String found = null;
            String saved = prefs.getString("url", null);
            if (saved != null && ping(saved)) found = saved;
            if (found == null) found = discover();
            final String result = found;
            ui.post(() -> {
                if (id != searchId) return;
                if (result != null) openServer(result);
                else showConnect("Bilgisayar bulunamadı.\n\n"
                        + "• Bilgisayarda Sınav Okuyucu programı açık mı?\n"
                        + "• Telefon ve bilgisayar aynı Wi-Fi ağında mı?\n"
                        + "• Bilgisayarda güvenlik duvarı izni verildi mi?", false);
            });
        }).start();
    }

    private void manualConnect() {
        String text = addrInput.getText().toString().trim().replace("http://", "").replace("https://", "");
        while (text.endsWith("/")) text = text.substring(0, text.length() - 1);
        if (text.isEmpty()) return;
        if (!text.contains(":")) text += ":8000";
        final String url = "http://" + text;
        final int id = ++searchId;
        showConnect("Bağlanılıyor: " + text, true);
        new Thread(() -> {
            boolean ok = ping(url);
            ui.post(() -> {
                if (id != searchId) return;
                if (ok) openServer(url);
                else showConnect("Bu adreste Sınav Okuyucu bulunamadı: " + url.replace("http://", "")
                        + "\n\nAdresi ve güvenlik duvarı iznini kontrol edin.", false);
            });
        }).start();
    }

    private void openServer(String url) {
        serverUrl = url;
        prefs.edit().putString("url", url).apply();
        connectView.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        String current = web.getUrl();
        if (current == null || !current.startsWith(url)) web.loadUrl(url + "/");
        else web.reload();
    }

    /** Adreste Sınav Okuyucu sunucusu var mı? */
    private static boolean ping(String url) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(url + "/api/durum").openConnection();
            c.setConnectTimeout(1500);
            c.setReadTimeout(3000);
            if (c.getResponseCode() != 200) return false;
            try (InputStream in = c.getInputStream()) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buf = new byte[4096];
                int n;
                while ((n = in.read(buf)) > 0 && out.size() < 65536) out.write(buf, 0, n);
                return out.toString("UTF-8").contains("sinav-okuyucu");
            }
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /** Ağa yayın yaparak bilgisayarı bulur; bulamazsa null. */
    private String discover() {
        WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
        WifiManager.MulticastLock lock = null;
        DatagramSocket sock = null;
        try {
            if (wifi != null) {
                lock = wifi.createMulticastLock("sinav-okuyucu");
                lock.acquire();
            }
            List<InetAddress> targets = new ArrayList<>();
            targets.add(InetAddress.getByName("255.255.255.255"));
            if (wifi != null) {
                DhcpInfo d = wifi.getDhcpInfo();
                if (d != null && d.ipAddress != 0) {
                    int bc = (d.ipAddress & d.netmask) | ~d.netmask;
                    if (d.netmask == 0) bc = (d.ipAddress & 0x00FFFFFF) | 0xFF000000; // /24 varsay
                    byte[] q = new byte[4];
                    for (int k = 0; k < 4; k++) q[k] = (byte) ((bc >> (k * 8)) & 0xFF);
                    targets.add(InetAddress.getByAddress(q));
                }
            }
            sock = new DatagramSocket();
            sock.setBroadcast(true);
            sock.setSoTimeout(800);
            byte[] buf = new byte[512];
            for (int attempt = 0; attempt < 4; attempt++) {
                for (InetAddress t : targets) {
                    try {
                        sock.send(new DatagramPacket(DISCOVERY_ASK, DISCOVERY_ASK.length, t, DISCOVERY_PORT));
                    } catch (Exception ignored) {
                    }
                }
                long until = System.currentTimeMillis() + 800;
                while (System.currentTimeMillis() < until) {
                    DatagramPacket p = new DatagramPacket(buf, buf.length);
                    try {
                        sock.receive(p);
                    } catch (SocketTimeoutException e) {
                        break;
                    }
                    try {
                        JSONObject o = new JSONObject(new String(p.getData(), 0, p.getLength(), StandardCharsets.UTF_8));
                        if ("sinav-okuyucu".equals(o.optString("uygulama"))) {
                            String url = "http://" + p.getAddress().getHostAddress() + ":" + o.optInt("port", 8000);
                            if (ping(url)) return url;
                        }
                    } catch (Exception ignored) {
                    }
                }
            }
        } catch (Exception ignored) {
        } finally {
            if (sock != null) sock.close();
            if (lock != null && lock.isHeld()) lock.release();
        }
        return null;
    }

    // ------------------------------------------------------------------ WebView

    private void setupWebView() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setUserAgentString(s.getUserAgentString() + " SinavOkuyucuApp/1.0");
        web.addJavascriptInterface(new Bridge(), "SinavOkuyucuApp");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                if (serverUrl != null && u.toString().startsWith(serverUrl)) return false;
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (ActivityNotFoundException ignored) {
                }
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) {
                    showConnect("Bilgisayarla bağlantı kurulamadı.\n\nBilgisayarda Sınav Okuyucu açık mı, telefon aynı Wi-Fi'de mi?", false);
                }
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                if (params.isCaptureEnabled()) openCamera();
                else openPicker(params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE, mimeTypes(params.getAcceptTypes()));
                return true;
            }
        });

        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, length) -> {
            try {
                String name = URLUtil.guessFileName(url, contentDisposition, mimeType);
                DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
                String cookie = android.webkit.CookieManager.getInstance().getCookie(url);
                if (cookie != null) r.addRequestHeader("Cookie", cookie);  // eşleşmiş cihaz anahtarı
                r.setMimeType(mimeType);
                r.setTitle(name);
                r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
                } else {
                    r.setDestinationInExternalFilesDir(this, Environment.DIRECTORY_DOWNLOADS, name);
                }
                ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
                Toast.makeText(this, "İndiriliyor: " + name + (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q
                        ? "\n(İndirilenler klasörüne)" : "\n(bildirimden açabilirsiniz)"), Toast.LENGTH_LONG).show();
            } catch (Exception e) {
                Toast.makeText(this, "İndirilemedi: " + e.getMessage(), Toast.LENGTH_LONG).show();
            }
        });
    }

    /** Web arayüzünden çağrılabilen işlemler. */
    private class Bridge {
        @JavascriptInterface
        public void baglantiyiDegistir() {
            ui.post(() -> {
                prefs.edit().remove("url").apply();
                searchId++;
                showConnect("Bağlanmak istediğiniz bilgisayarın adresini girin ya da yeniden arayın.", false);
            });
        }

        /** Bilgisayarla eşleşince verilen anahtar (yerel yüklemelerde de kullanılır). */
        @JavascriptInterface
        public void anahtarKaydet(String key) {
            if (serverUrl != null && key != null) prefs.edit().putString("anahtar_" + serverUrl, key).apply();
            android.webkit.CookieManager.getInstance().flush();
        }

        /** Seri çekim kamerasını aç: kağıtlar otomatik çekilip bu sınava gönderilir. */
        @JavascriptInterface
        public void seriCekim(String examId, String title, int pages) {
            seriCekim(examId, title, pages, 1);
        }

        /** sheets: bir fotoğrafta kaç kağıt var (kağıtlar yan yana dizilip tek fotoğrafta çekilir). */
        @JavascriptInterface
        public void seriCekim(String examId, String title, int pages, int sheets) {
            if (serverUrl == null || examId == null || !examId.matches("\\w+")) return;
            ui.post(() -> {
                Intent i = new Intent(MainActivity.this, SeriCekimActivity.class);
                i.putExtra(SeriCekimActivity.EXTRA_URL, serverUrl);
                i.putExtra(SeriCekimActivity.EXTRA_EXAM, examId);
                i.putExtra(SeriCekimActivity.EXTRA_TITLE, title);
                i.putExtra(SeriCekimActivity.EXTRA_PAGES, pages);
                i.putExtra(SeriCekimActivity.EXTRA_SHEETS, sheets);
                i.putExtra(SeriCekimActivity.EXTRA_KEY, prefs.getString("anahtar_" + serverUrl, ""));
                startActivity(i);
            });
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        // seri çekimden dönünce tabloyu hemen tazele
        if (web != null && web.getVisibility() == View.VISIBLE) {
            web.evaluateJavascript("typeof reload==='function'&&typeof state!=='undefined'&&state.exam&&reload()", null);
        }
    }

    private void cleanCameraCache() {
        File dir = new File(getCacheDir(), "kamera");
        File[] files = dir.listFiles();
        if (files != null) for (File f : files) //noinspection ResultOfMethodCallIgnored
            f.delete();
    }

    private void openCamera() {
        // Uygulama kamera izni beyan ettiği için sistem kamerasını açmak da bu izni ister
        if (checkSelfPermission(android.Manifest.permission.CAMERA) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{android.Manifest.permission.CAMERA}, REQ_PERM_CAMERA);
            return;
        }
        try {
            File dir = new File(getCacheDir(), "kamera");
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
            File f = new File(dir, "kagit_" + System.currentTimeMillis() + ".jpg");
            cameraUri = FileProvider.getUriForFile(this, getPackageName() + ".dosyalar", f);
            Intent i = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
            i.putExtra(MediaStore.EXTRA_OUTPUT, cameraUri);
            i.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);
            startActivityForResult(i, REQ_CAMERA);
        } catch (Exception e) {
            Toast.makeText(this, "Kamera açılamadı, galeriden seçin.", Toast.LENGTH_SHORT).show();
            openPicker(false, new String[]{"image/*"});
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode != REQ_PERM_CAMERA) return;
        if (results.length > 0 && results[0] == android.content.pm.PackageManager.PERMISSION_GRANTED) {
            openCamera();
        } else {
            Toast.makeText(this, "Kamera izni verilmedi; fotoğrafı galeriden seçebilirsiniz.", Toast.LENGTH_LONG).show();
            if (fileCallback != null) fileCallback.onReceiveValue(null);
            fileCallback = null;
        }
    }

    /** accept="image/*,application/pdf" ya da ".xlsx,.csv" → MIME türleri. */
    private static String[] mimeTypes(String[] accept) {
        java.util.LinkedHashSet<String> out = new java.util.LinkedHashSet<>();
        for (String a : accept == null ? new String[0] : accept) {
            if (a == null) continue;
            for (String t : a.split(",")) {
                t = t.trim().toLowerCase(java.util.Locale.ROOT);
                if (t.isEmpty()) continue;
                if (t.startsWith(".")) {
                    String m = android.webkit.MimeTypeMap.getSingleton().getMimeTypeFromExtension(t.substring(1));
                    if (t.equals(".csv")) out.add("text/comma-separated-values");
                    out.add(m != null ? m : "*/*");
                } else out.add(t);
            }
        }
        return out.isEmpty() ? new String[]{"*/*"} : out.toArray(new String[0]);
    }

    private void openPicker(boolean multiple, String[] types) {
        Intent i = new Intent(Intent.ACTION_GET_CONTENT);
        i.addCategory(Intent.CATEGORY_OPENABLE);
        if (types.length == 1) {
            i.setType(types[0]);
        } else {
            i.setType("*/*");
            i.putExtra(Intent.EXTRA_MIME_TYPES, types);
        }
        i.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, multiple);
        try {
            startActivityForResult(Intent.createChooser(i, "Kağıt fotoğrafı seçin"), REQ_PICK);
        } catch (ActivityNotFoundException e) {
            if (fileCallback != null) fileCallback.onReceiveValue(null);
            fileCallback = null;
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (fileCallback == null) {
            if (requestCode == REQ_CAMERA && resultCode == RESULT_OK) {
                Toast.makeText(this, "Telefon, kamera açıkken uygulamayı kapattı; fotoğrafı yeniden çekin.", Toast.LENGTH_LONG).show();
            }
            return;
        }
        Uri[] result = null;
        if (resultCode == RESULT_OK) {
            if (requestCode == REQ_CAMERA) {
                result = new Uri[]{cameraUri};
            } else if (data != null) {
                ClipData clip = data.getClipData();
                if (clip != null && clip.getItemCount() > 0) {
                    result = new Uri[clip.getItemCount()];
                    for (int k = 0; k < clip.getItemCount(); k++) result[k] = clip.getItemAt(k).getUri();
                } else if (data.getData() != null) {
                    result = new Uri[]{data.getData()};
                }
            }
        }
        fileCallback.onReceiveValue(result);
        fileCallback = null;
    }

    @Override
    public void onBackPressed() {
        if (web.getVisibility() == View.VISIBLE && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }
}
