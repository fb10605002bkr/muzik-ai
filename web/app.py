# -*- coding: utf-8 -*-
"""
Müzik AI — Sohbet + Şarkı Üretim Web Uygulaması (MVP)
Kullanıcı AI ile sohbet eder, şarkısını tarif eder, "Üret" der → söz + şarkı üretilir.

Akış:
  /api/chat     -> Ollama ile sohbet (şarkıyı netleştir)
  /api/generate -> sohbetten söz + tarz üret -> ACE-Step ile şarkı -> ses döndür
"""
import os
import re
import json
import time
import uuid
import random
import shutil
import threading
import urllib.request
import subprocess
from flask import Flask, request, jsonify, send_from_directory, render_template

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True   # şablon değişikliği restart'sız görünsün
app.jinja_env.auto_reload = True


@app.after_request
def _sayfa_onbellekleme(resp):
    """HTML sayfası tarayıcıda önbelleğe alınmasın (tasarım değişikliği hemen görünsün)."""
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

# ---- Ayarlar ----
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", OLLAMA_URL.rsplit("/api/", 1)[0] if "/api/" in OLLAMA_URL else "http://127.0.0.1:11434")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gemma2:9b")      # sohbet — kullanıcının okuduğu, iyi Türkçe şart
GEN_MODEL = os.environ.get("GEN_MODEL", "qwen2.5:3b")      # boru hattı iç metin (söz/tarz/görsel tarifi) — hızlı
ACESTEP_DIR = r"C:\Users\FiratBakir\muzik-ai\ACE-Step-1.5"
# Sıcak servis (warm API): model BİR KEZ yüklenir, her şarkıda soğuk başlatma YOK.
ACESTEP_API = os.environ.get("ACESTEP_API", "http://127.0.0.1:8001").rstrip("/")
ACESTEP_MODEL = "acestep-v15-base"     # kalite modeli (kullanıcı seçti)
API_AUDIO_DIR = os.path.join(ACESTEP_DIR, ".cache", "acestep", "tmp", "api_audio")
INFERENCE_STEPS = 40                    # base kalite (kullanıcı seçti; enstrüman netliği için)
GUIDANCE_SCALE = 7.0                    # base CFG (turbo'da 1.0)

# ---- KAPAK GÖRSELİ (SDXL-Turbo, ayrı süreç, düşük VRAM) ----
GORSEL_PY = os.path.join(ACESTEP_DIR, ".venv", "Scripts", "python.exe")  # diffusers burada
GORSEL_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gorsel_uret.py")
COVERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "covers")

# ---- KULLANICIYA ÖZEL SES ----
# Sabit seed + sabit vokal stili = hep aynı şarkıcı. Her kullanıcıya bir kez atanır.
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json")
_users_lock = threading.Lock()
VOICE_STYLES = [
    "warm male vocal", "deep male vocal", "husky male vocal", "soft male vocal",
    "bright female vocal", "soft female vocal", "powerful female vocal", "gentle female vocal",
]

# Ses kütüphanesi (kullanıcı bunlardan birini SEÇER). Her ses sabit seed+stil = tutarlı şarkıcı.
PRESET_VOICES = [
    {"id": "ses1", "name": "Sıcak Erkek", "gender": "erkek", "vocal_style": "warm male vocal", "seed": 101},
    {"id": "ses2", "name": "Derin Erkek", "gender": "erkek", "vocal_style": "deep male vocal", "seed": 202},
    {"id": "ses3", "name": "Boğuk Erkek", "gender": "erkek", "vocal_style": "husky male vocal", "seed": 303},
    {"id": "ses4", "name": "Parlak Kadın", "gender": "kadın", "vocal_style": "bright female vocal", "seed": 404},
    {"id": "ses5", "name": "Yumuşak Kadın", "gender": "kadın", "vocal_style": "soft female vocal", "seed": 505},
    {"id": "ses6", "name": "Güçlü Kadın", "gender": "kadın", "vocal_style": "powerful female vocal", "seed": 606},
]
PRESET_BY_ID = {v["id"]: v for v in PRESET_VOICES}
PREVIEWS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "previews")
PREVIEW_LYRICS = "[verse]\nBu benim sesim, dinle beni\nSeninle güzel şarkılar"

# ---- KREDİ / ÖDEME ----
ODEME_AKTIF = False      # ŞİMDİLİK KAPALI: kredi kontrolü/düşme devre dışı (kod arka planda hazır)
BASLANGIC_KREDI = 10     # yeni kullanıcıya ücretsiz kredi
SARKI_MALIYET = 1        # 1 şarkı = 1 kredi
KAPAK_MALIYET = 1        # 1 kapak = 1 kredi


def _users_yukle():
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _users_kaydet(u):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(u, f, ensure_ascii=False, indent=2)


def _kullanici_hazirla(users, user_id):
    """users dict'inde user_id kaydını garanti et (ses + kredi). Değişiklik olduysa True."""
    degisti = False
    if user_id not in users:
        users[user_id] = {
            "seed": random.randint(1, 2**31 - 1),
            "vocal_style": random.choice(VOICE_STYLES),
            "voice_id": None,          # kullanıcı henüz ses SEÇMEDİ (null); seçince dolar
            "credits": BASLANGIC_KREDI,
        }
        degisti = True
    if "credits" not in users[user_id]:   # eski kayıtlar için geriye dönük
        users[user_id]["credits"] = BASLANGIC_KREDI
        degisti = True
    if "voice_id" not in users[user_id]:
        users[user_id]["voice_id"] = None
        degisti = True
    return degisti


def kullanici_sesi(user_id):
    """Kullanıcının SABİT sesini getir; yoksa rastgele ata ve kalıcı sakla."""
    with _users_lock:
        users = _users_yukle()
        if _kullanici_hazirla(users, user_id):
            _users_kaydet(users)
        return users[user_id]


def kredi_getir(user_id):
    with _users_lock:
        users = _users_yukle()
        if _kullanici_hazirla(users, user_id):
            _users_kaydet(users)
        return int(users[user_id].get("credits", 0))


def kredi_dus(user_id, miktar):
    """Yeterliyse krediyi düş ve yeni bakiyeyi döndür; yetersizse None."""
    with _users_lock:
        users = _users_yukle()
        _kullanici_hazirla(users, user_id)
        bakiye = int(users[user_id].get("credits", 0))
        if bakiye < miktar:
            return None
        users[user_id]["credits"] = bakiye - miktar
        _users_kaydet(users)
        return users[user_id]["credits"]


def kredi_ekle(user_id, miktar):
    with _users_lock:
        users = _users_yukle()
        _kullanici_hazirla(users, user_id)
        users[user_id]["credits"] = int(users[user_id].get("credits", 0)) + int(miktar)
        _users_kaydet(users)
        return users[user_id]["credits"]

CHAT_SYSTEM = (
    "Sen samimi bir müzik asistanısın. Kullanıcıyla kısa sohbet ederek nasıl bir şarkı "
    "istediğini öğrenirsin: konu, tarz (pop/rock/rap...), ruh hali. Kısa ve Türkçe konuş, "
    "tek seferde 1-2 soru sor. Yeterli bilgi olduğunda kullanıcıya \"Aşağıdaki 'Şarkıyı Üret' "
    "butonuna basabilirsin\" de. Şarkı sözü yazma; onu üretim aşaması halleder."
)


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()


def gemini_chat(messages, system=None, timeout=30):
    """Google Gemini API ile bulutta sohbet (Ollama gerektirmez)."""
    key = GEMINI_API_KEY
    if not key:
        raise ValueError("GEMINI_API_KEY bulunamadı")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
    contents = []
    for m in messages:
        role = "user" if m.get("role") == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})
    payload = {"contents": contents}
    if system:
        payload["system_instruction"] = {"parts": [{"text": system}]}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read().decode("utf-8"))
        return res["candidates"][0]["content"]["parts"][0]["text"].strip()


def ollama_chat(messages, system=None, model=None, timeout=240, keep_alive="5m", num_predict=400):
    if GEMINI_API_KEY:
        try:
            return gemini_chat(messages, system=system)
        except Exception as e:
            print(f"Gemini API hatası, Ollama deneniyor: {e}")

    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    payload = json.dumps({
        "model": model or GEN_MODEL, "messages": msgs, "stream": False, "keep_alive": keep_alive,
        "options": {"num_predict": num_predict, "temperature": 0.7},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=payload, headers=TUNNEL_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))["message"]["content"].strip()


def ollama_bosalt(model=None):
    """Ollama modelini/modellerini bellekten at. model=None → ikisini de (chat+gen)."""
    for m in ([model] if model else [CHAT_MODEL, GEN_MODEL]):
        try:
            payload = json.dumps({"model": m, "keep_alive": 0}).encode("utf-8")
            req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/generate",
                                         data=payload, headers=TUNNEL_HEADERS)
            urllib.request.urlopen(req, timeout=30).read()
        except Exception:
            pass


def _vram_used_mib():
    """GPU'da kullanılan VRAM (MiB). nvidia-smi yoksa -1 döner."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return -1


def ollama_yer_ac():
    """
    Base model sıcak servis GPU'da duruyor. Üretim VRAM'i ~6 GB'a çıkarır;
    Ollama'nın 2.2 GB'ı da açık kalırsa 8 GB taşar. Bu yüzden üretimden önce
    SADECE Ollama'yı bellekten atarız (base model warm kalır).
    """
    ollama_bosalt()
    time.sleep(2)
    # Artık kalmış Ollama runner'ı VRAM tutuyorsa temizle (hayalet süreç önlemi)
    try:
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"],
                       capture_output=True, timeout=15)
    except Exception:
        pass
    time.sleep(1)


# ---- Sıcak servis (ACE-Step warm API) istemcisi ----
def _api_post(path, payload, timeout=1800):
    req = urllib.request.Request(ACESTEP_API + path, data=json.dumps(payload).encode("utf-8"),
                                 headers=TUNNEL_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _api_get(path, timeout=10):
    req = urllib.request.Request(ACESTEP_API + path, headers={"Bypass-Tunnel-Reminder": "true", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


_base_hazir = False  # bu Flask sürecinde base bir kez yüklendi mi?


def acestep_base_yukle():
    """
    Base modeli sıcak servise BİR KEZ yükle. (health'in loaded_model alanı
    per-request model geçişini yansıtmadığı için ona güvenmiyoruz; süreç
    başına bayrak tutuyoruz. Böylece her üretimde gereksiz reload OLMAZ.)
    """
    global _base_hazir
    if _base_hazir:
        return
    _api_post("/v1/init", {"model_name": ACESTEP_MODEL}, timeout=300)
    _base_hazir = True


def acestep_uret(caption, lyrics, duration, enstrumantal, seed=None, timeout=600):
    """
    Sıcak servise üretim işi gönder, dosya-izleme ile sonucu bekle.
    (query_result yerine yeni WAV dosyasını izliyoruz — daha güvenilir.)
    seed verilirse SABİT ses (kullanıcıya özel); verilmezse rastgele.
    """
    os.makedirs(API_AUDIO_DIR, exist_ok=True)
    onceki = set(f for f in os.listdir(API_AUDIO_DIR) if f.lower().endswith(".wav"))
    payload = {
        "caption": caption, "lyrics": lyrics,
        "duration": duration, "audio_duration": duration,
        "inference_steps": INFERENCE_STEPS, "batch_size": 1,
        "instrumental": enstrumantal,
        "guidance_scale": GUIDANCE_SCALE, "shift": 3.0,
        "audio_format": "wav", "model": ACESTEP_MODEL,
        "vocal_language": "tr",
    }
    if seed is not None:
        payload["use_random_seed"] = False
        payload["seed"] = int(seed)
    else:
        payload["use_random_seed"] = True
    _api_post("/release_task", payload, timeout=60)
    t0 = time.time()
    while time.time() - t0 < timeout:
        yeni = [f for f in os.listdir(API_AUDIO_DIR)
                if f.lower().endswith(".wav") and f not in onceki]
        if yeni:
            yol = os.path.join(API_AUDIO_DIR, yeni[0])
            boyut = os.path.getsize(yol)
            time.sleep(1.5)
            if boyut > 0 and os.path.getsize(yol) == boyut:  # yazımı bitti
                return yeni[0]
        time.sleep(2)
    return None


def kapak_uret(image_prompt, timeout=300):
    """SDXL-Turbo (ayrı süreç, düşük VRAM) ile kapak görseli üret. Dosya adı veya None."""
    if not image_prompt:
        return None
    os.makedirs(COVERS_DIR, exist_ok=True)
    fname = f"{uuid.uuid4().hex}.png"
    out = os.path.join(COVERS_DIR, fname)
    prompt = (f"album cover art, {image_prompt}, cinematic lighting, highly detailed, "
              f"no text, no watermark, no words")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        subprocess.run([GORSEL_PY, GORSEL_SCRIPT, "--prompt", prompt, "--out", out],
                       env=env, timeout=timeout)
    except Exception:
        return None
    return fname if os.path.exists(out) else None


def _port_pid(port):
    """Belirtilen portu dinleyen PID (yoksa None)."""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10)
        for line in out.stdout.splitlines():
            if f":{port} " in line and "LISTENING" in line:
                return int(line.split()[-1])
    except Exception:
        pass
    return None


def acestep_servis_durdur():
    """ACE-Step sıcak servisini durdur (RAM'i boşalt → SDXL kapak için yer aç)."""
    global _base_hazir
    _base_hazir = False
    pid = _port_pid(8001)
    if pid:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=15)
        except Exception:
            pass
    time.sleep(2)


def acestep_servis_baslat():
    """ACE-Step servisini arka planda (detached) başlat — sonraki şarkı için hazır olsun."""
    if _port_pid(8001):
        return  # zaten çalışıyor
    env = dict(os.environ, ACESTEP_INIT_LLM="false", ACESTEP_OFFLOAD_TO_CPU="true",
               ACESTEP_API_PORT="8001", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    # CREATE_NO_WINDOW → arka planda GİZLİ başlar (CMD penceresi açılmaz)
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen([GORSEL_PY, "-m", "acestep.api_server", "--port", "8001"],
                         cwd=ACESTEP_DIR, env=env, creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL)
    except Exception:
        pass


def _acestep_ayakta():
    try:
        _api_get("/health", timeout=3)
        return True
    except Exception:
        return False


def acestep_servis_hazir_ol(timeout=120):
    """ACE-Step servisi ayakta değilse OTOMATİK başlat ve hazır olana kadar bekle (kendi kendini iyileştirme)."""
    global _base_hazir
    if _acestep_ayakta():
        return True
    _base_hazir = False           # yeni servis → base yeniden yüklenecek
    acestep_servis_baslat()
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _acestep_ayakta():
            return True
        time.sleep(2)
    return False


def _sure_ayikla(text, varsayilan=60.0, azami=240.0):
    """Metinden şarkı süresini (saniye) çıkar: '2-3 dakika', '2 dk', '120 saniye'..."""
    t = text.lower()
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*(dakika|dak|dk|min)", t)
    if m:
        return min((int(m.group(1)) + int(m.group(2))) / 2 * 60, azami)
    m = re.search(r"(\d+)\s*(dakika|dakka|dak|dk|min)", t)
    if m:
        return min(int(m.group(1)) * 60, azami)
    m = re.search(r"(\d+)\s*(saniye|sn|sec)", t)
    if m:
        return min(float(m.group(1)), azami)
    return varsayilan


def _enstrumantal_mi(text):
    """Kullanıcı sözsüz/enstrümantal mı istedi? (çeşitli Türkçe kalıplarını yakalar)"""
    t = text.lower()
    if any(k in t for k in (
            "enstrüman", "enstruman", "instrumental", "sözsüz", "sozsuz",
            "vokalsiz", "vokalsız", "vokal yok", "vokal olmayan",
            "sadece müzik", "sadece muzik", "sadece enstrüman", "müzik olsun söz")):
        return True
    # "söz / vokal / şarkı sözü ... olmasın / olmayan / yok / istemiyorum / koyma"
    if re.search(r"(söz|soz|vokal|lyric)\w*\s+(olmasın|olmasin|olmayan|olmaz|"
                 r"yok|istemiyorum|istemem|istemiyoruz|koyma|koymay|gerekmez|"
                 r"gerekmiyor|eklenmesin|ekleme|istemi)", t):
        return True
    return False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    messages = request.json.get("messages", [])
    try:
        reply = ollama_chat(messages, CHAT_SYSTEM, model=CHAT_MODEL)
    except Exception as e:
        return jsonify({"error": f"Sohbet hatası: {e}"}), 500
    return jsonify({"reply": reply})


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.json or {}
    brief = (data.get("brief") or "").strip()
    if not brief:
        return jsonify({"error": "Şarkı tarifi boş"}), 400
    user_id = (data.get("user_id") or "anon").strip() or "anon"
    ses = kullanici_sesi(user_id)   # kullanıcının SABİT sesi (seed + vokal stili)

    # KREDİ KONTROLÜ (üretimden önce) — ödeme kapalıysa atlanır
    if ODEME_AKTIF and kredi_getir(user_id) < SARKI_MALIYET:
        return jsonify({"error": "Yetersiz kredi. Şarkı üretmek için kredi al.",
                        "need_credits": True, "credits": kredi_getir(user_id)}), 402

    # 0) Kullanıcının isteğini çöz: enstrümantal mı? kaç saniye?
    enstrumantal = _enstrumantal_mi(brief)
    duration = _sure_ayikla(brief)

    # 1) Söz + tarz üret. Önce ağır sohbet modelini (gemma2) boşalt → hızlı 3b'ye yer aç.
    ollama_bosalt(CHAT_MODEL)
    try:
        if enstrumantal:
            lyrics = "[inst]"
            caption = ollama_chat([{"role": "user", "content":
                f"Şu şarkı isteği için İngilizce müzik tarz etiketleri yaz (virgülle ayrılmış, "
                f"kısa, örn: turkish pop, calm, piano). Şarkı instrumental, no vocals. "
                f"SADECE etiketleri ver.\n\nİstek: {brief}"}])
            seed = None  # enstrümantalde ses yok
        else:
            lyrics = ollama_chat([{"role": "user", "content":
                f"Şu isteğe uygun bir Türkçe şarkı sözü yaz. Yapı: [verse] ile 1-2 kıta, "
                f"[chorus] ile 1 nakarat. SADECE sözü ver, açıklama yapma.\n\nİstek: {brief}"}])
            # Tarz etiketleri — vokal CİNSİYETİNİ yazma; onu kullanıcının SABİT sesi belirler
            song_tags = ollama_chat([{"role": "user", "content":
                f"Şu şarkı isteği için İngilizce müzik tarz etiketleri yaz (virgülle ayrılmış, "
                f"kısa, örn: turkish pop, calm, piano). Sadece TÜR/RUH HALİ/ENSTRÜMAN yaz; "
                f"vokal cinsiyeti (male/female) YAZMA. SADECE etiketleri ver.\n\nİstek: {brief}"}])
            caption = f"{song_tags}, {ses['vocal_style']}"   # kullanıcının sabit sesi eklenir
            seed = ses["seed"]                                # kullanıcının sabit seed'i → hep aynı şarkıcı
    except Exception as e:
        return jsonify({"error": f"Söz üretimi hatası: {e}"}), 500

    # 2) SICAK SERVİSLE ÜRET: Ollama'ya yer aç → base hazır → işi gönder → dosyayı bekle.
    #    (Sabit seed = kullanıcıya özel ses; enstrümantalde seed=None.)
    try:
        ollama_yer_ac()          # Ollama'yı bellekten at (base warm kalır)
        if not acestep_servis_hazir_ol():   # servis kapalıysa otomatik başlat
            return jsonify({"error": "Şarkı servisi başlatılıyor, birkaç saniye sonra tekrar dene."}), 503
        acestep_base_yukle()     # base zaten sıcaksa hızlı geçer
        dosya = acestep_uret(caption, lyrics, duration, enstrumantal, seed=seed)
    except Exception as e:
        return jsonify({"error": f"Üretim servisi hatası: {e}"}), 500
    if not dosya:
        return jsonify({"error": "Şarkı üretilemedi (servis zaman aşımı). Servis açık mı?"}), 500

    kalan = kredi_dus(user_id, SARKI_MALIYET) if ODEME_AKTIF else None   # ödeme kapalıysa düşme

    # Kapak OTOMATİK üretilmez (RAM sınırı). Kullanıcı "Kapak Üret" deyince /api/cover üretir.
    return jsonify({
        "lyrics": None if enstrumantal else lyrics,
        "caption": caption,
        "instrumental": enstrumantal,
        "duration": duration,
        "voice": None if enstrumantal else {"vocal_style": ses["vocal_style"], "seed": ses["seed"]},
        "audio_url": f"/audio/{dosya}",
        "brief": brief,   # kapak için (isteğe bağlı; /api/cover gemma2 ile tarif üretir)
        "credits": kalan,
    })


@app.route("/api/voice", methods=["POST"])
def voice_info():
    """Kullanıcının sabit sesini + kredi bakiyesini döndür (UI için)."""
    user_id = ((request.json or {}).get("user_id") or "anon").strip() or "anon"
    ses = kullanici_sesi(user_id)
    vid = ses.get("voice_id")
    return jsonify({"vocal_style": ses["vocal_style"], "seed": ses["seed"],
                    "credits": kredi_getir(user_id), "payment_active": ODEME_AKTIF,
                    "voice_id": vid, "voice_name": PRESET_BY_ID.get(vid, {}).get("name")})


@app.route("/api/voices")
def voices_list():
    """Ses kütüphanesi (kullanıcının seçeceği sesler + önizleme URL'leri)."""
    return jsonify([{"id": v["id"], "name": v["name"], "gender": v["gender"],
                     "preview_url": f"/previews/{v['id']}.wav"} for v in PRESET_VOICES])


@app.route("/api/set-voice", methods=["POST"])
def set_voice():
    """Kullanıcının SEÇTİĞİ sesi kaydet (o sesin sabit seed+stili artık kullanıcının)."""
    body = request.json or {}
    user_id = (body.get("user_id") or "anon").strip() or "anon"
    v = PRESET_BY_ID.get((body.get("voice_id") or "").strip())
    if not v:
        return jsonify({"error": "Geçersiz ses"}), 400
    with _users_lock:
        users = _users_yukle()
        _kullanici_hazirla(users, user_id)
        users[user_id]["voice_id"] = v["id"]
        users[user_id]["vocal_style"] = v["vocal_style"]
        users[user_id]["seed"] = v["seed"]
        _users_kaydet(users)
    return jsonify({"ok": True, "voice_id": v["id"], "name": v["name"]})


@app.route("/previews/<path:filename>")
def preview_file(filename):
    return send_from_directory(PREVIEWS_DIR, filename)


def _seed_preview_yolu(seed):
    return os.path.join(PREVIEWS_DIR, f"u_{seed}.wav")


@app.route("/api/reroll-voice", methods=["POST"])
def reroll_voice():
    """Kullanıcıya YENİ rastgele benzersiz bir ses ata (milyarlarca olasılıktan biri)."""
    user_id = ((request.json or {}).get("user_id") or "anon").strip() or "anon"
    with _users_lock:
        users = _users_yukle()
        _kullanici_hazirla(users, user_id)
        users[user_id]["seed"] = random.randint(1, 2**31 - 1)
        users[user_id]["vocal_style"] = random.choice(VOICE_STYLES)
        users[user_id]["voice_id"] = None
        _users_kaydet(users)
        v = users[user_id]
    return jsonify({"vocal_style": v["vocal_style"], "seed": v["seed"]})


@app.route("/api/voice-preview", methods=["POST"])
def voice_preview():
    """Kullanıcının KENDİ sesinin kısa örneğini üret (kendi seed'iyle). Önbelleğe alınır."""
    user_id = ((request.json or {}).get("user_id") or "anon").strip() or "anon"
    ses = kullanici_sesi(user_id)
    seed = int(ses["seed"]); style = ses["vocal_style"]
    fname = f"u_{seed}.wav"
    yol = _seed_preview_yolu(seed)
    if os.path.exists(yol) and os.path.getsize(yol) > 0:
        return jsonify({"preview_url": f"/previews/{fname}", "cached": True})
    try:
        ollama_yer_ac()
        if not acestep_servis_hazir_ol():
            return jsonify({"error": "Ses servisi başlatılıyor, birkaç saniye sonra tekrar dene."}), 503
        acestep_base_yukle()
        dosya = acestep_uret(f"turkish pop, calm, acoustic guitar, {style}",
                             PREVIEW_LYRICS, 12, False, seed=seed)
    except Exception as e:
        return jsonify({"error": f"Önizleme hatası: {e}"}), 500
    if not dosya:
        return jsonify({"error": "Önizleme üretilemedi"}), 500
    try:
        shutil.copy(os.path.join(API_AUDIO_DIR, dosya), yol)
    except Exception:
        return jsonify({"preview_url": f"/audio/{dosya}", "cached": False})
    return jsonify({"preview_url": f"/previews/{fname}", "cached": False})


# Kredi paketleri (test modu). Gerçek ödeme sağlayıcısı entegrasyonu /api/buy içinde.
KREDI_PAKETLERI = {"kucuk": 10, "orta": 30, "buyuk": 100}


@app.route("/api/buy", methods=["POST"])
def buy_credits():
    """
    TEST MODU: krediyi hemen ekler (simüle satın alma).

    GERÇEK ÖDEME İÇİN: burada bir ödeme sağlayıcısı (Stripe / iyzico / PayTR)
    ile ödeme onayı DOĞRULANMALI; sağlayıcı anahtarları/hesabı platform SAHİBİ
    tarafından sağlanır. Ödeme onaylanmadan kredi EKLENMEZ.
    """
    body = request.json or {}
    user_id = (body.get("user_id") or "anon").strip() or "anon"
    paket = (body.get("paket") or "kucuk").strip()
    miktar = KREDI_PAKETLERI.get(paket, 10)
    # TODO(gerçek ödeme): sağlayıcıdan ödeme onayını doğrula; onaysızsa 402 dön.
    yeni = kredi_ekle(user_id, miktar)
    return jsonify({"credits": yeni, "eklendi": miktar, "test_modu": True})


@app.route("/api/cover", methods=["POST"])
def cover_gen():
    """İSTEĞE BAĞLI kapak: ACE-Step'i durdur → gemma2 görsel tarif → SDXL kapak → ACE-Step geri."""
    body = request.json or {}
    brief = (body.get("brief") or "").strip()
    if not brief:
        return jsonify({"error": "Tema yok"}), 400
    user_id = (body.get("user_id") or "anon").strip() or "anon"
    if ODEME_AKTIF and kredi_getir(user_id) < KAPAK_MALIYET:
        return jsonify({"error": "Yetersiz kredi. Kapak için kredi al.",
                        "need_credits": True, "credits": kredi_getir(user_id)}), 402
    acestep_servis_durdur()          # RAM'i boşalt (gemma2 + SDXL için yer aç)
    # gemma2 ile TEMİZ İngilizce görsel tarif (ACE-Step durduğu için GPU boş → hızlı + düzgün)
    try:
        image_prompt = ollama_chat([{"role": "user", "content":
            "Write ONE short English album cover image description (scene, atmosphere, colors; "
            "no text, no letters, no people). Output ONLY the description.\n\nSong theme: " + brief}],
            model=CHAT_MODEL, num_predict=80)
    except Exception:
        image_prompt = "atmospheric music album cover, cinematic mood, abstract"
    ollama_bosalt(CHAT_MODEL)         # gemma2'yi boşalt → SDXL'e yer aç
    kapak = kapak_uret(image_prompt)  # SDXL-Turbo (ayrı süreç)
    acestep_servis_baslat()          # sonraki şarkı için arka planda hazırla
    if not kapak:
        return jsonify({"error": "Kapak üretilemedi"}), 500
    kalan = kredi_dus(user_id, KAPAK_MALIYET) if ODEME_AKTIF else None   # ödeme kapalıysa düşme
    return jsonify({"cover_url": f"/cover/{kapak}", "prompt": image_prompt, "credits": kalan})


@app.route("/audio/<path:filename>")
def audio(filename):
    return send_from_directory(API_AUDIO_DIR, filename)


@app.route("/cover/<path:filename>")
def cover(filename):
    return send_from_directory(COVERS_DIR, filename)


if __name__ == "__main__":
    print("=" * 55)
    print("  MÜZİK AI  ->  http://127.0.0.1:5000")
    print(f"  Sıcak servis: {ACESTEP_API}  (model: {ACESTEP_MODEL})")
    try:
        _api_get("/health", timeout=3)
        print("  Sıcak servis: AYAKTA ✓")
    except Exception:
        print("  UYARI: Sıcak servis KAPALI! Önce api_server'i başlat.")
    print("=" * 55)
    app.run(host="127.0.0.1", port=5000, debug=False)
