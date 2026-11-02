"""Rencana trade rule-based (lokal) + gate no-trade + validator setup AI.

Prinsip: entry/SL/TP dihitung dari struktur harga & ATR, BUKAN dari angka
karangan LLM. AI hanya dipakai untuk membaca konteks; hasilnya divalidasi di sini.
Semua perhitungan memakai candle yang SUDAH CLOSE (candle terakhir yang masih
berjalan dibuang) supaya sinyal tidak berubah-ubah (repainting).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------
def atr(candles: list[dict], period: int = 14) -> float:
    """ATR Wilder."""
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return sum(trs) / len(trs)
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def adx(candles: list[dict], period: int = 14) -> float | None:
    """ADX Wilder. None bila data kurang."""
    n = len(candles)
    if n < period * 2 + 1:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, n):
        up = candles[i]["high"] - candles[i - 1]["high"]
        dn = candles[i - 1]["low"] - candles[i]["low"]
        plus_dm.append(up if up > dn and up > 0 else 0.0)
        minus_dm.append(dn if dn > up and dn > 0 else 0.0)
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    def wilder(vals: list[float]) -> list[float]:
        s = sum(vals[:period])
        out = [s]
        for v in vals[period:]:
            s = s - s / period + v
            out.append(s)
        return out

    tr_s, p_s, m_s = wilder(trs), wilder(plus_dm), wilder(minus_dm)
    dx = []
    for t, p, m in zip(tr_s, p_s, m_s):
        if t == 0:
            dx.append(0.0)
            continue
        pdi, mdi = 100 * p / t, 100 * m / t
        dx.append(100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) else 0.0)
    if len(dx) < period:
        return None
    a = sum(dx[:period]) / period
    for v in dx[period:]:
        a = (a * (period - 1) + v) / period
    return a


def bb_squeeze(candles: list[dict], period: int = 20) -> dict | None:
    """Bandwidth Bollinger + persentil terhadap 100 candle terakhir."""
    closes = [c["close"] for c in candles]
    if len(closes) < period + 30:
        return None
    bws = []
    for i in range(period, len(closes) + 1):
        seg = closes[i - period:i]
        m = sum(seg) / period
        sd = (sum((x - m) ** 2 for x in seg) / period) ** 0.5
        bws.append((4 * sd) / m if m else 0.0)
    hist = bws[-100:]
    cur = hist[-1]
    rank = sum(1 for x in hist if x <= cur) / len(hist)
    return {"bandwidth": cur, "percentile": rank, "squeeze": rank <= 0.20}


def swing_pivots(candles: list[dict], left: int = 3, right: int = 3) -> tuple[list[float], list[float]]:
    highs, lows = [], []
    for i in range(left, len(candles) - right):
        h = candles[i]["high"]
        l = candles[i]["low"]
        if all(h >= candles[j]["high"] for j in range(i - left, i + right + 1)):
            highs.append(h)
        if all(l <= candles[j]["low"] for j in range(i - left, i + right + 1)):
            lows.append(l)
    return highs, lows


def cluster_levels(levels: list[float], tol: float) -> list[dict]:
    """Gabungkan level berdekatan (<= tol) menjadi zona; makin banyak sentuhan makin kuat."""
    if not levels:
        return []
    levels = sorted(levels)
    groups = [[levels[0]]]
    for v in levels[1:]:
        if v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [{"low": min(g), "high": max(g), "mid": sum(g) / len(g), "touches": len(g)}
            for g in groups]


def pip_size(symbol: str) -> float:
    s = symbol.upper().replace(" ", "")
    if s.startswith(("XAU", "GOLD")):
        return 0.1          # konvensi umum: 10 pips = 1 USD pada emas
    if s.startswith(("XAG", "SILVER")):
        return 0.01
    if "JPY" in s:
        return 0.01
    if s.startswith(("BTC", "ETH")):
        return 1.0
    if "/" in s and len(s.replace("/", "")) == 6:
        return 0.0001
    return 0.01


# ---------------------------------------------------------------------------
# Sesi pasar (UTC) — relevan untuk XAU / forex
# ---------------------------------------------------------------------------
def market_session(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    h = now.hour + now.minute / 60
    wd = now.weekday()  # 5=Sab, 6=Min
    if wd == 5 or (wd == 6 and h < 22) or (wd == 4 and h >= 22):
        return {"name": "Market tutup (weekend)", "good": False, "score": 0}
    london = 7 <= h < 16
    ny = 12 <= h < 21
    if london and ny:
        return {"name": "Overlap London–New York", "good": True, "score": 2}
    if london:
        return {"name": "London", "good": True, "score": 1}
    if ny:
        return {"name": "New York", "good": True, "score": 1}
    return {"name": "Asia / off-hours (likuiditas tipis)", "good": False, "score": 0}


# ---------------------------------------------------------------------------
# Trigger candle (pada candle close terakhir)
# ---------------------------------------------------------------------------
def candle_trigger(candles: list[dict], a: float) -> dict:
    """Deteksi pin bar & engulfing pada candle close terakhir. dir: +1/-1/0."""
    if len(candles) < 3 or a <= 0:
        return {"dir": 0, "name": "-"}
    c, p = candles[-1], candles[-2]
    body = abs(c["close"] - c["open"])
    rng = c["high"] - c["low"] or 1e-9
    upper = c["high"] - max(c["close"], c["open"])
    lower = min(c["close"], c["open"]) - c["low"]
    if rng < 0.5 * a:
        return {"dir": 0, "name": "candle terlalu kecil"}
    if lower >= 2 * body and lower >= 0.55 * rng and c["close"] >= c["low"] + 0.6 * rng:
        return {"dir": 1, "name": "bullish pin bar (rejection bawah)"}
    if upper >= 2 * body and upper >= 0.55 * rng and c["close"] <= c["high"] - 0.6 * rng:
        return {"dir": -1, "name": "bearish pin bar (rejection atas)"}
    p_body = abs(p["close"] - p["open"])
    if (c["close"] > c["open"] and p["close"] < p["open"] and body > p_body
            and c["close"] >= p["open"] and c["open"] <= p["close"]):
        return {"dir": 1, "name": "bullish engulfing"}
    if (c["close"] < c["open"] and p["close"] > p["open"] and body > p_body
            and c["close"] <= p["open"] and c["open"] >= p["close"]):
        return {"dir": -1, "name": "bearish engulfing"}
    return {"dir": 0, "name": "tidak ada pola trigger"}


# ---------------------------------------------------------------------------
# Hasil
# ---------------------------------------------------------------------------
@dataclass
class Gate:
    name: str
    ok: bool
    detail: str


@dataclass
class TradePlan:
    direction: str = "WAIT"            # BUY / SELL / WAIT
    order_type: str = "-"
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: list[float] = field(default_factory=list)
    risk_reward: str = "-"
    confluence: int = 0                # 0..100
    gates: list[Gate] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    scenarios: list[str] = field(default_factory=list)   # skenario kondisional saat WAIT
    zones: dict = field(default_factory=dict)
    atr_entry: float = 0.0
    atr_confirm: float = 0.0
    adx_confirm: float | None = None
    session: dict = field(default_factory=dict)
    htf_bias: int = 0
    pip: float = 0.0
    warnings: list[str] = field(default_factory=list)


def _tf_bias(power_signals: list[dict], tf_name: str) -> int:
    """Bias bobot per TF dari sinyal indikator (dikelompokkan per keluarga, bukan per indikator)."""
    fam = {
        "trend": ("ema200", "ema50200", "trenm5", "ichimoku", "ichimokutk"),
        "momentum": ("macd", "momentummy", "rsi"),
    }
    score = 0.0
    for fname, names in fam.items():
        sigs = [s["signal"] for s in power_signals if s["tf"] == tf_name and s["name"] in names]
        if sigs:
            score += (sum(sigs) / len(sigs)) * (1.5 if fname == "trend" else 1.0)
    return 1 if score > 0.6 else -1 if score < -0.6 else 0


def _parse_rr(rr: str) -> float:
    try:
        a, b = str(rr).replace(" ", "").split(":")
        return float(b) / float(a)
    except (ValueError, ZeroDivisionError):
        return 2.0


# ---------------------------------------------------------------------------
# Builder utama
# ---------------------------------------------------------------------------
def build_plan(symbol: str, candles_tf: dict[str, list[dict]], power, settings: dict,
               trading_mode: str, now: datetime | None = None) -> TradePlan:
    plan = TradePlan()
    names = list(candles_tf.keys())
    if not names:
        plan.warnings.append("Tidak ada data candle.")
        return plan
    entry_n = names[0]
    conf_n = names[1] if len(names) > 1 else names[0]
    trend_n = names[2] if len(names) > 2 else conf_n

    # Hanya candle close
    e_c = candles_tf[entry_n][:-1] if len(candles_tf[entry_n]) > 30 else candles_tf[entry_n]
    c_c = candles_tf[conf_n][:-1] if len(candles_tf[conf_n]) > 30 else candles_tf[conf_n]
    price = candles_tf[entry_n][-1]["close"]   # harga live (candle berjalan)
    plan.pip = pip_size(symbol)
    plan.atr_entry = atr(e_c)
    plan.atr_confirm = atr(c_c)
    plan.adx_confirm = adx(c_c)
    plan.session = market_session(now)
    a = plan.atr_entry or 1e-9

    # Bias TF
    sigs = power.signals if power else []
    b_entry = _tf_bias(sigs, entry_n)
    b_conf = _tf_bias(sigs, conf_n)
    b_trend = _tf_bias(sigs, trend_n)
    plan.htf_bias = b_trend if b_trend != 0 else b_conf

    # Zona S/R dari swing pivot (TF entry + konfirmasi), cluster pakai ATR
    hi_e, lo_e = swing_pivots(e_c[-150:])
    hi_c, lo_c = swing_pivots(c_c[-150:])
    tol = max(0.5 * a, 0.25 * plan.atr_confirm)
    res_zones = cluster_levels(hi_e + hi_c, tol)
    sup_zones = cluster_levels(lo_e + lo_c, tol)
    resist = sorted([z for z in res_zones if z["low"] > price], key=lambda z: z["low"])
    support = sorted([z for z in sup_zones if z["high"] < price], key=lambda z: -z["high"])
    plan.zones = {"support": support[:3], "resistance": resist[:3]}

    # --- Gate no-trade ---
    g = plan.gates
    if plan.adx_confirm is None:
        g.append(Gate("ADX", True, "data kurang, dilewati"))
    else:
        g.append(Gate("ADX", plan.adx_confirm >= 20,
                      f"ADX({conf_n}) = {plan.adx_confirm:.1f} "
                      f"({'tren cukup kuat' if plan.adx_confirm >= 20 else 'sideways <20'})"))
    sq = bb_squeeze(c_c)
    if sq:
        g.append(Gate("Volatilitas", not sq["squeeze"],
                      f"BB bandwidth persentil {sq['percentile']*100:.0f}% "
                      f"({'SQUEEZE — tunggu breakout' if sq['squeeze'] else 'normal'})"))
    # ATR terhadap ATR rata-rata -> hindari saat terlalu mati / ekstrem
    atr_hist = [atr(e_c[max(0, i - 60):i]) for i in range(len(e_c) - 40, len(e_c), 8) if i > 20]
    if atr_hist:
        avg = sum(atr_hist) / len(atr_hist)
        ratio = plan.atr_entry / avg if avg else 1
        g.append(Gate("ATR", 0.6 <= ratio <= 2.2, f"ATR sekarang {ratio:.2f}x rata-rata"))
    g.append(Gate("Sesi", plan.session["good"], plan.session["name"]))
    # Konsistensi arah: entry tidak boleh melawan konfirmasi+tren sekaligus
    htf_ok = not (b_entry != 0 and plan.htf_bias != 0 and b_entry != plan.htf_bias)
    g.append(Gate("Selaras TF", htf_ok,
                  f"bias Entry {b_entry:+d} / Konfirmasi {b_conf:+d} / Tren {b_trend:+d}"))

    # --- Arah kandidat: dari HTF, dikonfirmasi power ---
    buy_p, sell_p = (power.buy_pct, power.sell_pct) if power else (0, 0)
    edge = buy_p - sell_p
    direction = 0
    if plan.htf_bias > 0 and edge > 5:
        direction = 1
    elif plan.htf_bias < 0 and edge < -5:
        direction = -1
    elif plan.htf_bias == 0 and abs(edge) >= 20:
        direction = 1 if edge > 0 else -1

    trig = candle_trigger(e_c, a)
    near_zone = False
    zone_ref = None
    if direction > 0 and support:
        zone_ref = support[0]
        near_zone = price - zone_ref["high"] <= 1.2 * a
    elif direction < 0 and resist:
        zone_ref = resist[0]
        near_zone = zone_ref["low"] - price <= 1.2 * a

    # --- Confluence 0..100 ---
    score = 0
    if direction != 0:
        score += 20
        score += 15 if plan.htf_bias == direction else 0
        score += min(15, int(abs(edge) / 2))
        score += 15 if near_zone else 0
        score += 15 if trig["dir"] == direction else 0
        score += 10 if plan.session["score"] >= 1 else 0
        score += 10 if (plan.adx_confirm or 0) >= 20 else 0
    plan.confluence = min(100, score)

    hard_block = [x for x in g if not x.ok and x.name in ("ADX", "Volatilitas", "Selaras TF", "Sesi", "ATR")]
    rr = _parse_rr(settings.get("risk_reward", "1:2"))
    buffer = 0.5 * a

    if direction != 0 and not hard_block and plan.confluence >= 60 and zone_ref:
        if direction > 0:
            sl = zone_ref["low"] - buffer
            entry = price if (trig["dir"] > 0 and near_zone) else min(price, zone_ref["high"])
            plan.order_type = "Buy Market (konfirmasi trigger)" if entry == price else "Buy Limit"
        else:
            sl = zone_ref["high"] + buffer
            entry = price if (trig["dir"] < 0 and near_zone) else max(price, zone_ref["low"])
            plan.order_type = "Sell Market (konfirmasi trigger)" if entry == price else "Sell Limit"
        risk = abs(entry - sl)
        if risk < 1.0 * a:                 # SL terlalu mepet noise (min 1x ATR)
            risk = 1.0 * a
            sl = entry - risk if direction > 0 else entry + risk
        tp_main = entry + direction * risk * rr
        tps = [entry + direction * risk * 1.0, tp_main]
        # TP tidak boleh melewati zona lawan terdekat
        opp = resist[0] if direction > 0 and resist else support[0] if direction < 0 and support else None
        if opp:
            lim = opp["low"] if direction > 0 else opp["high"]
            if (direction > 0 and tp_main > lim) or (direction < 0 and tp_main < lim):
                tps = [entry + direction * risk * 1.0, lim]
                plan.warnings.append("TP2 dipotong di zona lawan terdekat.")
        real_rr = abs(tps[-1] - entry) / risk if risk else 0
        if real_rr < 1.5:
            plan.warnings.append(f"RR efektif {real_rr:.2f} < 1.5 — setup dibatalkan (WAIT).")
        else:
            plan.direction = "BUY" if direction > 0 else "SELL"
            plan.entry, plan.stop_loss = entry, sl
            plan.take_profit = tps
            plan.risk_reward = f"1:{real_rr:.1f}"
            plan.reasons.append(f"Arah {plan.direction} selaras HTF, power edge {edge:+.1f}%.")
            plan.reasons.append(f"Trigger: {trig['name']}; zona {zone_ref['low']:.2f}–{zone_ref['high']:.2f} "
                                f"({zone_ref['touches']}x sentuhan).")

    if plan.direction == "WAIT":
        for x in hard_block:
            plan.reasons.append(f"Gate gagal — {x.name}: {x.detail}")
        if direction == 0:
            plan.reasons.append("Tidak ada arah dominan (HTF netral / power seimbang).")
        elif plan.confluence < 60 and not hard_block:
            plan.reasons.append(f"Confluence {plan.confluence}/100 < 60 (belum cukup).")
        # Skenario kondisional: breakout valid = close candle di luar zona terdekat + ATR buffer
        if resist:
            lvl = resist[0]["high"]
            sl_b = lvl - 1.0 * a
            plan.scenarios.append(
                f"BUY jika candle {entry_n} close > {lvl + 0.2 * a:.2f} (lalu retest): "
                f"SL ≈ {sl_b:.2f}, TP1 ≈ {lvl + 1.5 * a:.2f}, TP2 ≈ {lvl + 3 * a:.2f}")
        if support:
            lvl = support[0]["low"]
            sl_b = lvl + 1.0 * a
            plan.scenarios.append(
                f"SELL jika candle {entry_n} close < {lvl - 0.2 * a:.2f} (lalu retest): "
                f"SL ≈ {sl_b:.2f}, TP1 ≈ {lvl - 1.5 * a:.2f}, TP2 ≈ {lvl - 3 * a:.2f}")

    # Cek target pips vs ATR konfirmasi
    tgt = settings.get("target_pips")
    if isinstance(tgt, (int, float)) and plan.pip and plan.atr_confirm:
        tgt_price = tgt * plan.pip
        if tgt_price > 3 * plan.atr_confirm:
            plan.warnings.append(
                f"Target {tgt} pips (~{tgt_price:.2f}) > 3x ATR {conf_n} ({plan.atr_confirm:.2f}) — "
                f"tidak realistis untuk mode {trading_mode}.")
    return plan


# ---------------------------------------------------------------------------
# Validator setup dari AI
# ---------------------------------------------------------------------------
def _num(x) -> float | None:
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def validate_setup(setup: dict | None, plan: TradePlan, price: float, settings: dict) -> tuple[dict | None, list[str]]:
    """Cek setup AI. Return (setup_final, catatan). Tolak/ganti bila melanggar aturan."""
    notes: list[str] = []
    if not setup:
        return setup, notes
    direction = str(setup.get("setup", "")).upper()
    if direction not in ("BUY", "SELL"):
        return setup, notes

    entry = _num(setup.get("entry"))
    sl = _num(setup.get("stop_loss"))
    tps_raw = setup.get("take_profit", [])
    tps = [t for t in (_num(x) for x in (tps_raw if isinstance(tps_raw, list) else [tps_raw])) if t is not None]
    a = plan.atr_entry or 1e-9
    problems = []

    if entry is None or sl is None or not tps:
        problems.append("entry/SL/TP tidak lengkap")
    else:
        sign = 1 if direction == "BUY" else -1
        if (entry - sl) * sign <= 0:
            problems.append("SL berada di sisi yang salah")
        if any((t - entry) * sign <= 0 for t in tps):
            problems.append("TP berada di sisi yang salah")
        if abs(entry - price) > 3 * a:
            problems.append(f"entry terlalu jauh dari harga ({abs(entry - price):.2f} > 3x ATR)")
        risk = abs(entry - sl)
        if risk < 0.5 * a:
            problems.append("SL terlalu dekat (< 0.5 ATR)")
        elif risk > 3 * a:
            problems.append("SL terlalu jauh (> 3 ATR)")
        if risk and abs(tps[-1] - entry) / risk < 1.5:
            problems.append("RR < 1:1.5")

    wanted = 1 if direction == "BUY" else -1
    if plan.htf_bias != 0 and plan.htf_bias != wanted:
        problems.append("melawan bias timeframe besar")
    blocking = [g.name for g in plan.gates if not g.ok and g.name in ("ADX", "Volatilitas", "Selaras TF", "Sesi", "ATR")]
    if blocking:
        problems.append("gate gagal: " + ", ".join(blocking))

    if not problems:
        notes.append("Setup AI lolos validasi lokal.")
        return setup, notes

    notes.append("Setup AI ditolak: " + "; ".join(problems))
    out = dict(setup)
    if plan.direction == direction:
        out.update({"entry": plan.entry, "stop_loss": plan.stop_loss,
                    "take_profit": plan.take_profit, "risk_reward": plan.risk_reward,
                    "order_type": plan.order_type})
        notes.append("Entry/SL/TP diganti dengan hitungan lokal (ATR + zona).")
    else:
        out.update({"setup": "WAIT", "order_type": "-", "entry": "None",
                    "stop_loss": "None", "take_profit": [], "risk_reward": "0"})
        notes.append("Diubah jadi WAIT.")
    return out, notes
