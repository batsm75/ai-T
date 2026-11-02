"""Engine analisis: gabungkan data market (3 timeframe) -> indikator lokal
(buy/sell power + area buyer/seller) -> kirim ke Groq -> hasilkan setup."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from . import config
from .clients import APIError, GroqClient, SoSoValueClient, TwelveDataClient
from .indicators import PowerResult, compute_power
from .news import fetch_news, fetch_quote
from .trade_plan import TradePlan, build_plan, validate_setup


@dataclass
class AnalysisResult:
    symbol: str = ""
    trading_mode: str = ""
    timeframes: tuple = ()
    current_price: float | None = None
    candles_per_tf: dict = field(default_factory=dict)   # {"Entry": [...], ...}
    twelvedata_ok: bool = False
    sosovalue_note: str = ""
    sosovalue_count: int = 0
    stats: dict = field(default_factory=dict)
    power: PowerResult | None = None
    ai_raw: str = ""
    setup: dict | None = None
    plan: TradePlan | None = None
    validation_notes: list = field(default_factory=list)
    error: str = ""


def _candles_to_text(candles: list[dict], limit: int = 50) -> str:
    subset = candles[-limit:]
    lines = ["time,O,H,L,C"]
    for c in subset:
        lines.append(f"{c['time']},{c['open']},{c['high']},{c['low']},{c['close']}")
    return "\n".join(lines)


def _compute_stats(candles: list[dict]) -> dict:
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    last = candles[-1]
    ma20 = sum(closes[-20:]) / min(20, len(closes))
    swing_high = max(highs[-30:]) if len(highs) >= 5 else max(highs)
    swing_low = min(lows[-30:]) if len(lows) >= 5 else min(lows)
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[-14:]) / max(1, len(trs[-14:])) if trs else 0
    return {
        "last_time": last["time"],
        "last_close": last["close"],
        "ma20": round(ma20, 6),
        "swing_high_30": round(swing_high, 6),
        "swing_low_30": round(swing_low, 6),
        "atr14": round(atr, 6),
        "bias_ma": "ABOVE MA20 (bullish)" if last["close"] > ma20 else "BELOW MA20 (bearish)",
    }


def _extract_json(text: str) -> dict | None:
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.DOTALL)
    if fence:
        t = fence.group(1)
    else:
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end != -1 and end > start:
            t = t[start:end + 1]
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _power_summary(power: PowerResult) -> str:
    lines = [f"BUY POWER: {power.buy_pct}% | SELL POWER: {power.sell_pct}% | "
             f"NETRAL: {power.neutral_pct}%"]
    if power.buyer_area:
        lines.append(f"AREA PEMBELI (buyer zone): {power.buyer_area[0]} - {power.buyer_area[1]}")
    if power.seller_area:
        lines.append(f"AREA PENJUAL (seller zone): {power.seller_area[0]} - {power.seller_area[1]}")
    # Ringkasan sinyal per indikator (gabungan lintas TF)
    agg: dict[str, int] = {}
    for s in power.signals:
        agg[s["name"]] = agg.get(s["name"], 0) + s["signal"]
    lines.append("Sinyal indikator (total skor lintas 3 TF, + bullish / - bearish):")
    for name, score in agg.items():
        arah = "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NETRAL"
        lines.append(f"  - {name}: {arah} ({score:+d})")
    return "\n".join(lines)


def _plan_summary(plan: TradePlan | None) -> str:
    if plan is None:
        return "(tidak tersedia)"
    lines = [f"Arah lokal: {plan.direction} | Confluence {plan.confluence}/100 | "
             f"ATR entry {plan.atr_entry:.4f} | ATR konfirmasi {plan.atr_confirm:.4f}"]
    for g in plan.gates:
        lines.append(f"  - Gate {g.name}: {'LOLOS' if g.ok else 'GAGAL'} — {g.detail}")
    if plan.direction != "WAIT":
        lines.append(f"  - Rencana lokal: entry {plan.entry:.4f}, SL {plan.stop_loss:.4f}, "
                     f"TP {', '.join(f'{t:.4f}' for t in plan.take_profit)}")
    for side, zs in plan.zones.items():
        txt = "; ".join(f"{z['low']:.4f}-{z['high']:.4f} ({z['touches']}x)" for z in zs)
        lines.append(f"  - Zona {side}: {txt or '-'}")
    return "\n".join(lines)


def _build_prompts(symbol: str, mode: dict, candles_entry: list[dict],
                   stats: dict, settings: dict, power: PowerResult,
                   plan: TradePlan | None = None) -> tuple[str, str]:
    method = config.method_by_code(settings["analysis_method"])
    method_name = method["name"] if method else settings["analysis_method"]
    method_desc = method["desc"] if method else ""
    tfs = mode["timeframes"]

    system = (
        "Kamu adalah analis teknikal profesional spesialis forex & crypto. "
        "Kamu menerima data candle + hasil perhitungan indikator teknis (buy/sell power, "
        "area buyer/seller) lintas timeframe. Identifikasi struktur pasar & chart pattern, "
        "lalu hasilkan satu setup trading yang jelas. Jawab HANYA dalam format JSON, tanpa teks lain. "
        "Field wajib: setup (BUY/SELL/WAIT/NO TRADE), order_type (Buy Limit / Sell Limit / "
        "Buy Stop / Sell Stop / -), pattern (nama pola), confidence (0-100), "
        "entry, stop_loss, take_profit (array 1-3 level), risk_reward, "
        "invalidation, key_levels (array level penting), bias (arah tren), "
        "buy_power_pct, sell_power_pct (ulang angka dari data), "
        "reasoning (3-5 poin, bahasa Indonesia, sebutkan area buyer/seller bila relevan). "
        "ATURAN TIPE ORDER WAJIB: Buy Limit = trend bullish & harga perlu pullback ke "
        "demand/support/FVG di bawah harga sekarang sebelum entry. Sell Limit = trend bearish "
        "& perlu pullback ke supply/resistance/FVG di atas harga sekarang. Buy Stop = harga "
        "dekat resistance & butuh konfirmasi breakout valid (candle close kuat). Sell Stop = "
        "harga dekat support & butuh konfirmasi breakdown valid. Jika harga sudah jauh dari "
        "area entry ideal: untuk bias LONG yang sudah naik jauh, cari Buy Stop breakout valid "
        "(jangan paksa Buy Limit jauh di bawah); untuk bias SHORT yang sudah turun jauh, cari "
        "Sell Stop breakdown valid. Jika tidak ada level breakout/breakdown yang valid, setup "
        "wajib WAIT/NO TRADE. Semua angka harga dibulatkan, tanpa desimal trailing "
        "(mis. tulis 63868, bukan 63868.0). "
        "ATURAN VALIDASI: jika ada gate yang GAGAL pada RENCANA LOKAL, setup wajib WAIT. "
        "Jangan melawan arah timeframe besar. Entry harus dalam 3x ATR dari harga, "
        "SL 0.5-3x ATR dari entry (letakkan di luar zona + buffer), RR minimal 1:1.5, "
        "TP tidak boleh melewati zona lawan terdekat. Jangan mengarang level di luar data."
    )

    candle_text = _candles_to_text(candles_entry)
    user = f"""INSTRUMEN: {symbol}
MODE TRADING: {mode['code']} — timeframe Entry={tfs[0]}, Konfirmasi={tfs[1]}, Tren={tfs[2]}
METODE ANALISIS: {method_name} — {method_desc}
PENGATURAN TRADE: Risk:Reward = {settings['risk_reward']}, Target = {settings['target_pips']} pips

HASIL INDIKATOR TEKNIS (9 metode: ema200, ema50200, macd, ichimoku, ichimokutk, fibonacci, trenm5, momentummy, lanjutimpuls):
{_power_summary(power)}

RENCANA LOKAL (rule-based, candle close saja):
{_plan_summary(plan)}

STATISTIK TF ENTRY:
- Close terakhir: {stats['last_close']} @ {stats['last_time']}
- MA20: {stats['ma20']} → harga {stats['bias_ma']}
- Swing high (30 candle): {stats['swing_high_30']}
- Swing low (30 candle): {stats['swing_low_30']}
- ATR14 (volatilitas): {stats['atr14']}

DATA CANDLE TF ENTRY (terlama → terbaru, format time,O,H,L,C):
{candle_text}

Berdasarkan mode {mode['code']}, metode {method_name}, serta buy/sell power & area buyer/seller di atas,
tentukan setup terbaik saat ini. Jika buy power & sell power seimbang atau tidak ada setup valid,
isi setup="WAIT". Pastikan entry, stop_loss, take_profit masuk akal terhadap harga, ATR, dan area buyer/seller.
Jawab HANYA JSON."""
    return system, user


def run_analysis(symbol: str, settings: dict) -> AnalysisResult:
    mode = config.trading_mode_by_code(settings.get("trading_mode", "Intraday")) or config.TRADING_MODES[1]
    res = AnalysisResult(symbol=symbol.upper(), trading_mode=mode["code"],
                         timeframes=mode["timeframes"])

    # 1. Ambil 3 timeframe dari TwelveData (butuh ~260 candle untuk EMA200)
    td = TwelveDataClient()
    if not td.ready():
        res.error = "TWELVEDATA_API_KEY belum diisi. Buka menu [3] Env."
        return res

    candles_per_tf: dict[str, list[dict]] = {}
    errors = []
    for label, interval in zip(config.TF_LABELS, mode["timeframes"]):
        try:
            candles_per_tf[label] = td.get_candles(symbol, interval, outputsize=260)
        except APIError as e:
            errors.append(f"{label}({interval}): {e}")
    if not candles_per_tf:
        res.error = "Gagal mengambil data TwelveData: " + "; ".join(errors)
        return res

    res.twelvedata_ok = True
    res.candles_per_tf = candles_per_tf
    live_entry = candles_per_tf.get("Entry") or next(iter(candles_per_tf.values()))
    res.current_price = live_entry[-1]["close"]          # harga live (candle berjalan)

    # Indikator & struktur hanya memakai candle yang SUDAH CLOSE (anti-repainting)
    closed_tf = {k: (v[:-1] if len(v) > 60 else v) for k, v in candles_per_tf.items()}
    entry_candles = closed_tf.get("Entry") or next(iter(closed_tf.values()))
    res.stats = _compute_stats(entry_candles)
    res.stats["live_price"] = res.current_price

    # Evaluasi hasil sinyal lama (TP/SL kena atau belum) memakai candle terbaru
    try:
        config.update_history_outcomes(res.symbol, live_entry)
    except Exception:  # noqa: BLE001
        pass

    # 2. Hitung buy/sell power + area buyer/seller dari indikator lokal
    try:
        res.power = compute_power(closed_tf, mode["code"], settings["analysis_method"])
    except Exception as e:  # noqa: BLE001 — jangan sampai gagal indikator menggagalkan analisis
        res.power = PowerResult()
        res.error = f"(warning) perhitungan indikator gagal: {e}"

    # 2b. Rencana trade lokal (ATR + zona swing + gate no-trade)
    try:
        res.plan = build_plan(symbol, candles_per_tf, res.power, settings, mode["code"])
    except Exception as e:  # noqa: BLE001
        res.plan = None
        res.error = (res.error + " | " if res.error else "") + f"(warning) rencana lokal gagal: {e}"

    # 3. SoSoValue (tambahan, opsional) — pakai interval entry
    ssv = SoSoValueClient()
    if ssv.ready():
        entry_interval = mode["timeframes"][0]
        ssv_candles, note = ssv.get_candles(symbol, entry_interval, limit=140)
        res.sosovalue_note = note
        res.sosovalue_count = len(ssv_candles)
    else:
        res.sosovalue_note = "SOSOVALUE_API_KEY belum diisi — data ini dilewati."

    # 4. Analisis oleh Groq
    groq = GroqClient()
    if not groq.ready():
        res.error = (res.error + " | " if res.error else "") + "GROQ_API_KEY belum diisi. Buka menu [3] Env."
        return res

    system, user = _build_prompts(symbol, mode, entry_candles, res.stats, settings, res.power, res.plan)
    try:
        raw = groq.chat(system, user)
        res.ai_raw = raw
        res.setup = _extract_json(raw)
    except APIError as e:
        res.error = (res.error + " | " if res.error else "") + str(e)

    # 5. Validasi setup AI terhadap aturan lokal (SL/TP sisi benar, RR, jarak ATR, gate)
    if res.plan is not None:
        if res.setup:
            res.setup, res.validation_notes = validate_setup(res.setup, res.plan,
                                                             res.current_price, settings)
        elif res.plan.direction != "WAIT":
            # AI gagal / JSON rusak -> pakai rencana lokal saja
            p = res.plan
            res.setup = {"setup": p.direction, "order_type": p.order_type, "pattern": "Rule-based (lokal)",
                         "confidence": p.confluence, "entry": p.entry, "stop_loss": p.stop_loss,
                         "take_profit": p.take_profit, "risk_reward": p.risk_reward,
                         "invalidation": f"Close melewati SL {p.stop_loss:.2f}", "reasoning": p.reasons}
            res.validation_notes = ["Respons AI tidak valid — memakai rencana lokal."]

    _record_history(res)
    return res


def _record_history(res: AnalysisResult) -> None:
    """Simpan ringkasan analisis ke history.json (jika ada setup)."""
    try:
        s = res.setup or {}
        tp = s.get("take_profit", [])
        tp1 = tp[0] if isinstance(tp, list) and tp else "-"
        record = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "symbol": res.symbol,
            "mode": res.trading_mode,
            "setup": str(s.get("setup", "-")).upper(),
            "entry": s.get("entry", "-"),
            "stop_loss": s.get("stop_loss", "-"),
            "tp1": tp1,
            "confidence": s.get("confidence", "-"),
            "buy_pct": res.power.buy_pct if res.power else None,
            "sell_pct": res.power.sell_pct if res.power else None,
            "ref_time": res.stats.get("last_time") if res.stats else None,
            "price": res.current_price,
            "confluence": res.plan.confluence if res.plan else None,
            "order_type": s.get("order_type", "-"),
            "outcome": "pending" if str(s.get("setup", "")).upper() in ("BUY", "SELL") else "-",
        }
        records = config.load_history()
        records.append(record)
        config.save_history(records)
    except Exception:  # noqa: BLE001 — gagal simpan history tidak boleh menggagalkan analisis
        pass


# ===========================================================================
# MENU 4 — SIGNAL MTF (murni sinyal BUY/SELL, TANPA entry/SL/TP)
# ===========================================================================
@dataclass
class SignalResult:
    symbol: str = ""
    trading_mode: str = ""
    timeframes: tuple = ()
    current_price: float | None = None
    candles_per_tf: dict = field(default_factory=dict)
    power: PowerResult | None = None
    quote: dict | None = None
    quote_note: str = ""
    news: list[dict] = field(default_factory=list)
    news_note: str = ""
    ai_raw: str = ""
    signal: dict | None = None
    error: str = ""


def _build_mtf_prompts(symbol: str, mode: dict, stats: dict, settings: dict,
                       power: PowerResult, quote: dict | None,
                       news: list[dict]) -> tuple[str, str]:
    method = config.method_by_code(settings["analysis_method"])
    method_name = method["name"] if method else settings["analysis_method"]
    tfs = mode["timeframes"]

    system = (
        "Kamu adalah analis pasar profesional (teknikal & makro/fundamental). "
        "Berdasarkan data multi-timeframe, indikator teknis, dan berita 24 jam terakhir, "
        "berikan SINYAL arah pasar. JANGAN memberikan entry, stop loss, atau take profit — "
        "jawaban murni sinyal + confidence + penjelasan. Output HANYA JSON, tanpa teks lain. "
        "Field wajib: signal (BUY/SELL/NEUTRAL), confidence (0-100), "
        "potential_target_1d (array 1-2 level harga target potensial selama 1 hari ke depan, boleh kosong), "
        "technical_summary (ringkasan teknikal multi-timeframe), "
        "macro_fundamental_summary (ringkasan makro/fundamental, bahasa Indonesia), "
        "news_impact (array of {headline, bias: bullish/bearish/neutral, impact: low/medium/high}), "
        "multi_tf_bias (object {entry, confirm, trend} masing-masing BUY/SELL/NEUTRAL), "
        "reasoning (3-5 poin, bahasa Indonesia). "
        "Fokus horison: arah selama 1 hari ke depan. Semua angka harga dibulatkan, "
        "tanpa desimal trailing (mis. 63868 bukan 63868.0)."
    )

    news_text = "(tidak ada berita signifikan 24 jam terakhir)"
    if news:
        news_text = "\n".join(f"  - [{n['age_hours']}h lalu] {n['title']}" for n in news[:12])

    quote_text = "tidak tersedia"
    if quote:
        parts = []
        if quote.get("percent_change") is not None:
            parts.append(f"change {quote['percent_change']}%")
        if quote.get("volume"):
            parts.append(f"volume {quote['volume']}")
        if quote.get("fifty_two_week_low") and quote.get("fifty_two_week_high"):
            parts.append(f"52w {quote['fifty_two_week_low']}-{quote['fifty_two_week_high']}")
        quote_text = ", ".join(parts) if parts else "tersedia"

    user = f"""INSTRUMEN: {symbol}
MODE TRADING: {mode['code']} — Entry={tfs[0]}, Konfirmasi={tfs[1]}, Tren={tfs[2]}
METODE ANALISIS: {method_name}
HARGA TERKINI: {stats.get('last_close')} @ {stats.get('last_time')}

KONTEKS MARKET (quote): {quote_text}

HASIL INDIKATOR TEKNIS (9 indikator × 3 timeframe):
{_power_summary(power)}

BERITA 24 JAM TERAKHIR (real-time):
{news_text}

Berdasarkan teknikal multi-timeframe, makro/fundamental, dan arah berita di atas,
tentukan sinyal arah {symbol}. Jika berita dominan high-impact, confidence harus
mencerminkan pengaruhnya. Jawab HANYA JSON."""
    return system, user


def run_mtf_signal(symbol: str, settings: dict) -> SignalResult:
    all_tfs = list(config.SIGNAL_TIMEFRAMES)
    res = SignalResult(symbol=symbol.upper(), trading_mode="Multi-TF",
                       timeframes=tuple(all_tfs))

    td = TwelveDataClient()
    if not td.ready():
        res.error = "TWELVEDATA_API_KEY belum diisi. Buka menu [3] Env."
        return res

    candles_per_tf: dict[str, list[dict]] = {}
    errors = []
    for interval in all_tfs:
        try:
            candles_per_tf[interval] = td.get_candles(symbol, interval, outputsize=260)
        except APIError as e:
            errors.append(f"{interval}: {e}")
    if not candles_per_tf:
        res.error = "Gagal mengambil data TwelveData: " + "; ".join(errors)
        return res

    res.candles_per_tf = candles_per_tf
    entry_candles = candles_per_tf.get("1h") or next(iter(candles_per_tf.values()))
    res.current_price = entry_candles[-1]["close"]
    stats = _compute_stats(entry_candles)

    try:
        res.power = compute_power(candles_per_tf, "Intraday", settings["analysis_method"])
    except Exception as e:  # noqa: BLE001
        res.power = PowerResult()
        res.error = f"(warning) indikator gagal: {e}"

    # Konteks makro + news real-time (24 jam)
    res.quote, res.quote_note = fetch_quote(symbol)
    res.news, res.news_note = fetch_news(symbol, hours=24)

    groq = GroqClient()
    if not groq.ready():
        res.error = (res.error + " | " if res.error else "") + "GROQ_API_KEY belum diisi. Buka menu [3] Env."
        return res

    mode = {"code": "Multi-TF", "timeframes": tuple(all_tfs)}
    system, user = _build_mtf_prompts(symbol, mode, stats, settings, res.power, res.quote, res.news)
    try:
        raw = groq.chat(system, user)
        res.ai_raw = raw
        res.signal = _extract_json(raw)
    except APIError as e:
        res.error = (res.error + " | " if res.error else "") + str(e)
    return res
