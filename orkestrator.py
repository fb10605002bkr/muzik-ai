# -*- coding: utf-8 -*-
"""
=====================================================================
  MÜZİK AI — SIRALI BORU HATTI ORKESTRATÖRÜ  (iskelet)
=====================================================================
Mantık: modeller SIRAYLA çalışır. Her aşama:
    YÜKLE  ->  ÇALIŞTIR  ->  BELLEKTEN AT (VRAM'i boşalt)
Böylece her an bellekte TEK model olur (laptop dostu).

Boru hattı:
    1) Söz          (LLM)              -> sözleri üret
    2) Şarkı        (YuE/DiffRhythm)   -> vokal + müzik ham şarkı  [AĞIR: GPU]
    3) Vokal ayır   (Demucs)           -> vokal / enstrümantal ayır
    4) Ses dönüştür (RVC)              -> vokali KULLANICININ sesine çevir
    5) Mix                              -> özel vokal + enstrümantal -> final

Şimdilik her aşama PLACEHOLDER'dır (gerçek model yerine işaret dosyası üretir),
böylece akışı uçtan uca test edebilirsin. Gerçek modeli "TODO" olan yerlere koyarsın.
"""

import gc
import os
import sys
import time
import json
import urllib.request
from dataclasses import dataclass, field
from typing import List

# Windows konsolunda Türkçe/sembol çıktısı için UTF-8 (çökmesin diye)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---- AYARLAR ----
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
SOZ_MODEL = "qwen2.5:3b"   # şarkı sözü için temiz, küçük model (değiştirilebilir)

# torch varsa VRAM temizliği için kullan; yoksa sorun değil (placeholder aşama)
try:
    import torch
    CUDA_VAR = torch.cuda.is_available()
except Exception:
    torch = None
    CUDA_VAR = False


# =====================================================================
#  YARDIMCILAR
# =====================================================================
def vram_bosalt():
    """Model silindikten sonra belleği/VRAM'i temizle."""
    gc.collect()
    if torch is not None and CUDA_VAR:
        torch.cuda.empty_cache()
        try:
            torch.cuda.ipc_collect()
        except Exception:
            pass


def _placeholder_dosya(yol: str, icerik: str = ""):
    """Gerçek çıktı yerine işaret dosyası yaz (akışı test etmek için)."""
    os.makedirs(os.path.dirname(yol) or ".", exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        f.write(icerik or f"[PLACEHOLDER] {os.path.basename(yol)}\n")
    return yol


def dosya_yaz(yol: str, icerik: str):
    """Metni dosyaya yaz."""
    os.makedirs(os.path.dirname(yol) or ".", exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        f.write(icerik)
    return yol


def ollama_uret(prompt: str, model: str = SOZ_MODEL, sistem: str = None) -> str:
    """Ollama'ya istek at, cevabı döndür. keep_alive=0 -> üretimden sonra modeli
    bellekten AT (sıralı boru hattı mantığı: sonraki aşamaya VRAM kalsın)."""
    mesajlar = []
    if sistem:
        mesajlar.append({"role": "system", "content": sistem})
    mesajlar.append({"role": "user", "content": prompt})
    payload = json.dumps({
        "model": model,
        "messages": mesajlar,
        "stream": False,
        "keep_alive": 0,
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))["message"]["content"]


# =====================================================================
#  BORU HATTI BOYUNCA TAŞINAN İŞ VERİSİ
# =====================================================================
@dataclass
class SarkiIsi:
    tema: str                       # kullanıcı girdisi (şarkının konusu/tarzı)
    kullanici_ses_modeli: str       # RVC ses modeli yolu (kullanıcıya ÖZEL, sabit)
    cikti_klasoru: str = "cikti"

    # Aşamalar doldurur:
    sozler: str = ""
    ham_sarki_yolu: str = ""        # 2. aşama: ham şarkı (vokal+müzik)
    vokal_yolu: str = ""            # 3. aşama: ayrıştırılmış vokal
    enstrumantal_yolu: str = ""     # 3. aşama: ayrıştırılmış müzik
    ozel_vokal_yolu: str = ""       # 4. aşama: RVC ile dönüştürülmüş vokal
    final_yolu: str = ""            # 5. aşama: nihai şarkı
    log: List[str] = field(default_factory=list)

    def dosya(self, ad: str) -> str:
        return os.path.join(self.cikti_klasoru, ad)


# =====================================================================
#  AŞAMA TABANI  (her modelin ortak arayüzü)
# =====================================================================
class Asama:
    ad = "asama"
    agir_gpu = False   # True ise: laptopta çalışmaz, kiralık GPU gerekir

    def yukle(self):
        """Modeli belleğe al. Alt sınıf doldurur."""
        raise NotImplementedError

    def calistir(self, is_: SarkiIsi):
        """İşi işle. Alt sınıf doldurur."""
        raise NotImplementedError

    def bosalt(self):
        """Modeli bellekten at + VRAM temizle. Alt sınıf 'self.model=None' yapmalı."""
        vram_bosalt()


# =====================================================================
#  SOMUT AŞAMALAR  (şimdilik PLACEHOLDER — gerçek kod TODO'lara)
# =====================================================================
class SozAsamasi(Asama):
    """GERÇEK: Ollama'daki LLM ile temadan şarkı sözü üretir."""
    ad = "1) Söz (LLM - Ollama)"

    SISTEM = (
        "Sen profesyonel bir Türkçe şarkı sözü yazarısın. "
        "Verilen temaya uygun, akıcı ve kafiyeli bir şarkı sözü yaz. "
        "Yapı: 2 kıta (verse) ve 1 nakarat (chorus). "
        "Nakaratı [Nakarat] etiketiyle belirt. "
        "SADECE şarkı sözünü ver; açıklama, giriş cümlesi veya not ekleme."
    )

    def yukle(self):
        # Ollama modeli kendi sürecinde tutar; burada yüklemeye gerek yok.
        # (Model, ilk istekte Ollama tarafından yüklenir.)
        pass

    def calistir(self, is_: SarkiIsi):
        prompt = f"Tema: {is_.tema}\n\nBu temada bir şarkı sözü yaz."
        try:
            is_.sozler = ollama_uret(prompt, model=SOZ_MODEL, sistem=self.SISTEM).strip()
        except Exception as e:
            is_.sozler = ""
            raise RuntimeError(f"Ollama söz üretimi başarısız: {e}")
        dosya_yaz(is_.dosya("01_sozler.txt"), is_.sozler)
        is_.log.append(f"Söz üretildi ({len(is_.sozler)} karakter, model={SOZ_MODEL})")

    def bosalt(self):
        # ollama_uret keep_alive=0 ile modeli zaten bellekten attı.
        vram_bosalt()


class SarkiAsamasi(Asama):
    ad = "2) Şarkı üretimi (YuE/DiffRhythm)"
    agir_gpu = True   # <-- AĞIR: 8GB VRAM'e sığmaz, kiralık GPU gerekir

    def yukle(self):
        # TODO: sözlü şarkı üreten açık modeli yükle (YuE / DiffRhythm).
        self.model = "SongGen(placeholder)"

    def calistir(self, is_: SarkiIsi):
        # TODO: is_.sozler -> ham şarkı (vokal + müzik) .wav
        is_.ham_sarki_yolu = _placeholder_dosya(is_.dosya("02_ham_sarki.wav.txt"))
        is_.log.append("Ham şarkı üretildi (placeholder)")

    def bosalt(self):
        self.model = None
        vram_bosalt()


class VokalAyirmaAsamasi(Asama):
    ad = "3) Vokal ayrıştırma (Demucs)"

    def yukle(self):
        # TODO: Demucs modelini yükle.
        self.model = "Demucs(placeholder)"

    def calistir(self, is_: SarkiIsi):
        # TODO: ham şarkıyı vokal + enstrümantal olarak ayır.
        is_.vokal_yolu = _placeholder_dosya(is_.dosya("03_vokal.wav.txt"))
        is_.enstrumantal_yolu = _placeholder_dosya(is_.dosya("03_enstrumantal.wav.txt"))
        is_.log.append("Vokal/enstrümantal ayrıştırıldı (placeholder)")

    def bosalt(self):
        self.model = None
        vram_bosalt()


class SesDonusturmeAsamasi(Asama):
    ad = "4) Ses dönüştürme (RVC — kullanıcı sesi)"

    def yukle(self):
        # TODO: RVC / so-vits-svc'yi + kullanıcının ses modelini yükle.
        self.model = "RVC(placeholder)"

    def calistir(self, is_: SarkiIsi):
        # TODO: is_.vokal_yolu -> is_.kullanici_ses_modeli ile dönüştür.
        is_.ozel_vokal_yolu = _placeholder_dosya(
            is_.dosya("04_ozel_vokal.wav.txt"),
            f"ses modeli: {is_.kullanici_ses_modeli}\n",
        )
        is_.log.append("Vokal, kullanıcının özel sesine dönüştürüldü (placeholder)")

    def bosalt(self):
        self.model = None
        vram_bosalt()


class MixAsamasi(Asama):
    ad = "5) Mix (özel vokal + enstrümantal)"

    def yukle(self):
        # Mix genelde model gerektirmez (pydub/ffmpeg). Yükleme boş.
        self.model = None

    def calistir(self, is_: SarkiIsi):
        # TODO: ozel_vokal + enstrumantal -> final .mp3 (ffmpeg/pydub).
        is_.final_yolu = _placeholder_dosya(
            is_.dosya("05_FINAL_sarki.mp3.txt"),
            "vokal + enstrumantal karistirildi\n",
        )
        is_.log.append("Final şarkı oluşturuldu (placeholder)")

    def bosalt(self):
        vram_bosalt()


# =====================================================================
#  ORKESTRATÖR  (aşamaları SIRAYLA yönetir)
# =====================================================================
class Orkestrator:
    def __init__(self, asamalar: List[Asama]):
        self.asamalar = asamalar

    def calistir(self, is_: SarkiIsi) -> SarkiIsi:
        print("=" * 60)
        print(f"  BORU HATTI BAŞLADI — tema: {is_.tema!r}")
        print("=" * 60)
        toplam_t0 = time.time()

        for asama in self.asamalar:
            etiket = asama.ad + ("  [AGIR/GPU]" if asama.agir_gpu else "")
            print(f"\n>> {etiket}")
            t0 = time.time()
            try:
                print("   yükleniyor...")
                asama.yukle()
                print("   çalışıyor...")
                asama.calistir(is_)
            except Exception as e:
                print(f"   [HATA]: {e}")
                is_.log.append(f"HATA [{asama.ad}]: {e}")
                # Bir aşama patlarsa da modeli boşalt (VRAM sızmasın)
            finally:
                print("   bellekten atiliyor (VRAM bosaltiliyor)...")
                asama.bosalt()
            print(f"   [OK] {time.time() - t0:.1f} sn")

        print("\n" + "=" * 60)
        print(f"  BİTTİ — toplam {time.time() - toplam_t0:.1f} sn")
        print(f"  Final: {is_.final_yolu or '(üretilmedi)'}")
        print("=" * 60)
        return is_


# =====================================================================
#  ÇALIŞTIR (örnek)
# =====================================================================
def main():
    # Boru hattı sırası — istediğin gibi ekle/çıkar:
    boru_hatti = [
        SozAsamasi(),
        SarkiAsamasi(),        # AĞIR — gerçek modelde kiralık GPU
        VokalAyirmaAsamasi(),
        SesDonusturmeAsamasi(),
        MixAsamasi(),
    ]

    is_ = SarkiIsi(
        tema="yaz akşamı, deniz kenarı, huzurlu pop",
        kullanici_ses_modeli="ses_modelleri/kullanici_42.pth",
        cikti_klasoru="cikti/sarki_001",
    )

    orkestrator = Orkestrator(boru_hatti)
    sonuc = orkestrator.calistir(is_)

    print("\n--- IS GUNLUGU ---")
    for satir in sonuc.log:
        print("  -", satir)


if __name__ == "__main__":
    main()
