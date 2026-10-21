package tr.sinavokuyucu;

import android.Manifest;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Matrix;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.MediaActionSound;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Size;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.ComponentActivity;
import androidx.annotation.NonNull;
import androidx.camera.core.Camera;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.FocusMeteringAction;
import androidx.camera.core.ImageAnalysis;
import androidx.camera.core.ImageCapture;
import androidx.camera.core.ImageCaptureException;
import androidx.camera.core.ImageProxy;
import androidx.camera.core.MeteringPoint;
import androidx.camera.core.Preview;
import androidx.camera.core.resolutionselector.AspectRatioStrategy;
import androidx.camera.core.resolutionselector.ResolutionSelector;
import androidx.camera.core.resolutionselector.ResolutionStrategy;
import androidx.camera.lifecycle.ProcessCameraProvider;
import androidx.camera.view.PreviewView;
import androidx.core.content.ContextCompat;

import com.google.common.util.concurrent.ListenableFuture;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Seri çekim: kamera açık kalır, öğretmen kağıtları sırayla koyar.
 * Otomatik modda yeni bir kağıt konup görüntü sabitlenince fotoğraf kendiliğinden çekilir,
 * bulanık çıkarsa yeniden çekilir ve arka planda bilgisayara gönderilir.
 * Sınav kaç sayfaysa sunucu, gelen sayfaları sırayla aynı öğrenciye bağlar.
 */
public class SeriCekimActivity extends ComponentActivity {
    public static final String EXTRA_URL = "url";
    public static final String EXTRA_EXAM = "sinav";
    public static final String EXTRA_TITLE = "baslik";
    public static final String EXTRA_PAGES = "sayfa";
    public static final String EXTRA_KEY = "anahtar";
    public static final String EXTRA_SHEETS = "kagit";  // bir fotoğrafta kaç kağıt var (1, 2, 4)

    private static final int REQ_CAMERA = 10;
    private static final int PRIMARY = 0xFF1D4F7C;

    // Görüntü analizi eşikleri (0-255 parlaklık ölçeğinde, ızgara hücre ortalamaları üzerinden)
    private static final int GW = 40, GH = 30;
    private static final float MOVE_T = 14f;    // bunun üstü: el/kağıt hareketi var
    private static final float STEADY_T = 4.5f; // bunun altı: görüntü sabit
    private static final float CHANGE_T = 4f;   // son çekimden bu kadar farklıysa yeni kağıt
    private static final long STEADY_MS = 700;  // çekmeden önce bu kadar sabit kalmalı
    private static final int MAX_SIDE = 2400;   // gönderilen fotoğrafın uzun kenarı
    private static final int MAX_SIDE_MULTI = 4096;  // fotoğrafta birden çok kağıt varsa: her kağıt yeterli çözünürlükte kalsın

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService analysisExec = Executors.newSingleThreadExecutor();
    private final ExecutorService ioExec = Executors.newSingleThreadExecutor();
    private final Thread uploader = new Thread(this::uploadLoop, "yukleyici");

    private String baseUrl, examId, session, authKey;
    private int pagesPer;
    private int sheetsPer = 1;
    private SharedPreferences prefs;

    private PreviewView preview;
    private Camera camera;
    private ImageCapture capture;
    private TextView titleView, statusView, hintView, nextView, autoView;
    private ImageView thumbView;
    private View flash;
    private MediaActionSound sound;

    // analiz durumu (yalnızca analiz iş parçacığında)
    private volatile float[] prevGrid, lastShotGrid;
    private volatile boolean needMotion;    // elle çekim ilk analiz karesinden önce yapıldıysa: aynı kağıdı tekrar çekme
    private volatile boolean discardNext;   // çekim işlenirken "Geri al": o çekimi at
    private long steadySince;
    private boolean sawMotion;
    private int blurRetries;

    private volatile boolean autoOn;
    private volatile boolean capturing;
    private volatile boolean finishing;
    private volatile int shots;   // bu oturumda gönderilmek üzere alınan sayfa sayısı (geri alınanlar düşülür)
    private volatile int sent, failedStreak;
    private volatile boolean unpaired;     // bilgisayar bu telefonu tanımıyor (eşleştirme gerekli)
    private volatile long failingSince;
    private final List<Double> sharpness = new ArrayList<>();

    // gönderim kuyruğu
    private final Object lock = new Object();
    private final ArrayDeque<File> pending = new ArrayDeque<>();
    private boolean uploading;
    private int undosAfterUpload;          // gönderilmekte olan fotoğraf için bekleyen geri almalar

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().setStatusBarColor(Color.BLACK);
        prefs = getSharedPreferences("ayarlar", MODE_PRIVATE);
        baseUrl = getIntent().getStringExtra(EXTRA_URL);
        examId = getIntent().getStringExtra(EXTRA_EXAM);
        pagesPer = Math.max(1, getIntent().getIntExtra(EXTRA_PAGES, 1));
        sheetsPer = Math.max(1, getIntent().getIntExtra(EXTRA_SHEETS, 1));
        authKey = getIntent().getStringExtra(EXTRA_KEY);
        if (savedInstanceState != null && savedInstanceState.getString("oturum") != null) {
            // aynı oturum sürsün: sunucu sayfaları doğru öğrenciye bağlamaya devam etsin
            session = savedInstanceState.getString("oturum");
            shots = savedInstanceState.getInt("cekim");
            sent = savedInstanceState.getInt("gonderilen");
        } else {
            session = "app-" + UUID.randomUUID().toString().substring(0, 12);
            cleanOldShots();
        }
        autoOn = prefs.getBoolean("otomatik_cekim", true);
        sound = new MediaActionSound();
        sound.load(MediaActionSound.SHUTTER_CLICK);
        buildUi(getIntent().getStringExtra(EXTRA_TITLE));
        uploader.setDaemon(true);
        uploader.start();

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startCamera();
        } else {
            requestPermissions(new String[]{Manifest.permission.CAMERA}, REQ_CAMERA);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions, @NonNull int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode != REQ_CAMERA) return;
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) startCamera();
        else {
            Toast.makeText(this, "Kağıtları çekebilmek için kamera izni gerekiyor.", Toast.LENGTH_LONG).show();
            finish();
        }
    }

    // ------------------------------------------------------------------ arayüz

    private int dp(float v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private GradientDrawable round(int fill, float radiusDp, int stroke, float strokeDp) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(fill);
        d.setCornerRadius(dp(radiusDp));
        if (stroke != 0) d.setStroke(dp(strokeDp), stroke);
        return d;
    }

    private TextView text(float sp, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setTextSize(sp);
        t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        return t;
    }

    private TextView pillButton(String label) {
        TextView b = text(14, Color.WHITE, true);
        b.setText(label);
        b.setGravity(Gravity.CENTER);
        b.setPadding(dp(16), 0, dp(16), 0);
        b.setBackground(round(0x66000000, 22, 0x55FFFFFF, 1));
        b.setMinWidth(dp(92));
        return b;
    }

    private void buildUi(String title) {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);

        preview = new PreviewView(this);
        preview.setScaleType(PreviewView.ScaleType.FIT_CENTER);
        root.addView(preview, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        preview.setOnTouchListener((v, e) -> {
            if (e.getAction() == MotionEvent.ACTION_UP && camera != null) {
                MeteringPoint p = preview.getMeteringPointFactory().createPoint(e.getX(), e.getY());
                camera.getCameraControl().startFocusAndMetering(new FocusMeteringAction.Builder(p).build());
            }
            return true;
        });

        flash = new View(this);
        flash.setBackgroundColor(Color.WHITE);
        flash.setAlpha(0f);
        root.addView(flash, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        // üst bilgi
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.VERTICAL);
        top.setPadding(dp(16), dp(14), dp(16), dp(12));
        top.setBackgroundColor(0x99000000);
        titleView = text(16, Color.WHITE, true);
        titleView.setText(title == null ? "Seri çekim" : title);
        top.addView(titleView);
        statusView = text(13, 0xCCFFFFFF, false);
        top.addView(statusView);
        nextView = text(13, 0xFFFFFFFF, false);
        top.addView(nextView);
        root.addView(top, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP));

        thumbView = new ImageView(this);
        thumbView.setScaleType(ImageView.ScaleType.CENTER_CROP);
        thumbView.setBackground(round(0x33000000, 4, Color.WHITE, 1.5f));
        thumbView.setVisibility(View.GONE);
        FrameLayout.LayoutParams tl = new FrameLayout.LayoutParams(dp(54), dp(72), Gravity.TOP | Gravity.END);
        tl.topMargin = dp(84);
        tl.rightMargin = dp(12);
        root.addView(thumbView, tl);

        // alt kısım: ipucu + otomatik + düğmeler
        LinearLayout bottom = new LinearLayout(this);
        bottom.setOrientation(LinearLayout.VERTICAL);
        bottom.setGravity(Gravity.CENTER_HORIZONTAL);
        bottom.setPadding(dp(16), dp(10), dp(16), dp(22));

        hintView = text(14, Color.WHITE, true);
        hintView.setGravity(Gravity.CENTER);
        hintView.setPadding(dp(14), dp(7), dp(14), dp(7));
        hintView.setBackground(round(0xAA000000, 16, 0, 0));
        bottom.addView(hintView, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        autoView = text(13, Color.WHITE, false);
        autoView.setPadding(dp(12), dp(6), dp(12), dp(6));
        autoView.setOnClickListener(v -> setAuto(!autoOn));
        LinearLayout.LayoutParams al = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        al.topMargin = dp(10);
        bottom.addView(autoView, al);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        TextView undo = pillButton("Geri al");
        undo.setOnClickListener(v -> undoLast());
        View shutter = new View(this);
        shutter.setBackground(round(Color.WHITE, 40, 0x55FFFFFF, 6));
        shutter.setOnClickListener(v -> manualShot());
        shutter.setContentDescription("Fotoğraf çek");
        TextView done = pillButton("Bitti");
        done.setOnClickListener(v -> finishSession());
        row.addView(undo, new LinearLayout.LayoutParams(0, dp(44), 1));
        LinearLayout.LayoutParams sl = new LinearLayout.LayoutParams(dp(76), dp(76));
        sl.leftMargin = dp(24);
        sl.rightMargin = dp(24);
        row.addView(shutter, sl);
        row.addView(done, new LinearLayout.LayoutParams(0, dp(44), 1));
        LinearLayout.LayoutParams rl = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rl.topMargin = dp(14);
        bottom.addView(row, rl);
        bottom.setBackgroundColor(0x88000000);
        root.addView(bottom, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM));

        setContentView(root);
        setAuto(autoOn);
        refreshCounters();
        hint("Kağıdı kameranın önüne düz koyun");
    }

    private void setAuto(boolean on) {
        autoOn = on;
        prefs.edit().putBoolean("otomatik_cekim", on).apply();
        autoView.setText(on ? "● Otomatik çekim açık" : "○ Otomatik çekim kapalı — deklanşöre ya da ses tuşuna basın");
        autoView.setBackground(round(on ? PRIMARY : 0x66000000, 14, 0, 0));
        steadySince = 0;
    }

    private void hint(String s) {
        ui.post(() -> {
            if (!s.contentEquals(hintView.getText())) hintView.setText(s);
        });
    }

    private void refreshCounters() {
        ui.post(() -> {
            int queued;
            synchronized (lock) {
                queued = pending.size() + (uploading ? 1 : 0);
            }
            String st = "Gönderilen " + sent + (queued > 0 ? " · gönderiliyor " + queued : "");
            if (failedStreak > 0) st += " · bağlantı bekleniyor";
            statusView.setText(st);
            int pagesSoFar = shots * sheetsPer;
            int student = pagesSoFar / pagesPer + 1;
            int page = pagesSoFar % pagesPer + 1;
            if (sheetsPer > 1) {
                nextView.setText("Sıradaki fotoğraf: " + sheetsPer + " kağıt · " + student + ". öğrenciden başlar"
                        + (pagesPer > 1 && page > 1 ? " (sayfa " + page + "/" + pagesPer + ")" : ""));
            } else {
                nextView.setText(pagesPer > 1
                        ? "Sıradaki: " + student + ". öğrenci · sayfa " + page + "/" + pagesPer
                        : "Sıradaki: " + student + ". kağıt");
            }
        });
    }

    // ------------------------------------------------------------------ kamera

    private void startCamera() {
        ListenableFuture<ProcessCameraProvider> future = ProcessCameraProvider.getInstance(this);
        future.addListener(() -> {
            try {
                ProcessCameraProvider provider = future.get();
                ResolutionSelector ratio43 = new ResolutionSelector.Builder()
                        .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY).build();
                Preview pv = new Preview.Builder().setResolutionSelector(ratio43).build();
                pv.setSurfaceProvider(preview.getSurfaceProvider());
                // ~12 MP yeter (okuma için 2400 px'e küçültülür); 50 MP kameralarda bellek taşmasın
                capture = new ImageCapture.Builder()
                        .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
                        .setResolutionSelector(new ResolutionSelector.Builder()
                                .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
                                .setResolutionStrategy(new ResolutionStrategy(new Size(4000, 3000),
                                        ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER))
                                .build())
                        .build();
                ImageAnalysis analysis = new ImageAnalysis.Builder()
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .setResolutionSelector(new ResolutionSelector.Builder()
                                .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
                                .setResolutionStrategy(new ResolutionStrategy(new Size(640, 480),
                                        ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER))
                                .build())
                        .build();
                analysis.setAnalyzer(analysisExec, this::analyze);
                provider.unbindAll();
                camera = provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, pv, capture, analysis);
            } catch (Exception e) {
                hint("Kamera açılamadı: " + e.getMessage());
            }
        }, ContextCompat.getMainExecutor(this));
    }

    /** Her önizleme karesinde: hareket var mı, görüntü sabit mi, yeni bir kağıt mı? */
    private void analyze(@NonNull ImageProxy img) {
        try {
            ImageProxy.PlaneProxy plane = img.getPlanes()[0];
            ByteBuffer buf = plane.getBuffer();
            int rs = plane.getRowStride(), ps = plane.getPixelStride();
            int w = img.getWidth(), h = img.getHeight();
            float[] grid = new float[GW * GH];
            float total = 0;
            for (int gy = 0; gy < GH; gy++) {
                for (int gx = 0; gx < GW; gx++) {
                    int sum = 0;
                    for (int sy = 0; sy < 3; sy++) {
                        int y = (gy * h + (sy * 2 + 1) * h / 6) / GH;
                        for (int sx = 0; sx < 3; sx++) {
                            int x = (gx * w + (sx * 2 + 1) * w / 6) / GW;
                            sum += buf.get(y * rs + x * ps) & 0xFF;
                        }
                    }
                    float v = sum / 9f;
                    grid[gy * GW + gx] = v;
                    total += v;
                }
            }
            float brightness = total / grid.length;
            float motion = prevGrid == null ? 99f : diff(grid, prevGrid);
            prevGrid = grid;
            if (!autoOn || capturing || finishing) return;

            long now = SystemClock.uptimeMillis();
            if (motion > MOVE_T) {
                sawMotion = true;
                steadySince = 0;
                hint(lastShotGrid == null ? "Kağıdı yerleştirin" : "Kağıt değişiyor…");
                return;
            }
            if (motion > STEADY_T) {
                steadySince = 0;
                hint("Sabit tutun…");
                return;
            }
            float[] last = lastShotGrid;
            boolean newPage = last == null ? (!needMotion || sawMotion) : (sawMotion && diff(grid, last) > CHANGE_T);
            if (!newPage) {
                hint(nextHint());
                return;
            }
            if (brightness < 35) {
                hint("Işık yetersiz");
                return;
            }
            if (steadySince == 0) steadySince = now;
            if (now - steadySince < STEADY_MS) {
                hint("Sabit tutun…");
                return;
            }
            steadySince = 0;
            shoot(grid, true);
        } catch (Exception ignored) {
        } finally {
            img.close();
        }
    }

    private static float diff(float[] a, float[] b) {
        float s = 0;
        for (int i = 0; i < a.length; i++) s += Math.abs(a[i] - b[i]);
        return s / a.length;
    }

    private void manualShot() {
        if (capturing || capture == null) return;
        shoot(prevGrid, false);
    }

    private void shoot(float[] grid, boolean auto) {
        if (capturing || capture == null) return;
        capturing = true;
        hint("Çekiliyor…");
        ui.post(() -> {
            sound.play(MediaActionSound.SHUTTER_CLICK);
            flash.setAlpha(0.7f);
            flash.animate().alpha(0f).setDuration(250).start();
            preview.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
        });
        capture.takePicture(ioExec, new ImageCapture.OnImageCapturedCallback() {
            @Override
            public void onCaptureSuccess(@NonNull ImageProxy image) {
                byte[] jpeg;
                int rotation;
                try {
                    ByteBuffer b = image.getPlanes()[0].getBuffer();
                    jpeg = new byte[b.remaining()];
                    b.get(jpeg);
                    rotation = image.getImageInfo().getRotationDegrees();
                } finally {
                    image.close();
                }
                onShot(jpeg, rotation, grid, auto);
            }

            @Override
            public void onError(@NonNull ImageCaptureException e) {
                discardNext = false;  // iptal edilecek bir çekim kalmadı; sonraki iyi çekim silinmesin
                capturing = false;
                hint("Çekilemedi, tekrar deneyin");
            }
        });
    }

    private String nextHint() {
        if (sheetsPer > 1) return "Sıradaki " + sheetsPer + " kağıdı yan yana koyun";
        return pagesPer > 1 && shots % pagesPer != 0 ? "Aynı öğrencinin sonraki sayfasını koyun" : "Sıradaki kağıdı koyun";
    }

    /** Fotoğrafı döndür, küçült, netliğini ölç; uygunsa gönderim kuyruğuna ekle. */
    private void onShot(byte[] jpeg, int rotation, float[] grid, boolean auto) {
        if (discardNext) {
            discardNext = false;
            capturing = false;
            hint("Çekim iptal edildi");
            return;
        }
        try {
            BitmapFactory.Options o = new BitmapFactory.Options();
            o.inJustDecodeBounds = true;
            BitmapFactory.decodeByteArray(jpeg, 0, jpeg.length, o);
            int sample = 1;
            int maxSide = sheetsPer > 1 ? MAX_SIDE_MULTI : MAX_SIDE;
            while (Math.max(o.outWidth, o.outHeight) / (sample * 2) >= maxSide) sample *= 2;
            o = new BitmapFactory.Options();
            o.inSampleSize = sample;
            Bitmap bmp = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.length, o);
            Matrix m = new Matrix();
            if (rotation != 0) m.postRotate(rotation);
            float scale = Math.min(1f, (float) maxSide / Math.max(bmp.getWidth(), bmp.getHeight()));
            if (scale < 1f) m.postScale(scale, scale);
            if (!m.isIdentity()) {
                Bitmap t = Bitmap.createBitmap(bmp, 0, 0, bmp.getWidth(), bmp.getHeight(), m, true);
                if (t != bmp) bmp.recycle();
                bmp = t;
            }

            double sharp = sharpness(bmp);
            double median = median(sharpness);
            if (auto && sharpness.size() >= 3 && sharp < 0.45 * median && blurRetries < 2) {
                blurRetries++;
                bmp.recycle();
                hint("Bulanık çıktı, yeniden çekiliyor…");
                capturing = false;  // lastShotGrid değişmedi: sahne hâlâ "yeni kağıt", sabitlenince tekrar çekilir
                return;
            }
            blurRetries = 0;
            sharpness.add(sharp);
            if (sharpness.size() > 15) sharpness.remove(0);

            File dir = new File(getCacheDir(), "seri");
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
            File f = new File(dir, System.currentTimeMillis() + ".jpg");
            try (FileOutputStream out = new FileOutputStream(f)) {
                bmp.compress(Bitmap.CompressFormat.JPEG, 90, out);
            }
            Bitmap thumb = Bitmap.createScaledBitmap(bmp, Math.max(1, bmp.getWidth() / 10), Math.max(1, bmp.getHeight() / 10), true);
            bmp.recycle();

            synchronized (lock) {
                if (discardNext) {  // işlenirken geri alındı
                    discardNext = false;
                    //noinspection ResultOfMethodCallIgnored
                    f.delete();
                    hint("Çekim iptal edildi");
                    return;
                }
                pending.addLast(f);
                lock.notifyAll();
            }
            shots++;
            lastShotGrid = grid != null ? grid : prevGrid;
            needMotion = lastShotGrid == null;  // karşılaştıracak kare yoksa, otomatik çekim önce hareket beklesin
            sawMotion = false;
            ui.post(() -> {
                thumbView.setImageBitmap(thumb);
                thumbView.setVisibility(View.VISIBLE);
            });
            if (!auto && sharpness.size() >= 4 && sharp < 0.45 * median) hint("Bulanık olabilir. Gerekirse 'Geri al' deyip yeniden çekin.");
            else hint(nextHint());
            refreshCounters();
        } catch (Throwable e) {  // OutOfMemoryError dahil: uygulama kapanmasın
            hint("Fotoğraf işlenemedi, tekrar çekin" + (e instanceof OutOfMemoryError ? " (bellek yetmedi)" : ""));
        } finally {
            capturing = false;
        }
    }

    /** Netlik ölçüsü: küçültülmüş gri görüntüde Laplace değişkenliği. */
    private static double sharpness(Bitmap src) {
        int tw = 480;
        int th = Math.max(1, src.getHeight() * tw / src.getWidth());
        Bitmap s = Bitmap.createScaledBitmap(src, tw, th, true);
        int[] px = new int[tw * th];
        s.getPixels(px, 0, tw, 0, 0, tw, th);
        s.recycle();
        int[] g = new int[px.length];
        for (int i = 0; i < px.length; i++) {
            int c = px[i];
            g[i] = (((c >> 16) & 0xFF) * 3 + ((c >> 8) & 0xFF) * 6 + (c & 0xFF)) / 10;
        }
        double sum = 0, sq = 0;
        int n = 0;
        for (int y = 1; y < th - 1; y++) {
            for (int x = 1; x < tw - 1; x++) {
                int i = y * tw + x;
                int lap = 4 * g[i] - g[i - 1] - g[i + 1] - g[i - tw] - g[i + tw];
                sum += lap;
                sq += (double) lap * lap;
                n++;
            }
        }
        double mean = sum / n;
        return sq / n - mean * mean;
    }

    private static double median(List<Double> values) {
        if (values.isEmpty()) return 0;
        List<Double> c = new ArrayList<>(values);
        Collections.sort(c);
        return c.get(c.size() / 2);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_VOLUME_DOWN || keyCode == KeyEvent.KEYCODE_VOLUME_UP) {
            if (event.getRepeatCount() == 0) manualShot();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    // ------------------------------------------------------------------ gönderim

    /** Sunucunun verdiği HTTP hatası. */
    private static class HttpStatus extends Exception {
        final int code;

        HttpStatus(int code, String msg) {
            super(msg);
            this.code = code;
        }

        boolean permanent() {
            return code >= 400 && code < 500 && code != 401 && code != 408 && code != 429;
        }
    }

    private void uploadLoop() {
        while (!Thread.currentThread().isInterrupted()) {
            File f;
            synchronized (lock) {
                while (pending.isEmpty()) {
                    try {
                        lock.wait();
                    } catch (InterruptedException e) {
                        return;
                    }
                }
                f = pending.peekFirst();
                uploading = true;
            }
            boolean ok = false, drop = false;
            try {
                JSONObject r = postPhoto(f);
                ok = true;
                failedStreak = 0;
                failingSince = 0;
                unpaired = false;
                sent++;
                JSONObject o = r.optJSONObject("oturum");
                if (r.optInt("atlanan") > 0) toastUi("Bu fotoğraf zaten gönderilmişti, atlandı.");
                if (o != null && o.has("acik_sayfa") && o.optInt("acik_sayfa") == 0 && pagesPer > 1 && o.optInt("ogrenci") > 0) {
                    hintIfIdle(o.optInt("ogrenci") + ". öğrenci tamamlandı, okumaya gönderildi");
                }
            } catch (HttpStatus e) {
                if (e.code == 401) {
                    unpaired = true;
                    hint("Telefon bu bilgisayarla eşleşmemiş. Çıkıp ana ekranda kodu girin.");
                } else if (e.permanent()) {
                    drop = true;  // ör. sınav silinmiş: sonsuza kadar denemenin anlamı yok
                    toastUi("Fotoğraf gönderilemedi: " + e.getMessage());
                }
                noteFailure();
            } catch (Exception e) {
                noteFailure();
            }
            int undos;
            synchronized (lock) {
                if (ok || drop || undosAfterUpload > 0) {
                    // gönderildi / kalıcı hata / gönderilemedi ama bu arada geri alındı → kuyruktan çıkar
                    pending.remove(f);
                    //noinspection ResultOfMethodCallIgnored
                    f.delete();
                }
                uploading = false;
                undos = undosAfterUpload;
                undosAfterUpload = 0;
                lock.notifyAll();
            }
            if (drop) shots = Math.max(0, shots - 1);
            if (undos > 0) {
                // gönderilemediyse ilk geri alma bu fotoğrafla karşılandı (kalıcı hatada sayaç zaten düştü)
                int serverSide = ok ? undos : undos - 1;
                if (!ok && !drop) applyLocalUndo();
                for (int i = 0; i < serverSide; i++) serverUndo();
            }
            refreshCounters();
            if (!ok && !drop) SystemClock.sleep(Math.min(10000, 1500L * Math.max(1, failedStreak)));
        }
    }

    private void noteFailure() {
        failedStreak++;
        if (failingSince == 0) failingSince = SystemClock.uptimeMillis();
    }

    private void hintIfIdle(String s) {
        if (!capturing) hint(s);
    }

    private void toastUi(String s) {
        ui.post(() -> Toast.makeText(this, s, Toast.LENGTH_SHORT).show());
    }

    private void addAuth(HttpURLConnection c) {
        if (authKey != null && !authKey.isEmpty()) c.setRequestProperty("X-Sinav-Anahtar", authKey);
        String cookie = android.webkit.CookieManager.getInstance().getCookie(baseUrl);
        if (cookie != null) c.setRequestProperty("Cookie", cookie);
    }

    private JSONObject postPhoto(File f) throws Exception {
        String boundary = "----sinav" + System.nanoTime();
        HttpURLConnection c = (HttpURLConnection) new URL(baseUrl + "/api/sinavlar/" + examId + "/kagit").openConnection();
        c.setConnectTimeout(5000);
        c.setReadTimeout(60000);
        c.setDoOutput(true);
        c.setRequestMethod("POST");
        c.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        addAuth(c);
        try (DataOutputStream out = new DataOutputStream(c.getOutputStream())) {
            out.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"oturum\"\r\n\r\n" + session + "\r\n")
                    .getBytes(StandardCharsets.UTF_8));
            out.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"dosyalar\"; filename=\"" + f.getName()
                    + "\"\r\nContent-Type: image/jpeg\r\n\r\n").getBytes(StandardCharsets.UTF_8));
            out.write(Files.readAllBytes(f.toPath()));
            out.write(("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
        }
        return readJson(c);
    }

    private JSONObject post(String path) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(baseUrl + path).openConnection();
        c.setConnectTimeout(5000);
        c.setReadTimeout(15000);
        c.setRequestMethod("POST");
        c.setDoOutput(true);
        addAuth(c);
        c.getOutputStream().close();
        return readJson(c);
    }

    private static JSONObject readJson(HttpURLConnection c) throws Exception {
        int code = c.getResponseCode();
        InputStream in = code < 400 ? c.getInputStream() : c.getErrorStream();
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        if (in != null) {
            byte[] buf = new byte[4096];
            int n;
            while ((n = in.read(buf)) > 0) b.write(buf, 0, n);
            in.close();
        }
        c.disconnect();
        JSONObject o;
        try {
            o = b.size() > 0 ? new JSONObject(b.toString("UTF-8")) : new JSONObject();
        } catch (Exception e) {
            o = new JSONObject();
        }
        if (code >= 400) throw new HttpStatus(code, o.optString("detail", "Hata " + code));
        return o;
    }

    /** Son çekimi geri al: işleniyorsa iptal et, gönderilmediyse yerelde sil, gönderildiyse bilgisayardan geri al. */
    private void undoLast() {
        boolean server;
        synchronized (lock) {
            if (capturing) {
                discardNext = true;  // deklanşöre basıldı ama fotoğraf henüz işleniyor: o çekimi at, öncekine dokunma
                hint("Çekim iptal ediliyor…");
                return;
            }
            int notInFlight = pending.size() - (uploading ? 1 : 0);
            if (notInFlight > 0) {
                File last = pending.pollLast();
                if (last != null) //noinspection ResultOfMethodCallIgnored
                    last.delete();
                server = false;
            } else if (uploading) {
                undosAfterUpload++;  // gönderilmekte olan fotoğraf: gönderim bitince geri alınır
                hint("Geri alınıyor…");
                return;
            } else {
                server = true;
            }
        }
        if (!server) {
            applyLocalUndo();
            return;
        }
        if (sent == 0) {
            toastUi("Geri alınacak çekim yok");
            return;
        }
        hint("Geri alınıyor…");
        ioExec.execute(this::serverUndo);
    }

    private void applyLocalUndo() {
        shots = Math.max(0, shots - 1);
        lastShotGrid = null;  // aynı kağıt yeniden çekilebilsin
        needMotion = false;
        ui.post(() -> thumbView.setVisibility(View.GONE));
        hint("Son çekim geri alındı");
        refreshCounters();
    }

    /** Bilgisayardan geri al; yerel sayaçlar yalnızca bilgisayar onaylarsa değişir. */
    private void serverUndo() {
        try {
            post("/api/sinavlar/" + examId + "/oturum/" + session + "/geri-al");
            sent = Math.max(0, sent - 1);
            applyLocalUndo();
        } catch (HttpStatus e) {
            toastUi("Geri alınamadı: " + e.getMessage());
            hint("Geri alınamadı");
        } catch (Exception e) {
            toastUi("Geri alınamadı: bilgisayara ulaşılamıyor");
        }
        refreshCounters();
    }

    // ------------------------------------------------------------------ bitirme

    private void finishSession() {
        if (finishing) {
            askDiscard();  // ikinci kez basıldı: beklemek istemiyor olabilir
            return;
        }
        finishing = true;
        hint("Kalan fotoğraflar gönderiliyor…");
        waitAndFinish();
    }

    private int leftToSend() {
        synchronized (lock) {
            return pending.size() + (uploading ? 1 : 0);
        }
    }

    private boolean askedDiscard;

    private void waitAndFinish() {
        if (isFinishing()) return;
        int left = leftToSend();
        if (left > 0 || capturing) {
            hint((capturing ? "Son çekim işleniyor…" : left + " fotoğraf gönderiliyor…")
                    + (unpaired ? " (telefon eşleşmemiş)" : failedStreak > 0 ? " (bilgisayara ulaşılamıyor)" : ""));
            // 20 saniyedir gönderilemiyorsa ne yapılacağını sor
            if (!askedDiscard && failingSince > 0 && SystemClock.uptimeMillis() - failingSince > 20000) askDiscard();
            ui.postDelayed(this::waitAndFinish, 500);
            return;
        }
        ioExec.execute(() -> {
            try {
                post("/api/sinavlar/" + examId + "/oturum/" + session + "/bitir");
            } catch (Exception ignored) {
            }
            ui.post(this::finish);
        });
    }

    private void askDiscard() {
        int left = leftToSend();
        if (left == 0 || askedDiscard) return;
        askedDiscard = true;
        new android.app.AlertDialog.Builder(this)
                .setTitle("Fotoğraflar gönderilemiyor")
                .setMessage(left + " fotoğraf henüz bilgisayara gönderilemedi. Bilgisayar açık ve aynı Wi-Fi'de mi?\n\n"
                        + "Çıkarsanız bu fotoğraflar silinir; kağıtları sonra yeniden çekebilirsiniz.")
                .setPositiveButton("Beklemeye devam", (d, w) -> askedDiscard = false)
                .setNegativeButton("Sil ve çık", (d, w) -> {
                    synchronized (lock) {
                        for (File f : pending) //noinspection ResultOfMethodCallIgnored
                            f.delete();
                        pending.clear();
                    }
                    finish();
                })
                .setCancelable(false)
                .show();
    }

    private void cleanOldShots() {
        File[] old = new File(getCacheDir(), "seri").listFiles();
        if (old != null) for (File f : old) //noinspection ResultOfMethodCallIgnored
            f.delete();
    }

    @Override
    protected void onSaveInstanceState(@NonNull Bundle out) {
        super.onSaveInstanceState(out);
        out.putString("oturum", session);
        out.putInt("cekim", shots);
        out.putInt("gonderilen", sent);
    }

    @Override
    public void onBackPressed() {
        finishSession();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        analysisExec.shutdown();
        if (isFinishing()) {
            uploader.interrupt();  // iş parçacıkları ve ekran sızmasın
            ioExec.shutdown();
        }
        sound.release();
    }
}
