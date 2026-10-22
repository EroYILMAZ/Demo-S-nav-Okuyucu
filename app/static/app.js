"use strict";

const $view = document.getElementById("view");
const $status = document.getElementById("status");
const $modal = document.getElementById("modal");
const $modalBox = $modal.querySelector(".modal-box");
const $modalBody = document.getElementById("modal-body");
const $toast = document.getElementById("toast");

const state = { exam: null, pollTimer: null, durum: null, filter: "hepsi", sort: "eklenme", renderedKey: "", reviewId: null };
const isLocalHost = ["localhost", "127.0.0.1"].includes(location.hostname);
const isNarrow = () => matchMedia("(max-width: 720px)").matches;

// ------------------------------------------------------------ simgeler

const ICONS = {
  template: '<rect x="3" y="3" width="18" height="18" rx="2"/><rect x="6" y="6" width="5" height="4" rx=".5"/><rect x="13" y="12" width="5" height="4" rx=".5"/><path d="M6 14h4M13 8h5"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6M16 4.5a3.5 3.5 0 0 1 0 7M18 14c2 .6 3 3 3 6"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  pageplus: '<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4M12 11v6M9 14h6"/>',
  left: '<path d="M15 5l-7 7 7 7"/>',
  right: '<path d="M9 5l7 7-7 7"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  sliders: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  phone: '<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18h2"/>',
  file: '<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
  swap: '<path d="M4 8h13l-3-3M20 16H7l3 3"/>',
};
const ic = (name) => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
const dots = '<svg class="i fill" viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>';
document.getElementById("settings-btn").innerHTML = ic("sliders");

// ------------------------------------------------------------ yardımcılar

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmt(n) {
  if (n === null || n === undefined || n === "") return "";
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 100) / 100).replace(".", ",");
}
function parseScore(s) {
  s = String(s).trim().replace(",", ".");
  if (s === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : NaN;
}
function fmtDate(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleDateString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric" });
}
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    if (res.status === 401 && msg === "eslestirme") {
      showPairing();
      const e = new Error("Bu cihaz henüz bilgisayarla eşleşmedi");
      e.pairing = true;  // çağıran, eşleştirme ekranının üzerine hata yazmasın
      throw e;
    }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res;
}
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const totalOf = (r) => (r.puanlar || []).reduce((a, p) => a + (p || 0), 0);
const maxTotal = () => state.exam.tam_puanlar.reduce((a, b) => a + b, 0);
const pagesPer = () => Math.max(1, state.exam?.sayfa_sayisi || 1);
const sheetsPer = () => Math.max(1, state.exam?.fotodaki_kagit || 1);  // bir fotoğrafta kaç kağıt var
const hasWarn = (r) => r.durum === "tamam" && !r.onaylandi && (r.uyarilar.length > 0 || Object.keys(r.hucre_uyarilari).length > 0);
const isQueued = (r) => r.durum === "bekliyor" || r.durum === "isleniyor";
const isBusy = (r) => isQueued(r) || r.durum === "eksik";
// tarayıcıda tek tek çekilen fotoğrafların sayfa sayısına göre birleştirilmesi için sekme başına oturum
const browserSession = (() => {
  try {
    let s = sessionStorage.getItem("oturum");
    if (!s) { s = "web-" + Math.random().toString(36).slice(2, 12); sessionStorage.setItem("oturum", s); }
    return s;
  } catch { return "web-" + Math.random().toString(36).slice(2, 12); }
})();

let toastTimer;
function toast(msg, opts = {}) {
  $toast.innerHTML = `<span>${esc(msg)}</span>${opts.action ? `<button>${esc(opts.action)}</button>` : ""}`;
  $toast.className = "toast" + (opts.err ? " err" : "");
  $toast.hidden = false;
  if (opts.action) $toast.querySelector("button").onclick = () => { $toast.hidden = true; opts.onAction(); };
  clearTimeout(toastTimer);
  if (opts.ms !== 0) toastTimer = setTimeout(() => ($toast.hidden = true), opts.ms || (opts.action ? 6000 : 3000));
}
const toastErr = (err) => toast(err.message || String(err), { err: true, ms: 5000 });

// ------------------------------------------------------------ pencereler

function openModal(html, { wide = false } = {}) {
  $modalBody.innerHTML = html;
  $modalBox.classList.toggle("wide", wide);
  $modal.hidden = false;
}
function closeModal() {
  $modal.hidden = true;
  $modalBody.innerHTML = "";
  state.reviewId = null;
}
$modal.addEventListener("click", (e) => {
  if (e.target === $modal || e.target.closest("[data-close]")) closeModal();
});
document.addEventListener("keydown", (e) => {
  if ($modal.hidden) return;
  if (e.key === "Escape") closeModal();
  if (state.reviewId && e.altKey && (e.key === "ArrowRight" || e.key === "ArrowLeft")) stepReview(e.key === "ArrowRight" ? 1 : -1);
});
const head = (title) => `<div class="m-head"><h2>${title}</h2><button class="icon-btn" data-close aria-label="Kapat">${ic("x")}</button></div>`;

/** Tarayıcının confirm() penceresi yerine. Seçilen düğmenin değeriyle çözülür (vazgeçilirse null). */
function ask(title, text, buttons) {
  return new Promise((resolve) => {
    openModal(`${head(esc(title))}<div class="m-body"><p style="margin:0">${esc(text)}</p></div>
      <div class="m-foot"><button class="btn" data-close>Vazgeç</button>${buttons
        .map((b, i) => `<button class="btn ${b.cls || "primary"}" data-i="${i}">${esc(b.label)}</button>`).join("")}</div>`);
    const done = (v) => { obs.disconnect(); resolve(v); };
    const obs = new MutationObserver(() => { if ($modal.hidden) done(null); });
    obs.observe($modal, { attributes: true, attributeFilter: ["hidden"] });
    $modalBody.querySelectorAll("[data-i]").forEach((b) => (b.onclick = () => { const v = buttons[+b.dataset.i].value; obs.disconnect(); closeModal(); resolve(v); }));
  });
}

// ------------------------------------------------------------ durum

async function refreshStatus() {
  const set = (cls, text) => { $status.className = "status " + cls; $status.querySelector("span").textContent = text; };
  try {
    const d = await api("/api/durum");
    if (d.eslesme_gerekli) { set("bad", "Eşleştirme gerekli"); showPairing(); return; }
    state.durum = d;
    if (d.ollama_bulunamadi) set("bad", "Okuma motoru bulunamadı");
    else if (!d.ollama.calisiyor) set("bad", d.kuyruk ? "Okuma motoru bekleniyor" : "Okuma motoru kapalı");
    else if (!d.ollama.model_yuklu) set("bad", "Model yüklü değil");
    else if (d.kuyruk > 0) set("busy", `${d.kuyruk} kağıt okunuyor`);
    else set("ok", "Hazır");
  } catch {
    set("bad", "Bilgisayara ulaşılamıyor");
  }
}
setInterval(refreshStatus, 4000);

// ------------------------------------------------------------ eşleştirme (ağdaki telefonlar için)

function showPairing() {
  if (document.getElementById("pair-form")) return;  // zaten gösteriliyor
  clearTimeout(state.pollTimer);
  state.exam = null;
  closeModal();
  document.querySelector(".capture-bar")?.remove();
  $view.innerHTML = `<div class="panel panel-pad pair">
    <h1>Bu cihazı bağlayın</h1>
    <p class="muted">Öğrenci fotoğrafları ve puanlar korunsun diye, ağdaki her cihaz bilgisayara bir kez bağlanır.
    Bilgisayardaki Sınav Okuyucu'nun ana sayfasında yazan <b>6 haneli kodu</b> girin.</p>
    <form id="pair-form" class="actions">
      <input name="kod" inputmode="numeric" autocomplete="one-time-code" maxlength="7" placeholder="123456" aria-label="Eşleştirme kodu">
      <button class="btn primary">Bağlan</button>
    </form>
    ${window.SinavOkuyucuApp ? `<button class="btn ghost" id="pair-other" style="margin-top:8px">${ic("swap")}Başka bilgisayara bağlan</button>` : ""}
  </div>`;
  const f = document.getElementById("pair-form");
  f.kod.focus();
  document.getElementById("pair-other")?.addEventListener("click", () => window.SinavOkuyucuApp.baglantiyiDegistir());
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await fetch("/api/eslestir", json("POST", { kod: f.kod.value, ad: navigator.userAgent.slice(0, 60) }));
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || "Bağlanılamadı");
      try { window.SinavOkuyucuApp?.anahtarKaydet?.(body.anahtar); } catch {}
      location.hash = "#/";
      location.reload();
    } catch (err) { toastErr(err); f.kod.select(); }
  });
}

// ------------------------------------------------------------ yönlendirme

function route() {
  clearTimeout(state.pollTimer);
  state.exam = null;
  closeModal();
  document.querySelector(".capture-bar")?.remove();
  const m = location.hash.match(/^#\/sinav\/(\w+)/);
  if (m) showExam(m[1]);
  else showHome();
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------ ana sayfa

async function showHome() {
  let exams;
  try {
    await refreshStatus();
    exams = await api("/api/sinavlar");
  } catch (err) {
    if (err.pairing || document.getElementById("pair-form")) return;  // eşleştirme ekranı açık kalsın
    $view.innerHTML = `<div class="empty">Bilgisayardaki programa ulaşılamıyor.<br><button class="btn" style="margin-top:12px" onclick="route()">Tekrar dene</button></div>`;
    return;
  }
  const d = state.durum || { adresler: [], ollama: {}, ayarlar: {} };
  const engineWarn = d.ollama_bulunamadi
    ? "Okuma motoru (Ollama) bu bilgisayarda bulunamadı. Programı paketteki KUR.bat ile kurun ya da kurulum.bat dosyasını çalıştırın."
    : !d.ollama.calisiyor
    ? (d.motor_bekleniyor ? `Okuma motoru bekleniyor: ${d.motor_bekleniyor}. Kağıtlar sırada; motor açılınca kendiliğinden okunacak.`
      : "Okuma motoru (Ollama) çalışmıyor. Bilgisayardaki Sınav Okuyucu programını kapatıp yeniden açın.")
    : !d.ollama.model_yuklu ? `“${d.ayarlar.model}” modeli yüklü değil. Bilgisayarda kurulum.bat dosyasını çalıştırın.` : "";

  const side = isLocalHost && d.adresler.length ? `
    <aside class="panel panel-pad side">
      <h2>Telefon uygulaması</h2>
      <div class="phone">
        ${d.apk_var ? `<img src="/api/qr.svg?url=${encodeURIComponent(d.adresler[0] + "/apk")}" alt="Uygulamayı indirmek için QR kod">` : ""}
        <ol>
          ${d.apk_var ? "<li>Android telefonla QR kodu okutun, uygulamayı kurun.</li>" : ""}
          <li>Telefon bu bilgisayarla aynı Wi-Fi'deyken uygulamayı açın; bilgisayar otomatik bulunur.</li>
          <li>Bulunamazsa adres: ${d.adresler.map((u) => `<code>${esc(u.replace("http://", ""))}</code>`).join(" ")}</li>
        </ol>
      </div>
      ${d.eslestirme_kodu ? `<div class="pair-code"><span class="small muted">Telefonu bağlamak için kod</span>
        <b>${esc(d.eslestirme_kodu.replace(/(\d{3})(\d{3})/, "$1 $2"))}</b>
        <span class="small muted">${d.cihaz_sayisi ? `${d.cihaz_sayisi} cihaz bağlı · <a href="#" id="forget-devices">bağlantıları kes</a>` : "Henüz bağlı cihaz yok"}</span></div>` : ""}
    </aside>` : "";

  $view.innerHTML = `
    ${engineWarn ? `<div class="panel panel-pad" style="border-color:var(--err);color:var(--err);margin-bottom:16px">${esc(engineWarn)}</div>` : ""}
    <div class="home ${side ? "has-side" : ""}">
      <section>
        <div class="page-head">
          <h1>Sınavlar</h1>
          <div class="actions">
            ${window.SinavOkuyucuApp ? `<button class="btn ghost" id="change-pc">${ic("swap")}Bilgisayarı değiştir</button>` : ""}
            <button class="btn primary" id="new-exam">${ic("plus")}Yeni sınav</button>
          </div>
        </div>
        <div class="panel">
          ${exams.length ? `<table class="list">
            <thead><tr><th>Sınıf</th><th>Ders</th><th class="hide-sm">Sınav</th><th class="num hide-sm">Soru</th><th class="num">Kağıt</th><th class="num hide-sm">Tarih</th></tr></thead>
            <tbody>${exams.map((e) => `
              <tr data-href="#/sinav/${e.id}">
                <td><a href="#/sinav/${e.id}">${esc(e.sinif || "—")}</a></td>
                <td>${esc(e.ders || "—")}</td>
                <td class="hide-sm">${esc(e.ad || "")}</td>
                <td class="num hide-sm">${e.tam_puanlar.length}</td>
                <td class="num">${e.kagit_sayisi}${e.bekleyen ? `<span class="badge">${e.bekleyen} okunuyor</span>` : ""}</td>
                <td class="num hide-sm muted">${fmtDate(e.olusturma)}</td>
              </tr>`).join("")}</tbody></table>`
            : `<div class="empty">${ic("file")}<div>Henüz sınav yok.</div><div class="small">Sağ üstteki “Yeni sınav” ile başlayın.</div></div>`}
        </div>
      </section>
      ${side}
    </div>`;

  $view.querySelectorAll("tr[data-href]").forEach((tr) => (tr.onclick = () => (location.hash = tr.dataset.href)));
  document.getElementById("new-exam").onclick = () => examDialog(null);
  document.getElementById("change-pc")?.addEventListener("click", () => window.SinavOkuyucuApp.baglantiyiDegistir());
  document.getElementById("forget-devices")?.addEventListener("click", async (e) => {
    e.preventDefault();
    if (!(await ask("Bağlantıları kes", "Bağlı bütün telefonların bağlantısı kesilsin mi? Yeniden bağlanmak için kodu girmeleri gerekir.",
      [{ label: "Bağlantıları kes", value: true, cls: "danger" }]))) return;
    try { await api("/api/cihazlar", { method: "DELETE" }); toast("Bağlantılar kesildi"); showHome(); } catch (err) { toastErr(err); }
  });
}

// ------------------------------------------------------------ sınav oluştur / düzenle

const classKey = (s) => String(s || "").replace(/İ/g, "i").replace(/I/g, "ı").toLowerCase()
  .replace(/[çğıöşüâîû]/g, (c) => ({ ç: "c", ğ: "g", ı: "i", ö: "o", ş: "s", ü: "u", â: "a", î: "i", û: "u" }[c])).replace(/[^a-z0-9]/g, "");

/** ex: düzenlenecek sınav; copy: true ise ex'in kopyası olarak başka şube için yeni sınav. */
async function examDialog(ex, { copy = false } = {}) {
  const editing = ex && !copy;
  const n0 = ex ? ex.tam_puanlar.length : 10;
  const pages0 = ex?.sayfa_sayisi || 1;
  let classes = {};
  try { classes = await api("/api/siniflar"); } catch {}
  openModal(`${head(editing ? "Sınav bilgileri" : copy ? "Başka şube için kopyala" : "Yeni sınav")}
    <form id="exam-form">
      <div class="m-body">
        <div class="form-grid">
          <label class="f">Sınıf / şube<input name="sinif" placeholder="10-C" value="${esc(copy ? "" : ex?.sinif)}" autocomplete="off" list="class-list"></label>
          <label class="f">Ders<input name="ders" placeholder="Matematik" value="${esc(ex?.ders)}" autocomplete="off"></label>
          <label class="f">Sınav<input name="ad" placeholder="1. Dönem 1. Yazılı" value="${esc(ex?.ad)}" autocomplete="off"></label>
        </div>
        <datalist id="class-list">${Object.values(classes).map((c) => `<option value="${esc(c.ad)}">`).join("")}</datalist>
        <p class="hint" id="class-hint"></p>
        <div class="form-grid" style="margin-top:12px">
          <label class="f">Soru sayısı<input name="n" type="number" min="1" max="50" value="${n0}" inputmode="numeric"></label>
          <label class="f">Her soruya aynı puan<input name="all" inputmode="decimal" placeholder="örn. 10"></label>
          <label class="f">Bir kağıt kaç sayfa?
            <select name="sayfa">${[1, 2, 3, 4, 5, 6, 7, 8].map((k) => `<option value="${k}" ${k === pages0 ? "selected" : ""}>${k} sayfa</option>`).join("")}</select>
          </label>
        </div>
        <p class="hint">Sayfa sayısını doğru seçerseniz fotoğraflar ve PDF sayfaları sırayla aynı öğrenciye bağlanır; “Sayfa ekle” gerekmez.</p>
        <p class="hint" style="margin-top:14px">Tam puanlar · toplam <b id="sum">0</b></p>
        <div class="maxes" id="maxes"></div>
        ${editing && ex.kayitlar.length ? `<p class="hint">Soru sayısını değiştirirseniz okunmuş kağıtlarda yeni sorular boş kalır.</p>` : ""}
      </div>
      <div class="m-foot"><button type="button" class="btn" data-close>Vazgeç</button><button class="btn primary">${editing ? "Kaydet" : "Oluştur"}</button></div>
    </form>`);
  const f = document.getElementById("exam-form");
  const hint = () => {
    const c = classes[classKey(f.sinif.value)];
    const hasOwn = editing && ex.ogrenci_listesi.length;
    document.getElementById("class-hint").textContent = c && !hasOwn ? `${c.ad} sınıf listesi (${c.sayi} öğrenci) kendiliğinden eklenecek.` : "";
  };
  f.sinif.addEventListener("input", hint);
  hint();
  const grid = document.getElementById("maxes");
  const sum = () => (document.getElementById("sum").textContent = fmt([...grid.querySelectorAll("input")].reduce((a, i) => a + (parseScore(i.value) || 0), 0)));
  const build = () => {
    const n = Math.max(1, Math.min(50, parseInt(f.n.value) || 1));
    const cur = [...grid.querySelectorAll("input")].map((i) => i.value);
    const each = Math.floor(100 / n), rest = 100 - each * n;
    grid.innerHTML = Array.from({ length: n }, (_, i) => {
      const v = cur[i] ?? (ex?.tam_puanlar[i] !== undefined ? fmt(ex.tam_puanlar[i]) : String(each + (i < rest ? 1 : 0)));
      return `<label>S${i + 1}<input inputmode="decimal" value="${esc(v)}"></label>`;
    }).join("");
    sum();
  };
  f.n.addEventListener("input", build);
  f.all.addEventListener("input", () => { if (f.all.value.trim()) { grid.querySelectorAll("input").forEach((i) => (i.value = f.all.value.trim())); sum(); } });
  grid.addEventListener("input", sum);
  build();
  f.sinif.focus();
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const tam = [...grid.querySelectorAll("input")].map((i) => parseScore(i.value));
    if (tam.some((v) => !(v > 0))) return toast("Her sorunun tam puanı sıfırdan büyük olmalı", { err: true });
    const body = { ad: f.ad.value, sinif: f.sinif.value, ders: f.ders.value, tam_puanlar: tam, sayfa_sayisi: +f.sayfa.value,
      sablon_kaynak: copy ? ex.id : "" };
    try {
      if (editing) {
        await api(`/api/sinavlar/${ex.id}`, json("PUT", body));
        closeModal();
        showExam(ex.id);
      } else {
        const created = await api("/api/sinavlar", json("POST", body));
        closeModal();
        location.hash = `#/sinav/${created.id}`;
      }
    } catch (err) { toastErr(err); }
  });
}

// ------------------------------------------------------------ ayarlar

const f_model = (d) => d.ayarlar.model;

document.getElementById("settings-btn").onclick = async () => {
  await refreshStatus();
  const d = state.durum;
  if (!d) return;
  const label = (m) => (m.includes("8b") ? "Doğru — " : m.includes("4b") ? "Hızlı — " : m.includes("2b") ? "Çok hızlı — " : "") + m;
  const models = [...new Set([d.ayarlar.model, ...(d.ollama.modeller || [])])].filter(Boolean);
  openModal(`${head("Ayarlar")}
    <form id="settings-form">
      <div class="m-body">
        <label class="f">Okuma modeli
          <select name="model">${models.map((m) => `<option value="${esc(m)}" ${m === d.ayarlar.model ? "selected" : ""}>${esc(label(m))}</option>`).join("")}</select>
        </label>
        <p class="hint">8b en az hata yapar (önerilen). 4b yaklaşık iki kat hızlıdır ama daha çok hata yapar.</p>
        <label class="f" style="margin-top:12px">Çapraz kontrol modeli
          <select name="ikinci_model">${[...new Set(["kapali", d.ayarlar.ikinci_model || "kapali", ...(d.ollama.modeller || [])])]
            .filter((m) => m === "kapali" || m !== f_model(d))
            .map((m) => `<option value="${esc(m)}" ${m === (d.ayarlar.ikinci_model || "kapali") ? "selected" : ""}>${m === "kapali" ? "Kapalı" : esc(m) + ((d.ollama.modeller || []).includes(m) ? "" : " (yüklü değil)")}</option>`).join("")}</select>
        </label>
        <p class="hint">Okunan puanlar, farklı eğitilmiş ikinci bir modelle bir kez daha okunur; tutmazsa hücre kontrole düşer.
        Varsayılan kapalı: denenen modeller Türkçe el yazısında ana modelden zayıf çıktı ve doğru okunan puanları gereksiz
        yere kontrole düşürdü. Açarsanız kağıt başına bir okuma ekler; model ~5,5 GB yer ve 16 GB bellek ister.</p>
        <div class="form-grid" style="margin-top:12px">
          <label class="f">Çözünürlük (piksel)<input name="max_kenar" type="number" min="512" max="3000" value="${d.ayarlar.max_kenar}"></label>
          <label class="f">Zaman aşımı (sn)<input name="zaman_asimi" type="number" min="30" max="3600" value="${d.ayarlar.zaman_asimi}"></label>
          <label class="f">Ekran kartı
            <select name="ollama_gpu">${[["otomatik", "Otomatik"], ["cuda", "NVIDIA (CUDA)"], ["vulkan", "Vulkan"], ["cpu", "Yalnızca işlemci"]]
              .map(([v, l]) => `<option value="${v}" ${v === (d.ayarlar.ollama_gpu || "otomatik") ? "selected" : ""}>${l}</option>`).join("")}</select>
          </label>
        </div>
        <label class="check" style="margin-top:14px"><input type="checkbox" name="yalnizca_renkli_kalem" ${d.ayarlar.yalnizca_renkli_kalem !== false ? "checked" : ""}>
          Puanları yalnızca renkli tükenmez kalemden oku</label>
        <p class="hint">Açıkken kurşun kalem ve gri/siyah izler puan olarak okunmaz; kağıda sonradan kurşun kalemle yazılan bir rakam
        sistemi kandıramaz. Fotoğrafta siyah tükenmez ile kurşun kalem ayırt edilemediği için, puanları siyah kalemle veriyorsanız kapatın.
        Ad ve numara her kalemle okunur.</p>
        <p class="hint">Ekran kartı ayarı programı yeniden açınca geçerli olur. “Otomatik” çalışan yolu kendisi bulur.</p>
        <p class="hint">Çözünürlüğü artırmak küçük yazıları daha iyi okur, ama okumayı yavaşlatır.
        Her puan birkaç kez okunur; okumalar birbirini tutmazsa ya da puan bulunamazsa kağıt “Kontrol” listesine düşer.</p>
      </div>
      <div class="m-foot"><button type="button" class="btn" data-close>Vazgeç</button><button class="btn primary">Kaydet</button></div>
    </form>`);
  document.getElementById("settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (f.ikinci_model.value !== "kapali" && f.ikinci_model.value === f.model.value) {
      return toast("Çapraz kontrol modeli okuma modelinden farklı olmalı (aynıysa kontrol yapılmaz)", { err: true });
    }
    try {
      const num = (v) => (String(v).trim() === "" ? null : +v);  // boş bırakılan alan değiştirilmesin
      await api("/api/ayarlar", json("PUT", { model: f.model.value, max_kenar: num(f.max_kenar.value), zaman_asimi: num(f.zaman_asimi.value),
        ollama_gpu: f.ollama_gpu.value,
        yalnizca_renkli_kalem: f.yalnizca_renkli_kalem.checked, ikinci_model: f.ikinci_model.value }));
      closeModal();
      toast("Ayarlar kaydedildi");
      refreshStatus();
    } catch (err) { toastErr(err); }
  });
};

// ------------------------------------------------------------ sınav sayfası

async function showExam(id) {
  let exam;
  try { exam = await api(`/api/sinavlar/${id}`); }
  catch (err) {
    if (!err.pairing) $view.innerHTML = `<div class="empty">${esc(err.message)}<br><a href="#/">Sınavlara dön</a></div>`;
    return;
  }
  state.exam = exam;
  state.renderedKey = "";
  document.querySelector(".capture-bar")?.remove();  // showExam doğrudan çağrıldığında ikinci çubuk oluşmasın

  const title = [exam.sinif, exam.ders].filter(Boolean).join(" · ") || "Sınav";
  $view.innerHTML = `
    <a href="#/" class="crumb">${ic("left")}Sınavlar</a>
    <div class="exam-head">
      <div>
        <h1>${esc(title)}</h1>
        <div class="meta">${esc(exam.ad || "")}${exam.ad ? " · " : ""}${exam.tam_puanlar.length} soru · ${fmt(maxTotal())} puan${pagesPer() > 1 ? ` · kağıt ${pagesPer()} sayfa` : ""}${exam.sablon?.kutular?.length ? " · şablonlu okuma" : ""}</div>
      </div>
      <div class="actions">
        <label class="btn primary desk-only" title="Fotoğraflar ya da tarayıcıdan (fotokopi makinesi) PDF. Pencereye sürükleyip bırakabilirsiniz.">${ic("image")}Fotoğraf / PDF ekle<input type="file" accept="image/*,application/pdf" multiple data-upload="many"></label>
        <div class="sheets-pick" title="Kağıtları masaya yan yana dizip tek fotoğrafta 2 ya da 4 kağıt çekebilirsiniz">
          <span>Fotoğrafta</span><div class="seg" id="sheets-seg">${[1, 2, 4].map((v) => `<button data-sheets="${v}" class="${sheetsPer() === v ? "on" : ""}">${v}</button>`).join("")}</div><span>kağıt</span>
        </div>
        <button class="btn" id="roster-btn">${ic("users")}Sınıf listesi</button>
        <button class="btn" id="excel-btn">${ic("download")}Excel</button>
        <button class="btn" id="template-btn" title="Puanların yazıldığı yerleri bir kez işaretleyin: en doğru okuma">${ic("template")}Şablon</button>
        <button class="icon-btn" id="edit-btn" title="Sınav bilgileri">${ic("edit")}</button>
        <button class="icon-btn dots" id="more-btn" title="Diğer">${dots}</button>
      </div>
    </div>
    <div id="sheets-hint"></div>
    ${exam.sablon?.kutular?.length ? "" : `<div class="panel panel-pad hint-bar" id="template-hint">
      <div><b>En doğru okuma için şablonu işaretleyin.</b> Bir kağıt üzerinde her sorunun puanının yazıldığı yeri bir kez kutu içine alırsınız;
      sonra her kağıt şablona oturtulup her soru ayrı ayrı, yakından okunur. Puanın hangi soruya ait olduğu karışmaz.</div>
      <button class="btn primary">${ic("template")}Şablonu işaretle</button></div>`}
    <div class="panel">
      <div class="bar">
        <div class="stats" id="stats"></div>
        <div class="actions">
          <div class="seg" id="filter"></div>
          <select id="sort" aria-label="Sıralama">
            <option value="eklenme">Son eklenen üstte</option>
            <option value="no">Numaraya göre</option>
          </select>
        </div>
      </div>
      <div class="sheet-wrap"><div class="sheet" id="sheet" style="--n:${exam.tam_puanlar.length}"></div></div>
    </div>`;

  const bar = document.createElement("div");
  bar.className = "capture-bar";
  const nativeCam = typeof window.SinavOkuyucuApp?.seriCekim === "function";
  bar.innerHTML = `
    ${nativeCam
      ? `<button class="btn primary" id="serial-btn">${ic("camera")}Kağıtları çek</button>`
      : `<label class="btn primary">${ic("camera")}Kağıt çek<input type="file" accept="image/*" capture="environment" data-upload="one" hidden></label>`}
    <label class="btn" title="Galeriden ya da dosyadan seç (PDF de olur)">${ic("image")}Galeri<input type="file" accept="image/*,application/pdf" multiple data-upload="many" hidden></label>`;
  document.body.appendChild(bar);
  bar.querySelector("#serial-btn")?.addEventListener("click", () => {
    const t = [state.exam.sinif, state.exam.ders].filter(Boolean).join(" · ") || "Sınav";
    try {
      window.SinavOkuyucuApp.seriCekim(state.exam.id, t, pagesPer(), sheetsPer());
    } catch {
      // eski telefon uygulaması: 4. bilgiyi tanımaz, fotoğrafı küçültüp gönderir
      window.SinavOkuyucuApp.seriCekim(state.exam.id, t, pagesPer());
      if (sheetsPer() > 1) toast("Telefon uygulamanız eski: çoklu kağıtta fotoğraflar düşük çözünürlükte gider. Uygulamayı güncelleyin (Ayarlar'daki QR).", { err: true, ms: 9000 });
    }
  });
  document.getElementById("sheets-seg").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-sheets]");
    if (!b || +b.dataset.sheets === sheetsPer()) return;
    state.mutSeq = (state.mutSeq || 0) + 1;  // bu sırada gelen eski bir yoklama yanıtı seçimi geri almasın
    state.mutating = (state.mutating || 0) + 1;
    try {
      applyFresh(await api(`/api/sinavlar/${state.exam.id}/fotodaki-kagit`, json("PUT", { deger: +b.dataset.sheets })));
      syncSheetsPick();
    } catch (err) { toastErr(err); } finally { state.mutating--; }
  });
  renderSheetsHint();

  document.getElementById("roster-btn").onclick = rosterDialog;
  document.getElementById("edit-btn").onclick = () => examDialog(state.exam);
  document.getElementById("template-btn").onclick = () => templateDialog();
  document.getElementById("template-hint")?.querySelector("button")?.addEventListener("click", () => templateDialog());
  document.getElementById("more-btn").onclick = moreDialog;
  document.getElementById("excel-btn").onclick = downloadExcel;
  const sort = document.getElementById("sort");
  sort.value = state.sort;
  sort.onchange = () => { state.sort = sort.value; renderSheet(true); };

  ensureRosterDatalist();
  renderSheet(true);
  poll();
}

function syncSheetsPick() {
  document.querySelectorAll("#sheets-seg button").forEach((x) => x.classList.toggle("on", +x.dataset.sheets === sheetsPer()));
  renderSheetsHint();
}

function renderSheetsHint() {
  const el = document.getElementById("sheets-hint");
  if (!el) return;
  const n = sheetsPer();
  if (n === 1) { el.innerHTML = ""; return; }
  const per = pagesPer();
  const how = n === 2 ? "yan yana" : "2×2 (iki üstte, iki altta)";
  const order = per > 1
    ? ` Bir öğrencinin ${per} sayfası sırayla gelsin (ör. ${n === 4 && per === 2 ? "üst satır bir öğrenci, alt satır diğeri" : "soldaki 1. sayfa, sağdaki 2. sayfa"}).`
    : "";
  el.innerHTML = `<div class="panel panel-pad hint-bar"><div><b>Her fotoğrafta ${n} kağıt.</b> Kağıtları ${how} dizin, aralarında biraz boşluk bırakın;
    koyu bir masa en iyisidir. Kağıtlar soldan sağa, yukarıdan aşağı sırayla okunur.${order}
    ${state.exam.sablon?.kutular?.length ? "" : " Çoklu çekimde şablon işaretlemeniz özellikle önerilir."}</div></div>`;
}

function poll() {
  clearTimeout(state.pollTimer);
  const exam = state.exam;
  if (!exam) return;
  state.pollTimer = setTimeout(async () => {
    if (!state.exam || state.exam.id !== exam.id) return;
    const seq = state.mutSeq;
    try {
      const fresh = await api(`/api/sinavlar/${exam.id}`);
      // bu istek sürerken bir düzeltme gönderildiyse yanıt eskidir: ekrana yazma (düzeltmeyi geri getirmesin)
      if (state.exam && state.exam.id === fresh.id && state.mutSeq === seq && !state.mutating) applyFresh(fresh);
    } catch {}
    poll();
  }, exam.kayitlar.some(isBusy) ? 2000 : 5000);
}

function visibleRecords() {
  let list = [...state.exam.kayitlar];
  if (state.filter === "kontrol") list = list.filter((r) => hasWarn(r) || r.durum === "hata");
  if (state.filter === "okunuyor") list = list.filter(isBusy);
  if (state.sort === "no") {
    const key = (r) => (/^\d+$/.test(r.ogrenci_no || "") ? +r.ogrenci_no : Infinity);
    list.sort((a, b) => key(a) - key(b) || (a.ad_soyad || "").localeCompare(b.ad_soyad || "", "tr"));
  } else {
    list.sort((a, b) => (b.eklenme || 0) - (a.eklenme || 0));
  }
  return list;
}

function renderStats() {
  const ks = state.exam.kayitlar;
  const done = ks.filter((r) => r.durum === "tamam");
  const busy = ks.filter(isBusy).length;
  const queued = ks.filter(isQueued).length;
  const warn = ks.filter((r) => hasWarn(r) || r.durum === "hata").length;
  const avg = done.length ? done.reduce((a, r) => a + totalOf(r), 0) / done.length : null;
  const roster = state.exam.ogrenci_listesi;
  const missing = roster.length ? roster.filter((s) => !ks.some((r) => r.ogrenci_no === s.no)) : [];
  state.missing = missing;
  // tahmini bitiş: bu sınavda okunan kağıtların ortalama süresinden
  const timed = done.filter((r) => r.sure);
  const per = timed.length ? timed.reduce((a, r) => a + r.sure, 0) / timed.length : 120 * pagesPer();
  const mins = Math.max(1, Math.round((queued * per) / 60));
  document.getElementById("stats").innerHTML = `
    <span><b>${done.length}</b> okundu</span>
    ${busy ? `<span><b>${busy}</b> sırada${queued ? ` · ~${mins} dk` : ""}</span>` : ""}
    ${warn ? `<span class="w"><b>${warn}</b> kontrol edilmeli</span>` : ""}
    ${avg !== null ? `<span>ortalama <b>${fmt(Math.round(avg * 10) / 10)}</b></span>` : ""}
    ${roster.length ? `<a href="#" id="missing-link" class="${missing.length ? "" : "muted"}"><b>${missing.length}</b> / ${roster.length} öğrencinin kağıdı yok</a>` : ""}`;
  document.getElementById("missing-link")?.addEventListener("click", (e) => { e.preventDefault(); missingDialog(); });
  const counts = { hepsi: ks.length, kontrol: warn, okunuyor: busy };
  if (state.filter !== "hepsi" && counts[state.filter] === 0) state.filter = "hepsi";
  document.getElementById("filter").innerHTML = [["hepsi", "Tümü"], ["kontrol", "Kontrol"], ["okunuyor", "Sırada"]]
    .map(([k, l]) => `<button data-filter="${k}" class="${state.filter === k ? "on" : ""}" ${k !== "hepsi" && !counts[k] ? "disabled" : ""}>${l}${k !== "hepsi" && counts[k] ? ` ${counts[k]}` : ""}</button>`).join("");
}

function headRow() {
  const qs = state.exam.tam_puanlar.map((m, i) => `<div>S${i + 1} <small>/${fmt(m)}</small></div>`).join("");
  return `<div class="row head"><div></div><div class="c-no">No</div><div class="c-name">Ad soyad</div>${qs}<div>Toplam</div><div title="✓ onaylandı · ! kontrol edilmeli (nedeni satırın altında yazar)">Durum</div><div></div></div>`;
}

function renderSheet(force = false) {
  renderStats();
  const sheet = document.getElementById("sheet");
  if (!sheet) return;
  const list = visibleRecords();
  const key = state.filter + state.sort + list.map((r) => r.id).join();
  if (!force && key === state.renderedKey) return;
  if (!force && sheet.contains(document.activeElement)) return; // yazarken listeyi yeniden kurma
  state.renderedKey = key;
  if (!state.exam.kayitlar.length) {
    sheet.innerHTML = `<div class="empty">${ic("camera")}<div>Henüz kağıt yok.</div>
      <div class="small">${isNarrow() ? "Aşağıdaki “Kağıt çek” ile" : "“Fotoğraf ekle” ile ya da telefon uygulamasından"} kağıtların fotoğrafını ekleyin.
      Kağıdı düz bir zemine koyun ve tamamı kadraja girsin.</div></div>`;
    return;
  }
  if (!list.length) {
    sheet.innerHTML = `<div class="empty">Bu filtrede kağıt yok.</div>`;
    return;
  }
  sheet.innerHTML = headRow() + list.map(rowHtml).join("");
}

function thumbHtml(r) {
  return r.resimler?.length
    ? `<img class="thumb" loading="lazy" data-review="${r.id}" src="/api/sinavlar/${state.exam.id}/resim/${r.resimler[0]}" alt="Kağıt">`
    : `<span class="thumb" title="Fotoğraf silinmiş"></span>`;
}

/** Durum işareti: onaylandı (✓), kontrol edilmeli (!), sorunsuz okundu (boş). */
function confHtml(r) {
  if (r.onaylandi) return `<div class="c-conf" data-sync="conf"><span class="conf ok" title="Öğretmen onayladı">✓</span></div>`;
  if (hasWarn(r)) return `<div class="c-conf" data-sync="conf"><span class="conf low" title="Kontrol edilmeli: nedeni satırın altında">!</span></div>`;
  return `<div class="c-conf" data-sync="conf"><span class="conf none" title="Uyarısız okundu">·</span></div>`;
}

function notesHtml(r) {
  const cw = r.hucre_uyarilari || {};
  const list = [...r.uyarilar, ...Object.entries(cw).map(([k, v]) => (k.startsWith("p") ? `S${+k.slice(1) + 1}: ` : k === "no" ? "No: " : "Ad: ") + v)];
  const info = r.eslesme_bilgi && !r.onaylandi ? `<div class="info">${esc(r.eslesme_bilgi)}</div>` : "";
  return (list.length && !r.onaylandi) || info
    ? `<div class="notes" data-sync="notes">${r.onaylandi ? "" : list.map((w) => `<div>${esc(w)}</div>`).join("")}${info}</div>`
    : `<div data-sync="notes" hidden></div>`;
}

function inputs(r, labels) {
  const cw = r.hucre_uyarilari || {};
  return state.exam.tam_puanlar.map((mx, i) => {
    const w = cw[`p${i}`];
    return `<div class="cell">${labels ? `<span class="ql">S${i + 1} /${fmt(mx)}</span>` : `<span class="ql">S${i + 1}</span>`}<input data-q="${i}" inputmode="decimal" value="${esc(fmt(r.puanlar?.[i]))}" class="${w ? "w" : ""}" title="${esc(w || `S${i + 1} · tam puan ${fmt(mx)}`)}" aria-label="Soru ${i + 1}"></div>`;
  }).join("");
}

function whoInputs(r) {
  const cw = r.hucre_uyarilari || {};
  return {
    no: `<input data-f="no" inputmode="numeric" placeholder="No" value="${esc(r.ogrenci_no)}" class="${cw.no ? "w" : ""}" title="${esc(cw.no || "Okul numarası")}" aria-label="Okul numarası">`,
    ad: `<input data-f="ad" placeholder="Ad soyad" value="${esc(r.ad_soyad)}" class="${cw.ad ? "w" : ""}" title="${esc(cw.ad || "Ad soyad")}" autocomplete="off" list="roster-list" aria-label="Ad soyad">`,
  };
}

function rowActions(r) {
  return `<div class="c-act" data-sync="act">
    <button class="icon-btn ${r.onaylandi ? "on" : ""}" data-act="${r.onaylandi ? "unconfirm" : "confirm"}" title="${r.onaylandi ? "Onaylandı (geri al)" : "Kontrol ettim, onayla"}">${ic("check")}</button>
    <label class="icon-btn" title="Bu öğrenciye sayfa ekle">${ic("pageplus")}<input type="file" accept="image/*" capture="environment" data-upload="page"></label>
    <button class="icon-btn" data-act="retry" title="Yeniden oku">${ic("refresh")}</button>
    <button class="icon-btn danger" data-act="del" title="Sil">${ic("trash")}</button>
  </div>`;
}

function rowHtml(r) {
  if (r.durum === "eksik") {
    return `<div class="row compact" data-rec="${r.id}" data-kind="row">
      <div class="c-thumb">${thumbHtml(r)}</div>
      <div class="c-state"><span class="pages">${r.resimler.length}/${pagesPer()}</span>Diğer sayfa bekleniyor</div>
      <div class="c-act">
        <label class="icon-btn" title="Sayfa ekle">${ic("pageplus")}<input type="file" accept="image/*" capture="environment" data-upload="page"></label>
        <button class="icon-btn" data-act="complete" title="Bu sayfalarla şimdi oku">${ic("check")}</button>
        <button class="icon-btn danger" data-act="del" title="Sil">${ic("trash")}</button>
      </div>
    </div>`;
  }
  if (isQueued(r) || r.durum === "hata") {
    const pages = r.resimler?.length > 1 ? ` · ${r.resimler.length} sayfa` : "";
    const text = r.durum === "hata" ? esc(r.hata || "Okunamadı")
      : r.durum === "isleniyor" ? esc(r.ilerleme || "Okunuyor…") : "Sırada bekliyor";
    return `<div class="row compact ${r.durum === "hata" ? "fail" : ""}" data-rec="${r.id}" data-kind="row">
      <div class="c-thumb">${thumbHtml(r)}</div>
      <div class="c-state ${r.durum === "hata" ? "err" : ""}">${r.durum === "hata" ? "" : '<div class="spinner"></div>'}${text}${pages}</div>
      <div class="c-act">${r.durum === "hata" ? `<button class="icon-btn" data-act="retry" title="Yeniden oku">${ic("refresh")}</button>` : ""}<button class="icon-btn danger" data-act="del" title="Sil">${ic("trash")}</button></div>
    </div>`;
  }
  const w = whoInputs(r);
  const cls = r.onaylandi ? "done" : hasWarn(r) ? "warn" : "";
  return `<div class="row ${cls}" data-rec="${r.id}" data-kind="row">
    <div class="c-thumb">${thumbHtml(r)}</div>
    <div class="cell c-no">${w.no}</div>
    <div class="cell c-name">${w.ad}</div>
    <div class="scores">${inputs(r, false)}</div>
    <div class="c-total" data-sync="total">${fmt(totalOf(r))}<small>/ ${fmt(maxTotal())}</small></div>
    ${confHtml(r)}
    ${rowActions(r)}
    ${notesHtml(r)}
  </div>`;
}

/** Sunucudan gelen yeni veriyi uygula; kullanıcının o an yazdığı satırı bozma. */
function applyFresh(fresh) {
  const old = state.exam;
  state.exam = fresh;
  ensureRosterDatalist();
  renderStats();
  const list = visibleRecords();
  const key = state.filter + state.sort + list.map((r) => r.id).join();
  if (key !== state.renderedKey) { renderSheet(); }
  else {
    const before = Object.fromEntries(old.kayitlar.map((r) => [r.id, JSON.stringify(r)]));
    for (const r of fresh.kayitlar) if (before[r.id] !== JSON.stringify(r)) syncRecord(r);
  }
  if (state.reviewId) {
    const r = fresh.kayitlar.find((x) => x.id === state.reviewId);
    if (r) syncRecord(r);
    refreshReview();
  }
  if (old.fotodaki_kagit !== fresh.fotodaki_kagit || old.sayfa_sayisi !== fresh.sayfa_sayisi) syncSheetsPick();
}

/** Kontrol ekranının yeniden kurulmasını gerektiren değişiklikler: durum, sayfalar, kutu resimleri. */
const reviewKey = (r) => [r.durum, (r.resimler || []).join(), Object.values(r.kutu_resimleri || {}).join()].join("|");

/** Kontrol ekranı açıksa ve gösterdiği kağıt yeniden okunmaya başladıysa / okuması bittiyse ekranı yeniden kur:
 * eski puanlarla düzenlenebilir bir form kalmasın, kutu kartları eskimesin. */
function refreshReview() {
  if (!state.reviewId || $modal.hidden) return;
  const r = state.exam.kayitlar.find((x) => x.id === state.reviewId);
  if (!r) { closeModal(); return; }
  if (reviewKey(r) !== state.reviewKey) openReview(r.id);
}

/** Bir kaydı gösteren tüm öğeleri (tablo satırı, kontrol ekranı) güncelle. force: odaktaki kutunun değeri de
 * sunucudakine döndürülür (kaydedilemeyen bir düzeltme ekranda kayıtlıymış gibi kalmasın). */
function syncRecord(r, force = false) {
  document.querySelectorAll(`[data-rec="${r.id}"]`).forEach((el) => {
    const html = el.dataset.kind === "form" ? formHtml(r) : rowHtml(r);
    if (!el.contains(document.activeElement)) { el.outerHTML = html; return; }
    // odak bu öğede: değerleri ezmeden yalnızca renkleri, toplamı ve notları güncelle
    const tmp = document.createElement("div");
    tmp.innerHTML = html;
    const fresh = tmp.firstElementChild;
    el.className = fresh.className;
    el.querySelectorAll("input[data-q], input[data-f]").forEach((inp) => {
      const sel = inp.dataset.q !== undefined ? `input[data-q="${inp.dataset.q}"]` : `input[data-f="${inp.dataset.f}"]`;
      const src = fresh.querySelector(sel);
      if (!src) return;
      inp.className = src.className;
      inp.title = src.title;
      if (force || inp !== document.activeElement) inp.value = src.value;
    });
    el.querySelectorAll("[data-sync]").forEach((part) => {
      const src = fresh.querySelector(`[data-sync="${part.dataset.sync}"]`);
      if (src) part.replaceWith(src);
    });
  });
}

function ensureRosterDatalist() {
  let dl = document.getElementById("roster-list");
  if (!dl) { dl = document.createElement("datalist"); dl.id = "roster-list"; document.body.appendChild(dl); }
  dl.innerHTML = state.exam.ogrenci_listesi.map((s) => `<option value="${esc(s.ad)}">${esc(s.no)}</option>`).join("");
}

/** Kaydı düzelt. Dönüş: kaydedildiyse true. Kaydedilemezse ekran sunucudaki değere döner (yoksa öğretmen düzeltmenin
 * kaydedildiğini sanar, ardından "Onayla" eski değeri onaylardı). */
async function patchRecord(id, body) {
  state.mutSeq = (state.mutSeq || 0) + 1;
  state.mutating = (state.mutating || 0) + 1;
  try {
    const fresh = await api(`/api/sinavlar/${state.exam.id}/kagit/${id}`, json("PATCH", body));
    const old = state.exam;
    state.exam = fresh;
    renderStats();
    const before = Object.fromEntries(old.kayitlar.map((r) => [r.id, JSON.stringify(r)]));
    // aynı numara başka satırlarda da uyarıyı değiştirebilir: değişen her kaydı güncelle
    for (const r of fresh.kayitlar) if (before[r.id] !== JSON.stringify(r)) syncRecord(r);
    refreshReview();
    return true;
  } catch (err) {
    toastErr(err.pairing ? err : new Error(`Kaydedilemedi: ${err.message}. Değer eski hâline döndürüldü; yeniden deneyin.`));
    const r = state.exam?.kayitlar.find((x) => x.id === id);
    if (r) syncRecord(r, true);
    return false;
  } finally { state.mutating--; }
}

async function reload() {
  state.exam = await api(`/api/sinavlar/${state.exam.id}`);
  ensureRosterDatalist();
  renderSheet(true);
  syncSheetsPick();
  if (state.reviewId) {
    const r = state.exam.kayitlar.find((x) => x.id === state.reviewId);
    if (r) syncRecord(r); else closeModal();
    refreshReview();
  }
  poll();
}

// ------------------------------------------------------------ olaylar (tablo ve kontrol ekranı ortak)

// Dosya seçiciler: seçici açıkken satır yeniden çizilse bile fotoğraf kaybolmasın diye
// olay doğrudan seçicinin kendisine, tıklandığı anda bağlanır (ve kağıt kimliği o an alınır).
document.addEventListener("click", (e) => {
  const input = e.target.closest?.("input[data-upload]");
  if (!input) return;
  const recId = input.closest("[data-rec]")?.dataset.rec || "";
  input.onchange = () => onUpload(input, recId);
}, true);

document.addEventListener("change", (e) => {
  const t = e.target;
  if (t.dataset.upload) return;  // yukarıda bağlanan onchange işler
  if (t.dataset.total !== undefined) {
    const box = t.closest("[data-rec]");
    const v = parseScore(t.value);
    if (Number.isNaN(v)) return toast("Toplam bir sayı olmalı", { err: true });
    return patchRecord(box.dataset.rec, v === null ? { yazili_toplam_sil: true } : { yazili_toplam: v });
  }
  const box = t.closest("[data-rec]");
  if (!box || !state.exam) return;
  const r = state.exam.kayitlar.find((x) => x.id === box.dataset.rec);
  if (!r) return;
  if (t.dataset.f === "no") {
    if (t.value.trim() === (r.ogrenci_no || "")) return;
    return patchRecord(r.id, { ogrenci_no: t.value });
  }
  if (t.dataset.f === "ad") {
    if (t.value.trim() === (r.ad_soyad || "")) return;
    const s = state.exam.ogrenci_listesi.find((x) => x.ad === t.value.trim());
    return patchRecord(r.id, s ? { ad_soyad: s.ad, ogrenci_no: s.no } : { ad_soyad: t.value });
  }
  if (t.dataset.q !== undefined) {
    const i = +t.dataset.q;
    const v = parseScore(t.value);
    if (Number.isNaN(v)) return toast("Puan bir sayı olmalı", { err: true });
    if ((r.puanlar?.[i] ?? null) === v) return;
    return patchRecord(r.id, { puan: { soru: i, deger: v } });  // yalnızca bu hücre: başka düzeltmeleri ezmez
  }
});

document.addEventListener("keydown", (e) => {
  // Enter: sonraki kutuya geç (tablo içinde)
  if (e.key !== "Enter" || !e.target.matches("[data-rec] input[data-q], [data-rec] input[data-f]")) return;
  e.preventDefault();
  const scope = e.target.closest(".sheet, .vform") || document;
  const all = [...scope.querySelectorAll("input[data-q], input[data-f]")];
  (all[all.indexOf(e.target) + 1] || e.target).focus();
});

document.addEventListener("click", async (e) => {
  const f = e.target.closest("[data-filter]");
  if (f && !f.disabled) { state.filter = f.dataset.filter; renderSheet(true); return; }
  const rv = e.target.closest("[data-review]");
  if (rv) return openReview(rv.dataset.review);
  const btn = e.target.closest("button[data-act]");
  if (!btn || !state.exam) return;
  const id = btn.closest("[data-rec]")?.dataset.rec;
  if (!id) return;
  const act = btn.dataset.act;
  if (act === "confirm") {
    const inReview = !!btn.closest(".vform");  // patchRecord ekranı yenileyince buton DOM'dan ayrılabiliyor: önce oku
    const ok = await patchRecord(id, { onaylandi: true });
    // kontrol ekranında onaylayınca kontrol bekleyen bir sonraki kağıda geç
    if (ok && inReview && state.reviewId === id) nextToCheck(id);
    return;
  }
  if (act === "unconfirm") return patchRecord(id, { onaylandi: false });
  if (act === "ignore-total") return patchRecord(id, { toplam_uyarisi_kapali: true });
  if (act === "complete") {
    try { await api(`/api/sinavlar/${state.exam.id}/kagit/${id}/tamamla`, { method: "POST" }); } catch (err) { return toastErr(err); }
    return reload();
  }
  if (act === "retry") {
    if (!(await ask("Yeniden oku", "Bu kağıt yeniden okunsun mu? Elle yaptığınız düzeltmeler silinir.", [{ label: "Yeniden oku", value: true }]))) return;
    try { await api(`/api/sinavlar/${state.exam.id}/kagit/${id}/tekrar`, { method: "POST" }); } catch (err) { return toastErr(err); }
    return reload();
  }
  if (act === "del") {
    if (!(await ask("Kağıdı sil", "Bu kağıt ve fotoğrafı silinsin mi?", [{ label: "Sil", value: true, cls: "danger" }]))) return;
    try { await api(`/api/sinavlar/${state.exam.id}/kagit/${id}`, { method: "DELETE" }); } catch (err) { return toastErr(err); }
    if (state.reviewId === id) closeModal();
    return reload();
  }
  if (act === "prev" || act === "next") return stepReview(act === "next" ? 1 : -1);
});

// ------------------------------------------------------------ kontrol ekranı

function formHtml(r) {
  const w = whoInputs(r);
  const list = visibleRecords().filter((x) => x.durum === "tamam");
  const idx = list.findIndex((x) => x.id === r.id);
  return `<div class="side vform" data-rec="${r.id}" data-kind="form">
    <div class="who"><div class="cell">${w.no}</div><div class="cell">${w.ad}</div></div>
    <div class="grid">${inputs(r, true)}</div>
    <div class="sum" data-sync="total"><span class="muted">Toplam</span><b>${fmt(totalOf(r))} <small class="muted">/ ${fmt(maxTotal())}</small></b></div>
    <div class="written-total">
      <label class="small muted">Kağıtta yazan toplam <input data-total inputmode="decimal" value="${esc(fmt(r.yazili_toplam))}" placeholder="yok"></label>
      ${r.uyarilar.some((w) => w.startsWith("Kağıttaki toplam")) ? `<button class="btn small" data-act="ignore-total" title="Toplam yanlış okunmuş ya da önemsiz">Uyarıyı kaldır</button>` : ""}
    </div>
    ${notesHtml(r)}
    <div class="actions" data-sync="act">
      <button class="btn ${r.onaylandi ? "" : "primary"}" data-act="${r.onaylandi ? "unconfirm" : "confirm"}">${ic("check")}${r.onaylandi ? "Onaylandı" : "Onayla"}</button>
      <label class="btn">${ic("pageplus")}Sayfa ekle<input type="file" accept="image/*" capture="environment" data-upload="page"></label>
      <button class="icon-btn" data-act="retry" title="Yeniden oku">${ic("refresh")}</button>
      <button class="icon-btn danger" data-act="del" title="Sil">${ic("trash")}</button>
    </div>
    <div class="nav">
      <button class="btn" data-act="prev" ${idx <= 0 ? "disabled" : ""}>${ic("left")}Önceki</button>
      <span class="small muted">${idx + 1} / ${list.length}</span>
      <button class="btn" data-act="next" ${idx < 0 || idx >= list.length - 1 ? "disabled" : ""}>Sonraki${ic("right")}</button>
    </div>
    ${r.sure ? `<div class="small muted">${fmt(Math.round(r.sure))} saniyede okundu${r.resimler.length > 1 ? ` · ${r.resimler.length} sayfa` : ""}</div>` : ""}
  </div>`;
}

function openReview(id) {
  const r = state.exam.kayitlar.find((x) => x.id === id);
  if (!r) return;
  state.reviewId = id;
  state.reviewKey = reviewKey(r);
  const name = r.ad_soyad || "Kağıt";
  const pics = r.resimler.map((n) => `<img src="/api/sinavlar/${state.exam.id}/resim/${n}" alt="Sayfa">`).join("");
  // her sorunun puan kutusu, okunan değerle yan yana: öğretmen bütün kağıdı aramadan bir bakışta doğrular
  const kr = r.durum === "tamam" ? (r.kutu_resimleri || {}) : {};
  const cw = r.hucre_uyarilari || {};
  const boxes = Object.keys(kr).length ? `<div class="boxes">${state.exam.tam_puanlar.map((mx, i) => kr[i]
    ? `<button type="button" class="bx ${cw[`p${i}`] && !r.onaylandi ? "w" : ""}" data-focus-q="${i}" title="${esc(cw[`p${i}`] || `S${i + 1}`)}">
        <img src="/api/sinavlar/${state.exam.id}/resim/${kr[i]}" alt="S${i + 1} kutusu"><span>S${i + 1} /${fmt(mx)}</span><b data-bx="${i}">${esc(fmt(r.puanlar?.[i])) || "—"}</b></button>`
    : `<div class="bx none"><span>S${i + 1}</span><b>${esc(fmt(r.puanlar?.[i])) || "—"}</b></div>`).join("")}</div>` : "";
  const body = r.durum === "tamam" ? formHtml(r) : `<div class="side"><p class="muted">${isBusy(r) ? "Bu kağıt henüz okunuyor." : esc(r.hata || "")}</p></div>`;
  openModal(`${head(`${esc(name)}${r.ogrenci_no ? ` <span class="muted small">· ${esc(r.ogrenci_no)}</span>` : ""}`)}
    <div class="review"><div class="pic">${boxes}${pics || '<div class="empty">Fotoğraf silinmiş.</div>'}</div>${body}</div>`, { wide: true });
  $modalBody.querySelectorAll(".pic > img").forEach((img) => (img.onclick = () => img.classList.toggle("zoom")));
  $modalBody.querySelectorAll("[data-focus-q]").forEach((b) => (b.onclick = () => {
    const inp = $modalBody.querySelector(`.vform input[data-q="${b.dataset.focusQ}"]`);
    if (inp) { inp.focus(); inp.select(); }
  }));

}

// kontrol ekranında puan düzeltilince kutu kartındaki değer de değişsin (form yeniden çizilse de çalışır)
document.addEventListener("input", (e) => {
  const inp = e.target.closest?.(".vform input[data-q]");
  if (!inp) return;
  const b = $modalBody.querySelector(`[data-bx="${inp.dataset.q}"]`);
  if (b) b.textContent = inp.value.trim() || "—";
});

function nextToCheck(fromId) {
  const list = visibleRecords().filter((x) => x.durum === "tamam");
  const i = list.findIndex((x) => x.id === fromId);
  const rest = [...list.slice(i + 1), ...list.slice(0, Math.max(0, i))];
  const next = rest.find((x) => hasWarn(x)) || rest.find((x) => !x.onaylandi);
  if (next) openReview(next.id);
  else { closeModal(); toast("Kontrol bekleyen kağıt kalmadı"); }
}

function stepReview(dir) {
  const list = visibleRecords().filter((x) => x.durum === "tamam");
  const i = list.findIndex((x) => x.id === state.reviewId);
  const next = list[i + dir];
  if (next) openReview(next.id);
}

// ------------------------------------------------------------ yükleme

const isPdf = (f) => f.type === "application/pdf" || /\.pdf$/i.test(f.name);
const natural = new Intl.Collator("tr", { numeric: true, sensitivity: "base" });

async function onUpload(input, recId = "") {
  const files = [...input.files];
  const mode = input.dataset.upload;
  input.value = "";
  if (files.length) uploadFiles(files, mode, mode === "page" ? recId : "");
}

/** mode: "one" (kamerayla tek çekim), "many" (galeri / dosya / sürükle-bırak), "page" (kağıda sayfa ekle) */
async function uploadFiles(files, mode, recId = "") {
  const examId = state.exam?.id;  // fotoğraflar küçültülürken başka sınava geçilse bile doğru sınava gitsin
  if (!examId) return;
  files = files.filter((f) => isPdf(f) || f.type.startsWith("image/") || /\.(jpe?g|png|heic|webp)$/i.test(f.name));
  if (!files.length) return toast("Yalnızca fotoğraf ve PDF dosyaları eklenebilir", { err: true });
  // çok sayfalı sınavda galeriden tek fotoğraf: sayfalar tek tek çekilmiş gibi birleştirilsin
  if (mode === "many" && files.length === 1 && !isPdf(files[0]) && pagesPer() > 1) mode = "one";
  // çekim sırasına koy: dosya adındaki sayı / tarih, yoksa değişiklik zamanı
  files.sort((a, b) => natural.compare(a.name, b.name) || a.lastModified - b.lastModified);
  const fd = new FormData();
  if (mode === "page") fd.append("kayit_id", recId);
  else if (mode === "one") fd.append("oturum", browserSession); // tek tek çekilen sayfalar sayfa sayısına göre birleşir
  else {
    const pageCount = files.some(isPdf) || files.length > 1 ? await choose(files) : "ayri";
    if (!pageCount) return;
    fd.append("gruplama", pageCount);
  }
  const side = mode !== "page" && sheetsPer() > 1 ? 4096 : 2400;  // çoklu kağıtta her kağıt yeterli çözünürlükte kalsın
  for (const f of files) fd.append("dosyalar", isPdf(f) ? f : await shrink(f, side));
  toast(`Gönderiliyor (${files.length} dosya)…`, { ms: 0 });
  try {
    const r = await api(`/api/sinavlar/${examId}/kagit`, { method: "POST", body: fd });
    const extra = (r.atlanan ? ` · ${r.atlanan} dosya zaten eklenmişti, atlandı` : "") +
      (r.hatali?.length ? ` · ${r.hatali.length} dosya okunamadı` : "");
    if (mode === "page") toast("Sayfa eklendi" + extra);
    else if (mode === "one") {
      const o = r.oturum || {};
      toast((o.acik_sayfa ? `Sayfa ${o.acik_sayfa}/${o.sayfa_sayisi} alındı. Aynı öğrencinin sonraki sayfasını ekleyin.`
        : "Kağıt gönderildi. Sıradaki kağıda geçebilirsiniz.") + extra);
    } else toast(`${r.sayfa} sayfa · ${r.kayitlar.length} kağıt sıraya alındı${extra}`, { ms: r.hatali?.length ? 8000 : 3000 });
    if (r.hatali?.length) setTimeout(() => toast(r.hatali.join(" · "), { err: true, ms: 9000 }), 3000);
    if (r.esit_bolunen?.length) setTimeout(() => toast(`${r.esit_bolunen.length} fotoğrafta kağıtlar ayrı ayrı seçilemedi, eşit parçalara bölündü. ` +
      "Kağıtlar arasında boşluk bırakıp koyu bir zeminde çekerseniz daha temiz ayrılır.", { ms: 9000 }), r.hatali?.length ? 6000 : 3000);
    if (state.exam?.id === examId) reload();
    refreshStatus();
  } catch (err) { toastErr(new Error("Gönderilemedi: " + err.message)); }
}

/** Birden çok fotoğraf ya da PDF: nasıl öğrencilere ayrılsın? */
function choose(files) {
  const per = pagesPer();
  const pdfs = files.filter(isPdf).length;
  const what = pdfs ? `${files.length} dosya${pdfs ? ` (${pdfs} PDF)` : ""}`
    : `${files.length} fotoğraf${sheetsPer() > 1 ? ` (her birinde ${sheetsPer()} kağıt)` : ""}`;
  const opts = [];
  if (per > 1) opts.push(["sayfa", `Her ${per} sayfa bir öğrenci`, `Bu sınav ${per} sayfa. Sayfalar sırayla ${per}'er ${per}'er öğrencilere ayrılır.`]);
  opts.push(["ayri", "Her sayfa ayrı bir öğrenci", "Tek sayfalık kağıtlar"]);
  if (!pdfs || files.length > 1) opts.push(["tek", "Hepsi tek öğrencinin sayfaları", "Bir öğrencinin çok sayfalı kağıdı"]);
  return new Promise((resolve) => {
    openModal(`${head(`${what} seçildi`)}<div class="m-body choice">
      ${opts.map(([v, t, d], i) => `<button class="btn ${i === 0 ? "primary-soft" : ""}" data-v="${v}">${t}<span>${d}</span></button>`).join("")}
      ${pdfs ? `<p class="hint">PDF'ler sayfalarına ayrılır. Fotokopi makinesinden tarattığınız destenin sırası korunur.</p>` : ""}
    </div>`);
    let answered = false;
    const obs = new MutationObserver(() => { if ($modal.hidden && !answered) { obs.disconnect(); resolve(null); } });
    obs.observe($modal, { attributes: true, attributeFilter: ["hidden"] });
    $modalBody.querySelectorAll("[data-v]").forEach((b) => (b.onclick = () => { answered = true; obs.disconnect(); closeModal(); resolve(b.dataset.v); }));
  });
}

// pencereye sürükle-bırak (bilgisayarda)
let dragDepth = 0;
document.addEventListener("dragenter", (e) => {
  if (!state.exam || !e.dataTransfer?.types?.includes("Files")) return;
  e.preventDefault();
  if (dragDepth++ === 0) document.body.classList.add("dropping");
});
document.addEventListener("dragover", (e) => { if (e.dataTransfer?.types?.includes("Files")) e.preventDefault(); });
document.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove("dropping"); } });
document.addEventListener("drop", (e) => {
  dragDepth = 0;
  document.body.classList.remove("dropping");
  if (!e.dataTransfer?.files?.length) return;
  e.preventDefault();  // pencere bırakılan dosyayı açıp uygulamadan çıkmasın
  if (!state.exam) return toast("Önce bir sınav açın, sonra dosyaları bırakın", { err: true });
  uploadFiles([...e.dataTransfer.files], "many");
});

// ------------------------------------------------------------ şablon düzenleyici

async function templateDialog() {
  const ex = state.exam;
  const per = pagesPer();
  const saved = ex.sablon?.kutular?.length ? ex.sablon : null;
  // her şablon sayfası için kaynak fotoğraf: kayıtlı şablon > sırası uyan ilk kağıt fotoğrafı
  const pages = Array.from({ length: Math.max(per, saved?.sayfalar.length || 0) }, (_, i) => saved?.sayfalar[i] ||
    ex.kayitlar.find((r) => r.resimler?.length > i && (per === 1 || r.resimler.length >= per))?.resimler[i] || null);
  const boxes = {};
  (saved?.kutular || []).forEach((b) => (boxes[b.soru] = { ...b }));
  const items = [{ soru: 0, ad: "Ad / numara", opt: true },
    ...ex.tam_puanlar.map((m, i) => ({ soru: i + 1, ad: `${i + 1}. soru`, alt: `/${fmt(m)}` })),
    { soru: -1, ad: "Kağıdın toplamı", opt: true }];
  let page = 0;
  let current = items.find((it) => it.soru > 0 && !boxes[it.soru])?.soru ?? 1;

  openModal(`${head("Şablon: puanların yazıldığı yerleri işaretleyin")}
    <div class="tpl">
      <div class="tpl-left">
        <div class="tpl-pages" id="tpl-pages"></div>
        <div class="tpl-stage" id="tpl-stage"></div>
        <div class="tpl-src" id="tpl-src"></div>
      </div>
      <div class="tpl-side">
        <p class="hint" style="margin-top:0">Bir soru seçip fotoğrafta o sorunun <b>puanının yazıldığı alanı</b> sürükleyerek kutu içine alın.
        Kutu, farklı kağıtlarda puanın biraz kayabileceğini düşünerek <b>biraz geniş</b> olsun; ama komşu sorunun puanını içermesin.
        Bir kutu çizince sıradaki soruya geçilir.</p>
        <div class="tpl-items" id="tpl-items"></div>
        <p class="hint">“Ad / numara” ve “Kağıdın toplamı” isteğe bağlıdır; işaretlenmezse sayfanın üst kısmından okunur.
        Şablonu, mümkünse doldurulmamış boş bir kağıdın fotoğrafıyla yapın.</p>
      </div>
    </div>
    <div class="m-foot">
      ${saved ? `<button class="btn danger" id="tpl-del" style="margin-right:auto">Şablonu kaldır</button>` : ""}
      <button class="btn" data-close>Vazgeç</button>
      <button class="btn primary" id="tpl-save">Kaydet</button>
    </div>`, { wide: true });

  const $pages = document.getElementById("tpl-pages");
  const $stage = document.getElementById("tpl-stage");
  const $src = document.getElementById("tpl-src");
  const $items = document.getElementById("tpl-items");
  const url = (name) => `/api/sinavlar/${ex.id}/resim/${name}`;

  function renderItems() {
    $items.innerHTML = items.map((it) => {
      const b = boxes[it.soru];
      return `<button class="tpl-item ${it.soru === current ? "on" : ""} ${b ? "set" : ""}" data-soru="${it.soru}">
        <span>${esc(it.ad)} <small>${esc(it.alt || "")}</small></span>
        <small>${b ? (pages.length > 1 ? `sayfa ${b.sayfa + 1} ✓` : "✓") : it.opt ? "isteğe bağlı" : "işaretlenmedi"}</small></button>`;
    }).join("");
    $items.querySelectorAll("[data-soru]").forEach((el) => (el.onclick = () => {
      current = +el.dataset.soru;
      if (boxes[current] && boxes[current].sayfa !== page) { page = boxes[current].sayfa; renderPage(); }
      renderItems(); drawBoxes();
    }));
  }

  function renderPage() {
    $pages.innerHTML = pages.length > 1
      ? pages.map((_, i) => `<button class="${i === page ? "on" : ""}" data-p="${i}">Sayfa ${i + 1}</button>`).join("") : "";
    $pages.querySelectorAll("[data-p]").forEach((b) => (b.onclick = () => { page = +b.dataset.p; renderPage(); }));
    $stage.innerHTML = pages[page]
      ? `<div class="tpl-canvas" id="tpl-canvas"><img src="${url(pages[page])}" alt="" draggable="false"></div>`
      : `<div class="empty">Bu sayfa için fotoğraf yok. Aşağıdan bir kağıt seçin ya da boş kağıdın fotoğrafını yükleyin.</div>`;
    const choices = [...new Set(ex.kayitlar.map((r) => r.resimler?.[page]).filter(Boolean))].slice(0, 12);
    $src.innerHTML = `<span class="small muted">Sayfa ${page + 1} için fotoğraf:</span>
      ${choices.map((nm) => `<img class="thumb ${nm === pages[page] ? "sel" : ""}" data-nm="${nm}" src="${url(nm)}" alt="">`).join("")}
      <label class="btn small">${ic("image")}Boş kağıt fotoğrafı yükle<input type="file" accept="image/*,application/pdf" id="tpl-upload" hidden></label>`;
    $src.querySelectorAll("[data-nm]").forEach((im) => (im.onclick = () => { pages[page] = im.dataset.nm; renderPage(); }));
    document.getElementById("tpl-upload").onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("dosya", isPdf(f) ? f : await shrink(f));
      try {
        const r = await api(`/api/sinavlar/${ex.id}/sablon/foto`, { method: "POST", body: fd });
        r.resimler.forEach((nm, k) => { if (page + k < pages.length) pages[page + k] = nm; });
        renderPage();
      } catch (err) { toastErr(err); }
    };
    const canvas = document.getElementById("tpl-canvas");
    if (canvas) {
      const img = canvas.querySelector("img");
      const fit = () => {
        // fotoğrafı alana sığdır: genişliğe, geniş ekranda ayrıca yüksekliğe göre
        if (!img.naturalWidth) return;
        const availW = $stage.clientWidth - 16;
        const availH = matchMedia("(min-width: 860px)").matches ? $stage.clientHeight - 16 : Infinity;
        const k = Math.min(availW / img.naturalWidth, availH / img.naturalHeight, 1);
        img.style.width = Math.round(img.naturalWidth * k) + "px";
        img.style.height = Math.round(img.naturalHeight * k) + "px";
      };
      img.complete ? fit() : img.addEventListener("load", fit);
      window.onresize = () => document.getElementById("tpl-canvas") && fit();
      setupDrawing(canvas);
    }
    drawBoxes();
  }

  function drawBoxes() {
    const canvas = document.getElementById("tpl-canvas");
    if (!canvas) return;
    canvas.querySelectorAll(".tpl-box").forEach((b) => b.remove());
    Object.values(boxes).filter((b) => b.sayfa === page).forEach((b) => {
      const el = document.createElement("div");
      el.className = "tpl-box" + (b.soru === current ? " on" : "");
      Object.assign(el.style, { left: b.x * 100 + "%", top: b.y * 100 + "%", width: b.w * 100 + "%", height: b.h * 100 + "%" });
      el.innerHTML = `<span>${b.soru === 0 ? "Ad/No" : b.soru === -1 ? "Toplam" : "S" + b.soru}</span>`;
      canvas.appendChild(el);
    });
  }

  function setupDrawing(canvas) {
    let start = null, ghost = null;
    const pos = (e) => {
      const r = canvas.getBoundingClientRect();
      return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
    };
    canvas.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      canvas.setPointerCapture(e.pointerId);
      start = pos(e);
      ghost = document.createElement("div");
      ghost.className = "tpl-box on";
      canvas.appendChild(ghost);
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!start) return;
      const p = pos(e);
      Object.assign(ghost.style, { left: Math.min(start.x, p.x) * 100 + "%", top: Math.min(start.y, p.y) * 100 + "%",
        width: Math.abs(p.x - start.x) * 100 + "%", height: Math.abs(p.y - start.y) * 100 + "%" });
    });
    const finish = (e) => {
      if (!start) return;
      const p = pos(e);
      const b = { soru: current, sayfa: page, x: Math.min(start.x, p.x), y: Math.min(start.y, p.y),
        w: Math.abs(p.x - start.x), h: Math.abs(p.y - start.y) };
      start = null;
      ghost?.remove();
      if (b.w < 0.01 || b.h < 0.01) return; // tıklama, kutu değil
      boxes[current] = b;
      const next = items.find((it) => it.soru > 0 && !boxes[it.soru]);
      if (next) current = next.soru;
      renderItems(); drawBoxes();
    };
    canvas.addEventListener("pointerup", finish);
    canvas.addEventListener("pointercancel", () => { start = null; ghost?.remove(); });
  }

  renderItems();
  renderPage();

  document.getElementById("tpl-del")?.addEventListener("click", async () => {
    try { await api(`/api/sinavlar/${ex.id}/sablon`, { method: "DELETE" }); closeModal(); showExam(ex.id); toast("Şablon kaldırıldı"); }
    catch (err) { toastErr(err); }
  });
  document.getElementById("tpl-save").onclick = async () => {
    const missing = items.filter((it) => it.soru > 0 && !boxes[it.soru]).map((it) => it.soru);
    if (missing.length) return toast(`İşaretlenmemiş soru: ${missing.join(", ")}`, { err: true });
    if (pages.some((p) => !p)) return toast("Her sayfa için bir fotoğraf seçin", { err: true });
    try {
      await api(`/api/sinavlar/${ex.id}/sablon`, json("PUT", { sayfalar: pages, kutular: Object.values(boxes) }));
      closeModal();
      const readable = state.exam.kayitlar.filter((r) => ["tamam", "hata"].includes(r.durum) && !r.onaylandi && r.resimler?.length);
      if (readable.length && await ask("Şablon kaydedildi",
        `Okunmuş ${readable.length} kağıt şablonla yeniden okunsun mu? Elle yaptığınız düzeltmeler bu kağıtlarda silinir; onayladıklarınız korunur.`,
        [{ label: "Yeniden oku", value: true }])) {
        await api(`/api/sinavlar/${ex.id}/yeniden-oku`, { method: "POST" });
      }
      showExam(ex.id);
      toast("Şablon kaydedildi. Kağıtlar şablonla okunacak.");
    } catch (err) { toastErr(err); }
  };
}

function missingDialog() {
  const list = state.missing || [];
  openModal(`${head("Kağıdı olmayan öğrenciler")}<div class="m-body">
    ${list.length ? `<table class="list"><tbody>${list.map((s) => `<tr><td style="width:70px">${esc(s.no)}</td><td>${esc(s.ad)}</td></tr>`).join("")}</tbody></table>
      <p class="hint">Bu öğrencilerin kağıdı henüz eklenmedi ya da numarası okunamadı. Excel'de boş satır olarak yer alırlar.</p>`
    : `<p class="muted" style="margin:0">Listedeki bütün öğrencilerin kağıdı var.</p>`}
  </div>`);
}

/** Telefon fotoğrafları çok büyük olabilir; göndermeden önce küçült (Wi-Fi'yi hızlandırır). */
async function shrink(file, maxSide = 2400) {
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
    if (scale === 1 && file.size < 3e6) return file;
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * scale);
    c.height = Math.round(bmp.height * scale);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise((res) => c.toBlob(res, "image/jpeg", 0.9));
    return blob ? new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }) : file;
  } catch {
    return file;
  }
}

// ------------------------------------------------------------ Excel, liste, diğer

async function downloadExcel() {
  const pending = state.exam.kayitlar.filter((r) => r.durum !== "tamam").length;
  if (pending && !(await ask("Excel", `${pending} kağıt henüz okunmadı ya da okunamadı. Excel'de bu kağıtlar puansız görünecek. Yine de indirilsin mi?`,
    [{ label: "İndir", value: true }]))) return;
  const desk = window.pywebview?.api?.excel_kaydet;
  if (!desk) { location.href = `/api/sinavlar/${state.exam.id}/excel`; return; }
  try {
    const path = await window.pywebview.api.excel_kaydet(state.exam.id);
    if (path) toast(`Kaydedildi: ${path.split(/[\\/]/).pop()}`, { action: "Klasörü aç", onAction: () => window.pywebview.api.klasoru_ac(path) });
  } catch (err) { toastErr(err); }
}

function rosterDialog() {
  const roster = state.exam.ogrenci_listesi;
  openModal(`${head("Sınıf listesi")}
    <form id="roster-form">
      <div class="m-body">
        <p class="hint" style="margin:0 0 10px">Her satıra numara ve ad soyad yazın ya da e-Okul / Excel'den kopyalayıp yapıştırın. Sıra numarası sütunu varsa sorun olmaz.
        Liste, el yazısıyla okunan ad ve numaraları düzeltmek için kullanılır.</p>
        <textarea name="metin" placeholder="247 Deniz Kaya&#10;412 Ayşe Demir">${esc(roster.map((s) => `${s.no} ${s.ad}`).join("\n"))}</textarea>
        <label class="f" style="margin-top:10px">veya dosya (.xlsx, .csv)<input type="file" name="dosya" accept=".xlsx,.csv,.txt" style="padding:5px 8px"></label>
      </div>
      <div class="m-foot">${roster.length ? `<button type="button" class="btn danger" id="roster-clear" style="margin-right:auto">Listeyi kaldır</button>
        <span class="small muted" style="align-self:center">${roster.length} öğrenci</span>` : ""}
        <button type="button" class="btn" data-close>Vazgeç</button><button class="btn primary">Kaydet</button></div>
    </form>`);
  document.getElementById("roster-clear")?.addEventListener("click", async () => {
    const fd = new FormData();
    fd.append("temizle", "true");
    try {
      state.exam = await api(`/api/sinavlar/${state.exam.id}/liste`, { method: "POST", body: fd });
      closeModal(); ensureRosterDatalist(); renderSheet(true); toast("Sınıf listesi kaldırıldı");
    } catch (err) { toastErr(err); }
  });
  document.getElementById("roster-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    if (!e.target.dosya.files.length) fd.delete("dosya");
    try {
      const fresh = await api(`/api/sinavlar/${state.exam.id}/liste`, { method: "POST", body: fd });
      state.exam = fresh;
      closeModal();
      ensureRosterDatalist();
      renderSheet(true);
      toast(`${fresh.ogrenci_listesi.length} öğrenci kaydedildi`);
    } catch (err) { toastErr(err); }
  });
}

function moreDialog() {
  const clean = state.exam.kayitlar.filter((r) => r.durum === "tamam" && !r.onaylandi && !hasWarn(r));
  const failed = state.exam.kayitlar.filter((r) => r.durum === "hata").length;
  openModal(`${head("Diğer işlemler")}<div class="m-body choice">
      ${failed ? `<button class="btn" id="m-retry">Okunamayan kağıtları yeniden oku<span>${failed} kağıt yeniden sıraya alınır</span></button>` : ""}
      <button class="btn" id="m-copy">Başka şube için kopyala<span>Aynı sorular ve puanlarla, örneğin 10-A için yeni sınav</span></button>
      <button class="btn" id="m-confirm" ${clean.length ? "" : "disabled"}>Uyarısız kağıtları onayla<span>${clean.length} kağıt onaylanır</span></button>
      <button class="btn" id="m-imgs">Kağıt fotoğraflarını sil<span>Puanlar ve isimler kalır. Excel'i aldıktan sonra önerilir (KVKK).</span></button>
      <button class="btn danger" id="m-del">Sınavı sil<span>Tüm puanlar ve fotoğraflar kalıcı olarak silinir.</span></button>
    </div>`);
  document.getElementById("m-copy").onclick = () => examDialog(state.exam, { copy: true });
  document.getElementById("m-retry")?.addEventListener("click", async () => {
    closeModal();
    try {
      const r = await api(`/api/sinavlar/${state.exam.id}/yeniden-oku?sadece_hatali=true`, { method: "POST" });
      toast(`${r.kayitlar.length} kağıt yeniden sıraya alındı`);
    } catch (err) { toastErr(err); }
    reload();
  });
  document.getElementById("m-confirm").onclick = async () => {
    closeModal();
    let done = 0, errors = 0;
    let changed = 0;
    for (const r of clean) {
      // pencere açıldıktan sonra uyarı kazanmış bir kağıt (ör. aynı numara başka kağıtta da okundu) onaylanmasın
      try { await api(`/api/sinavlar/${state.exam.id}/kagit/${r.id}`, json("PATCH", { onaylandi: true, yalniz_uyarisiz: true })); done++; }
      catch (err) { if (/uyarı/.test(err.message)) changed++; else errors++; }
    }
    const parts = [`${done} kağıt onaylandı`];
    if (changed) parts.push(`${changed} kağıt bu arada uyarı aldığı için onaylanmadı`);
    if (errors) parts.push(`${errors} kağıt onaylanamadı`);
    toast(parts.join(", "), { err: !!(errors || changed) });
    reload();
  };
  document.getElementById("m-imgs").onclick = async () => {
    if (!(await ask("Fotoğrafları sil", "Bu sınavın tüm kağıt fotoğrafları silinsin mi? Puanlar ve isimler kalır.", [{ label: "Sil", value: true, cls: "danger" }]))) return;
    try { await api(`/api/sinavlar/${state.exam.id}/resimleri-sil`, { method: "POST" }); toast("Fotoğraflar silindi"); reload(); }
    catch (err) { toastErr(err); }
  };
  document.getElementById("m-del").onclick = async () => {
    if (!(await ask("Sınavı sil", "Sınav, tüm puanlar ve fotoğraflar kalıcı olarak silinsin mi?", [{ label: "Sınavı sil", value: true, cls: "danger" }]))) return;
    try { await api(`/api/sinavlar/${state.exam.id}`, { method: "DELETE" }); location.hash = "#/"; }
    catch (err) { toastErr(err); }
  };
}

route();
