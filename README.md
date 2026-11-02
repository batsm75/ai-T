# 🤖 AI Trading Terminal (v1.2.0)

AI trading analyzer yang berjalan **di terminal (Termux / Linux / macOS / Windows)**.
Mengambil data market langsung dari **TwelveData** + **SoSoValue** via API key, menghitung
**9 indikator teknis** lintas **3 timeframe** (mode trading) untuk menghasilkan
**Buy Power / Sell Power** + **area buyer/seller**, lalu **Groq AI** membaca chart pattern
dan menghasilkan **setup trade** (BUY / SELL / WAIT) lengkap.

> ⚠️ **Disclaimer**: Alat ini hanya untuk edukasi & analisis. Bukan saran investasi.
> Selalu lakukan manajemen risiko dan verifikasi sendiri.

---

## ✨ Fitur

- **Menu 1 — Analisis**: input simbol → pilih **mode trading** (gabungan 3 timeframe) →
  indikator lokal menghitung Buy/Sell Power + area buyer/seller → AI menghasilkan setup.
  Ada tombol **Refresh**, **Ganti simbol**, **Ganti mode**, dan **Kembali ke menu utama**.
- **Mode trading (3-in-1 timeframe)**:
  1. **Scalping** — M5 (entry) + M30 (konfirmasi) + H1 (tren)
  2. **Intraday** — M15 + H1 + H4
  3. **Swing** — H1 + D1 + W1
- **Buy Power / Sell Power** (bar visual) + **Area Pembeli / Area Penjual**, dipengaruhi
  oleh **metode analisis** dan **mode trading** (pembobotan berbeda per keluarga indikator
  & per timeframe).
- **11 indikator teknis** (dihitung lokal, tanpa API):
  `ema200`, `ema50200` (golden/death cross), `macd`, `ichimoku` (cloud), `ichimokutk`
  (Tenkan/Kijun cross), `fibonacci` (retracement zone), `trenm5` (slope tren),
  `momentummy` (momentum/ROC), `lanjutimpuls` (impulse continuation),
  `rsi` (RSI14 oversold/overbought), `volume` (konfirmasi tren).
- **Riwayat analisis**: setiap hasil analisis otomatis tersimpan di `history.json`
  (maks 50), bisa dilihat lewat tombol **H** di menu analisis.
- **Menu 2 — Pengaturan Trade**: Risk:Reward, metode analisis (pilih **1–5**:
  SND/SNR/SMC/ICT/SESSION KILLZONE), target pips, **mode trading**.
- **Menu 3 — Env**: kelola semua API key & URL langsung dari aplikasi, tersimpan di `.env`.
- Setiap pindah menu: **clear screen** dulu, baru tampilkan menu tujuan.
- API key **hanya disimpan di `.env`** lokal pengguna (tidak pernah di-commit ke GitHub).

---

## 🧰 Tech Stack

| Komponen | Teknologi | Alasan |
|---|---|---|
| Bahasa | Python 3.8+ | Jalan native di Termux tanpa emulator |
| UI terminal | `rich` | Tampilan warna, tabel, panel yang rapi & mudah dibaca |
| Env | `python-dotenv` | Simpan API key di `.env` |
| HTTP | `requests` | Panggil REST API TwelveData, SoSoValue, Groq |
| AI Brain | **Groq** (OpenAI-compatible) | Model `qwen/qwen3.8-27b`, cepat & gratis |
| Data 1 | **TwelveData** | Forex / saham / crypto / indeks (time_series) |
| Data 2 | **SoSoValue v3** | US equity & perpetual futures (candles) |

---

## 📁 Struktur Folder

```
ai-trading-terminal/
├── main.py                  # Entry point: python main.py
├── requirements.txt
├── .env.example             # Template konfigurasi (copy jadi .env)
├── .gitignore               # .env & config.json TIDAK ikut ter-commit
├── README.md
└── src/
    ├── __init__.py
    ├── config.py            # Path, env loader, metode analisis, mode trading, setting
    ├── ui.py                # Clear screen, banner, input, tabel, masking, power bar
    ├── clients.py           # Client: TwelveData, SoSoValue, Groq
    ├── indicators.py        # 9 indikator teknis + Buy/Sell Power + area buyer/seller
    ├── analysis.py          # Orkestrasi: fetch 3 TF → indikator → rencana lokal → AI → validasi
    ├── trade_plan.py        # ATR/ADX/BB, zona swing S/R, gate no-trade, validator setup AI
    ├── news.py              # Quote & berita (Menu 4)
    └── menus.py             # Menu utama + Analisis + Pengaturan + Env
```

File lokal yang **dibuat otomatis saat dipakai** (tidak ada di repo):
- `.env` — API key & URL kamu
- `config.json` — pengaturan trade (RR, metode, target pips)

---

## 🚀 Instalasi di Termux (Android)

```bash
# 1. Install Python & Git (jika belum)
pkg update && pkg upgrade -y
pkg install python git -y

# 2. Clone dari GitHub (ganti URL dengan repo kamu)
git clone https://github.com/USERNAME/ai-trading-terminal.git
cd ai-trading-terminal

# 3. Install dependensi
pip install -r requirements.txt

# 4. Jalankan
python main.py
```

> Saat pertama kali dijalankan, file `.env` otomatis dibuat dari `.env.example`.
> Buka **menu [3] Env** untuk mengisi API key, atau edit manual: `nano .env`

### Dapatkan API Key
| Layanan | Link | Catatan |
|---|---|---|
| Groq | https://console.groq.com/keys | Gratis, model default `qwen/qwen3.8-27b` |
| TwelveData | https://twelvedata.com/account/api-keys | Free tier 8 req/menit |
| SoSoValue | https://sosovalue.com/developer | v3: US equity & perpetual futures |

---

## 📤 Upload ke GitHub

```bash
cd ai-trading-terminal
git init
git add .
git commit -m "AI Trading Terminal v1.0"
git branch -M main
git remote add origin https://github.com/USERNAME/ai-trading-terminal.git
git push -u origin main
```

✅ `.env` dan `config.json` **otomatis diabaikan** Git (sudah ada di `.gitignore`),
jadi API key kamu aman tidak ikut ter-upload.

---

## 🧪 Pengujian

Pengujian yang dilakukan pada proyek:
- **Syntax check**: `python -m py_compile main.py src/*.py` — wajib lulus.
- **Import check**: modul ter-load tanpa error.
- **Unit logika parsing JSON AI**: `_extract_json()` menangani respons ber-fence markdown.
- **Alur menu offline**: navigasi tanpa API key menampilkan peringatan yang jelas.

Yang **belum** diuji otomatis (butuh API key asli + jaringan):
- Panggilan nyata ke TwelveData / SoSoValue / Groq.
- Format respons AI yang bervariasi (sudah ada fallback tampilan teks mentah).

Untuk uji koneksi cepat setelah mengisi key:
```bash
python -c "from src.clients import TwelveDataClient; print(TwelveDataClient().get_candles('EUR/USD','1h',3)[-1])"
```

---

## 🛠️ Deployment / Menjalankan

Tidak ada server — aplikasi ini berjalan lokal di perangkat kamu. Cukup:
```bash
python main.py
```
Untuk update dari GitHub: `git pull && pip install -r requirements.txt`

---

## 📌 Status Proyek

**Selesai (v1.5.0):**
- ✅ 3 menu utama + clear screen di setiap transisi
- ✅ **Mode trading 3-in-1**: Scalping (M5/M30/H1), Intraday (M15/H1/H4), Swing (H1/D1/W1)
- ✅ **9 indikator teknis lokal**: ema200, ema50200, macd, ichimoku, ichimokutk, fibonacci, trenm5, momentummy, lanjutimpuls
- ✅ **Buy Power / Sell Power** (bar visual) + **Area Pembeli / Penjual**, dengan bobot yang dipengaruhi metode analisis & mode trading
- ✅ Pengambilan data TwelveData (3 timeframe sekaligus) + SoSoValue (candles v3)
- ✅ Analisis Groq AI dengan 5 metode (SND/SNR/SMC/ICT/Killzone) + RR + target pips
- ✅ Setup output: arah, pattern, entry, SL, TP, RR, invalidasi, level, reasoning
- ✅ Tombol Refresh, ganti simbol, ganti mode, & kembali di menu analisis
- ✅ Manajemen API key via menu Env, tersimpan di `.env`
- ✅ `.gitignore` aman + README + struktur siap `git clone`

## 📝 Changelog

**v1.5.0 — fokus ketepatan entry**

- Indikator & struktur kini hanya memakai **candle yang sudah close** (anti-repainting); harga live tetap dari candle berjalan.
- **Rencana trade lokal** (`trade_plan.py`): entry/SL/TP dihitung dari zona swing S/R + ATR, bukan angka karangan AI.
- **Gate no-trade**: ADX < 20, BB squeeze, ATR ekstrem, sesi sepi/weekend, TF tidak selaras → WAIT.
- **Confluence 0–100** (minimal 60 untuk entry) + **skenario kondisional** saat WAIT (breakout/breakdown + SL/TP).
- **Validator setup AI**: SL/TP sisi salah, entry > 3x ATR dari harga, SL di luar 0.5–3x ATR, RR < 1:1.5, melawan HTF → diganti hitungan lokal atau jadi WAIT.
- **Riwayat + hasil otomatis**: tiap sinyal dicek TP1/SL (SL menang bila keduanya kena di candle yang sama), tampil win rate di menu Riwayat.
- Perbaikan: fibonacci (logika sinyal), RSI Wilder, indikator tanpa data tidak lagi mengencerkan persen, bobot indikator berkorelasi dikurangi, markup `[cyan]/[magenta]` mentah, pesan HTML SoSoValue 403, temperature Groq 0.15.
- Peringatan jika target pips tidak realistis terhadap ATR.

**v1.1.0**
- Timeframe diganti menjadi **mode trading** (gabungan 3 timeframe): Scalping / Intraday / Swing.
- Tambah **Buy Power & Sell Power** (persentase + bar visual) di hasil analisis.
- Tambah **Area Pembeli (buyer zone)** & **Area Penjual (seller zone)**.
- Tambah **9 indikator teknis** yang dihitung lokal; sinyalnya di-aggregasi dengan bobot
  berbeda sesuai metode analisis (SND/SNR/SMC/ICT/Killzone) dan mode trading.
- Hasil indikator ikut dikirim ke Groq AI sebagai konteks setup.
- Mode trading bisa diubah di Menu 1 (tombol M) dan Menu 2 (opsi 4), tersimpan di `config.json`.

**v1.2.0**
- Tambah indikator **RSI(14)** dan **Volume** (total 11 indikator, 33 sinyal lintas 3 TF).
- Data **volume** sekarang diambil dari TwelveData dan dipakai untuk konfirmasi tren.
- Tambah **Riwayat Analisis**: otomatis tersimpan di `history.json` (maks 50), dilihat
  dengan tombol **H** di menu analisis. `history.json` masuk `.gitignore`.

**v1.3.0**
- Tambah **Menu 4 — Signal MTF**: sinyal murni BUY/SELL/NEUTRAL + confidence dari semua
  timeframe, **tanpa entry/SL/TP**. Struktur laporan sama (sumber data, power, panel, reasoning)
  tapi khusus sinyal.
- AI mengumpulkan **data makro/fundamental** (quote TwelveData: change %, volume, 52-week) +
  **news real-time** (Google News RSS, tanpa API key) yang di-filter **24 jam terakhir**.
- Jika news terdeteksi, AI menganalisis **arah & dampak** tiap berita (bullish/bearish/neutral,
  impact low/medium/high) dan confidence mencerminkan pengaruhnya.
- Hasil Signal MTF: sinyal + confidence + bias per timeframe (entry/confirm/trend) +
  ringkasan teknikal + ringkasan makro + tabel dampak news + reasoning.
- Sumber news tidak membutuhkan API key tambahan; jika gagal, analisis tetap berjalan (catatan ditampilkan).

**v1.4.0**
- **Aturan format angka**: semua angka harga di hasil analisis (Menu 1 & Menu 4) dibulatkan &
  desimal trailing dihapus (`63868.0` → `63868`, `1.08520` → `1.0852`), via `fmt_num()`.
- **Logika tipe order** di Menu 1: AI wajib menentukan `order_type` (Buy Limit / Sell Limit /
  Buy Stop / Sell Stop) sesuai aturan: Limit untuk pullback ke zona lawas arah, Stop untuk
  breakout/breakdown valid. Jika harga jauh dari area ideal & tidak ada level breakout/breakdown
  valid, setup wajib **WAIT / NO TRADE**. Ditampilkan di panel setup.
- **Signal MTF (Menu 4)** sekarang mengambil **semua 6 timeframe** (5m, 15m, 1h, 4h, 1d, 1w),
  bukan cuma 3. Ditambah field **`potential_target_1d`** (target potensial 1 hari) yang
  ditampilkan terpisah (tetap tanpa entry/SL/TP).

**Catatan / batasan:**
- SoSoValue v3 saat ini hanya mencakup **US equity & perpetual futures** (belum spot forex).
  Untuk pair forex, analisis tetap berjalan penuh menggunakan TwelveData; status SoSoValue
  ditampilkan sebagai info di panel sumber data.
- Jika model `qwen/qwen3.8-27b` tidak tersedia di akun Groq kamu, ubah `GROQ_MODEL`
  di menu Env (mis. `llama-3.3-70b-versatile`).

**Langkah berikutnya (opsional):**
- Tambahkan indikator teknis (RSI, MACD, EMA) di sisi lokal.
- Simpan riwayat analisis ke file JSON/CSV.
- Tambahkan alert notifikasi bila setup terpenuhi.
