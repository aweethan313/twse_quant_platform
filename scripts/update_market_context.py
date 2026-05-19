"""
V4.6.1 Market context rebuild

核心修正：
1. 實際日 K 表為 ohlcv_daily，不是 daily_price。
2. 市場廣度、主線題材、量價結構都排除疑似 stale OHLCV。
3. 不直接相信 ohlcv_daily.change_pct，改用 close / 前一筆 close 重新計算。
4. 夜盤 / 美股缺資料時明確以 50 中性處理，避免誤以為有分析。
5. 僅用 context_date 當日以前資料，不偷看未來。
"""

from __future__ import annotations

import argparse
import csv
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any


DB_PATH = Path("data/db/quant.db")
OVERNIGHT_CSV = Path("data/external/overnight_market.csv")

THEME_CODES: dict[str, set[str]] = {
    "AI/半導體": {
        "2330", "2454", "2308", "2382", "2383", "2449", "3037", "3189", "3711",
        "6205", "2356", "2357", "2324", "2317", "3231", "6669", "3443", "3661",
        "3017", "2368", "6415", "8046", "3533", "5274", "4966", "5347", "4968",
        "3035", "3529", "3653", "3014", "3406",
    },
    "PCB/載板": {
        "2383", "3037", "3189", "8046", "2368", "2313", "4927", "4958", "3533",
        "6274", "6191", "5469", "6153", "6213", "6269",
    },
    "電源/散熱": {
        "2308", "3017", "2421", "3324", "3653", "8996", "6121", "6230", "3015",
        "3338", "6271", "6412",
    },
    "金融": {
        "2881", "2882", "2883", "2884", "2885", "2886", "2887", "2888", "2890",
        "2891", "2892", "2801", "2834", "2836", "2845", "2812", "2820",
    },
    "傳產/航運": {
        "1101", "1102", "1216", "1301", "1303", "2002", "2603", "2609", "2615",
        "2618", "2633", "2201", "2207", "2371",
    },
}


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_float(x: Any, default: float | None = 0.0) -> float | None:
    if x is None or x == "":
        return default
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _is_common_stock(code: str) -> bool:
    """台股普通股多為 4 位數。排除 ETF/權證/債券/特別股等，以免市場廣度失真。"""
    code = str(code)
    if not (len(code) == 4 and code.isdigit()):
        return False
    if code.startswith("00"):
        return False
    return True


def _date_sub(date_str: str, days: int) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return (d - timedelta(days=days)).isoformat()


def _ensure_tables(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS market_context_daily (
        context_date DATE PRIMARY KEY,
        market_bias_score REAL,
        next_day_bias TEXT,
        trend_regime TEXT,
        up_count INTEGER,
        down_count INTEGER,
        flat_count INTEGER,
        up_ratio REAL,
        avg_change_pct REAL,
        median_change_pct REAL,
        total_value REAL,
        market_volume_ratio REAL,
        avg_open_to_close_pct REAL,
        avg_close_position REAL,
        breadth_score REAL,
        volume_score REAL,
        overnight_score REAL,
        nasdaq_ret REAL,
        sox_ret REAL,
        qqq_ret REAL,
        sp500_ret REAL,
        tw_futures_ret REAL,
        top_theme TEXT,
        top_theme_score REAL,
        ai_theme_score REAL,
        semicon_theme_score REAL,
        summary TEXT,
        created_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS theme_trend_daily (
        context_date DATE NOT NULL,
        theme TEXT NOT NULL,
        score REAL,
        momentum_score REAL,
        breadth_score REAL,
        volume_ratio REAL,
        code_count INTEGER,
        leader_codes TEXT,
        keyword_hits INTEGER,
        summary TEXT,
        PRIMARY KEY (context_date, theme)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS stock_structure_daily (
        feature_date DATE NOT NULL,
        code TEXT NOT NULL,
        open_to_close_pct REAL,
        close_position REAL,
        volume_ratio REAL,
        buy_volume REAL,
        sell_volume REAL,
        buy_sell_ratio REAL,
        PRIMARY KEY (feature_date, code)
    )
    """)
    conn.commit()


def _load_overnight(csv_path: Path = OVERNIGHT_CSV) -> dict[str, dict[str, float | None]]:
    if not csv_path.exists():
        return {}

    out: dict[str, dict[str, float | None]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = (row.get("context_date") or "").strip()
            if not d:
                continue
            item: dict[str, float | None] = {}
            for k in ["nasdaq_ret", "sox_ret", "qqq_ret", "sp500_ret", "tw_futures_ret"]:
                raw = (row.get(k) or "").strip()
                item[k] = None if raw == "" else _safe_float(raw, None)
            out[d] = item
    return out


def _overnight_score(item: dict[str, float | None] | None) -> tuple[float, bool, dict[str, float | None]]:
    empty = {"nasdaq_ret": None, "sox_ret": None, "qqq_ret": None, "sp500_ret": None, "tw_futures_ret": None}
    if not item:
        return 50.0, False, empty

    weights = {
        "nasdaq_ret": 6.0,
        "sox_ret": 9.0,
        "qqq_ret": 6.0,
        "sp500_ret": 4.0,
        "tw_futures_ret": 10.0,
    }
    total = 0.0
    used = 0
    for k, w in weights.items():
        v = item.get(k)
        if v is not None:
            # CSV 欄位用百分比，例如 1.2 表示 +1.2%。
            total += float(v) * w
            used += 1

    if used == 0:
        return 50.0, False, item
    return _clip(50.0 + total), True, item


def _trade_dates(conn: sqlite3.Connection, start_date: str | None, end_date: str | None) -> list[str]:
    params: dict[str, str] = {}
    wheres = []
    if start_date:
        wheres.append("trade_date >= :start")
        params["start"] = start_date
    if end_date:
        wheres.append("trade_date <= :end")
        params["end"] = end_date
    where_sql = "WHERE " + " AND ".join(wheres) if wheres else ""
    rows = conn.execute(
        f"SELECT DISTINCT trade_date FROM ohlcv_daily {where_sql} ORDER BY trade_date",
        params,
    ).fetchall()
    return [str(r[0]) for r in rows]


def _latest_trade_date(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT MAX(trade_date) FROM ohlcv_daily").fetchone()
    if not row or not row[0]:
        raise RuntimeError("ohlcv_daily 沒有資料")
    return str(row[0])


def _load_rows_with_history(conn: sqlite3.Connection, start_date: str, end_date: str, lookback_days: int = 90) -> dict[str, list[dict[str, Any]]]:
    hist_start = _date_sub(start_date, lookback_days)
    rows = conn.execute("""
        SELECT code, trade_date, open, high, low, close, volume, value, change_pct
        FROM ohlcv_daily
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY code, trade_date
    """, (hist_start, end_date)).fetchall()

    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_code[str(r["code"])].append({
            "code": str(r["code"]),
            "trade_date": str(r["trade_date"]),
            "open": _safe_float(r["open"], None),
            "high": _safe_float(r["high"], None),
            "low": _safe_float(r["low"], None),
            "close": _safe_float(r["close"], None),
            "volume": _safe_float(r["volume"], 0.0),
            "value": _safe_float(r["value"], 0.0),
            "stored_change_pct": _safe_float(r["change_pct"], None),
        })
    return by_code


def _compute_features(by_code: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for code, rows in by_code.items():
        prev_close: float | None = None
        prev_key: tuple[float | None, ...] | None = None
        stale_streak = 1
        vol_window: list[float] = []

        for item in rows:
            o, h, l, c = item["open"], item["high"], item["low"], item["close"]
            v = _safe_float(item["volume"], 0.0) or 0.0

            key = (o, h, l, c, v)
            if prev_key is not None and key == prev_key:
                stale_streak += 1
            else:
                stale_streak = 1

            if prev_close is not None and prev_close > 0 and c is not None and c > 0:
                actual_change_pct = (c / prev_close - 1.0) * 100.0
            else:
                actual_change_pct = item["stored_change_pct"] if item["stored_change_pct"] is not None else 0.0

            avg20_vol = sum(vol_window[-20:]) / len(vol_window[-20:]) if vol_window[-20:] else None
            volume_ratio = (v / avg20_vol) if avg20_vol and avg20_vol > 0 else 1.0

            if o and o > 0 and c is not None:
                open_to_close_pct = (c / o - 1.0) * 100.0
            else:
                open_to_close_pct = 0.0

            if h is not None and l is not None and c is not None and h > l:
                close_position = (c - l) / (h - l) * 100.0
            else:
                close_position = 50.0

            feature = dict(item)
            feature.update({
                "actual_change_pct": actual_change_pct,
                "stale_streak": stale_streak,
                "volume_ratio": volume_ratio,
                "open_to_close_pct": open_to_close_pct,
                "close_position": close_position,
                "is_common_stock": _is_common_stock(code),
            })
            by_date[item["trade_date"]].append(feature)

            prev_close = c
            prev_key = key
            vol_window.append(v)

    return by_date


def _active_rows(rows: list[dict[str, Any]], stale_days: int) -> list[dict[str, Any]]:
    selected = []
    for r in rows:
        if not r["is_common_stock"]:
            continue
        if r["close"] is None or r["close"] <= 0:
            continue
        if r["open"] is None or r["high"] is None or r["low"] is None:
            continue
        if (r["volume"] or 0) <= 0:
            continue
        if r["stale_streak"] >= stale_days:
            continue
        # 台股個股日漲跌通常有漲跌幅限制；過大多半是資料錯、分割、異常商品。
        if abs(float(r["actual_change_pct"])) > 15:
            continue
        selected.append(r)
    return selected


def _score_volume(volume_ratio: float) -> float:
    # 1.0 約 50 分；1.5 約 65 分；2.0 約 80 分；過低降分。
    return _clip(50.0 + (volume_ratio - 1.0) * 30.0)


def _compute_theme_scores(date_str: str, active: list[dict[str, Any]], news_hits: dict[str, int] | None = None) -> list[dict[str, Any]]:
    by_code = {r["code"]: r for r in active}
    results: list[dict[str, Any]] = []

    for theme, codes in THEME_CODES.items():
        items = [by_code[c] for c in codes if c in by_code]
        if not items:
            results.append({
                "context_date": date_str, "theme": theme, "score": 0.0,
                "momentum_score": 0.0, "breadth_score": 0.0, "volume_ratio": 0.0,
                "code_count": 0, "leader_codes": "", "keyword_hits": 0,
                "summary": f"{theme}: 無有效活躍股票",
            })
            continue

        changes = [float(x["actual_change_pct"]) for x in items]
        vols = [float(x["volume_ratio"]) for x in items]
        up_ratio = sum(1 for x in changes if x > 0) / len(changes)
        avg_chg = sum(changes) / len(changes)
        med_vol = median(vols) if vols else 1.0

        momentum_score = _clip(50.0 + avg_chg * 8.0)
        breadth_score = _clip(up_ratio * 100.0)
        volume_score = _score_volume(med_vol)
        keyword_hits = (news_hits or {}).get(theme, 0)

        score = _clip(momentum_score * 0.45 + breadth_score * 0.35 + volume_score * 0.20 + min(keyword_hits, 5) * 1.5)
        leaders = sorted(items, key=lambda x: x["actual_change_pct"], reverse=True)[:5]
        leader_codes = ",".join(x["code"] for x in leaders)

        results.append({
            "context_date": date_str,
            "theme": theme,
            "score": score,
            "momentum_score": momentum_score,
            "breadth_score": breadth_score,
            "volume_ratio": med_vol,
            "code_count": len(items),
            "leader_codes": leader_codes,
            "keyword_hits": keyword_hits,
            "summary": f"{theme}: score={score:.1f}, active={len(items)}, leaders={leader_codes}",
        })

    return results


def _write_stock_structure(conn: sqlite3.Connection, date_str: str, active: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM stock_structure_daily WHERE feature_date = ?", (date_str,))
    rows = []
    for r in active:
        rows.append((
            date_str,
            r["code"],
            float(r["open_to_close_pct"]),
            float(r["close_position"]),
            float(r["volume_ratio"]),
            None,
            None,
            None,
        ))
    conn.executemany("""
        INSERT OR REPLACE INTO stock_structure_daily
        (feature_date, code, open_to_close_pct, close_position, volume_ratio, buy_volume, sell_volume, buy_sell_ratio)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)


def _write_theme_scores(conn: sqlite3.Connection, date_str: str, theme_rows: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM theme_trend_daily WHERE context_date = ?", (date_str,))
    conn.executemany("""
        INSERT OR REPLACE INTO theme_trend_daily
        (context_date, theme, score, momentum_score, breadth_score, volume_ratio, code_count, leader_codes, keyword_hits, summary)
        VALUES (:context_date, :theme, :score, :momentum_score, :breadth_score, :volume_ratio, :code_count, :leader_codes, :keyword_hits, :summary)
    """, theme_rows)


def _write_market_context(
    conn: sqlite3.Connection,
    date_str: str,
    all_rows: list[dict[str, Any]],
    active: list[dict[str, Any]],
    theme_rows: list[dict[str, Any]],
    overnight_item: dict[str, float | None] | None,
) -> None:
    if active:
        change_pcts = [float(r["actual_change_pct"]) for r in active]
        up_count = sum(1 for x in change_pcts if x > 0.001)
        down_count = sum(1 for x in change_pcts if x < -0.001)
        flat_count = len(change_pcts) - up_count - down_count
        up_ratio = up_count / len(change_pcts)
        avg_change = sum(change_pcts) / len(change_pcts)
        med_change = median(change_pcts)
        total_value = sum(float(r["value"] or 0.0) for r in active)
        volume_ratios = [float(r["volume_ratio"]) for r in active]
        market_volume_ratio = median(volume_ratios) if volume_ratios else 1.0
        avg_open_to_close = sum(float(r["open_to_close_pct"]) for r in active) / len(active)
        avg_close_position = sum(float(r["close_position"]) for r in active) / len(active)
        strong_spread = (
            sum(1 for x in change_pcts if x >= 3.0) -
            sum(1 for x in change_pcts if x <= -3.0)
        ) / len(change_pcts)
    else:
        up_count = down_count = flat_count = 0
        up_ratio = 0.5
        avg_change = med_change = total_value = 0.0
        market_volume_ratio = 1.0
        avg_open_to_close = 0.0
        avg_close_position = 50.0
        strong_spread = 0.0

    breadth_score = _clip(50.0 + (up_ratio - 0.5) * 70.0 + avg_change * 5.0 + strong_spread * 20.0)
    volume_score = _score_volume(market_volume_ratio)
    overnight_score, has_overnight, overnight_values = _overnight_score(overnight_item)

    top_theme_row = max(theme_rows, key=lambda x: x["score"]) if theme_rows else None
    top_theme = top_theme_row["theme"] if top_theme_row else ""
    top_theme_score = float(top_theme_row["score"]) if top_theme_row else 0.0
    ai_theme_score = next((float(x["score"]) for x in theme_rows if x["theme"] == "AI/半導體"), 0.0)
    semicon_theme_score = ai_theme_score

    market_bias_score = _clip(
        breadth_score * 0.45 +
        volume_score * 0.15 +
        overnight_score * 0.25 +
        top_theme_score * 0.15
    )

    if market_bias_score >= 60:
        next_day_bias = "偏多"
    elif market_bias_score >= 45:
        next_day_bias = "中性"
    else:
        next_day_bias = "偏空"

    if breadth_score >= 58 and avg_change > 0.1:
        trend_regime = "多頭/輪動"
    elif breadth_score <= 38 and avg_change < -0.05:
        trend_regime = "空頭/修正"
    else:
        trend_regime = "震盪"

    stale_count = sum(1 for r in all_rows if r.get("is_common_stock") and r.get("stale_streak", 0) >= 5)
    all_common = sum(1 for r in all_rows if r.get("is_common_stock"))
    overnight_note = "" if has_overnight else "；夜盤/美股缺資料，以50中性處理"
    summary = (
        f"{date_str} {next_day_bias}，{trend_regime}；"
        f"active={len(active)}/{all_common}，stale排除={stale_count}；"
        f"上漲={up_count}，下跌={down_count}，平盤={flat_count}，"
        f"均漲跌={avg_change:+.2f}%；主線={top_theme}({top_theme_score:.1f})"
        f"{overnight_note}"
    )

    conn.execute("""
        INSERT OR REPLACE INTO market_context_daily
        (context_date, market_bias_score, next_day_bias, trend_regime,
         up_count, down_count, flat_count, up_ratio, avg_change_pct, median_change_pct,
         total_value, market_volume_ratio, avg_open_to_close_pct, avg_close_position,
         breadth_score, volume_score, overnight_score,
         nasdaq_ret, sox_ret, qqq_ret, sp500_ret, tw_futures_ret,
         top_theme, top_theme_score, ai_theme_score, semicon_theme_score,
         summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        date_str, market_bias_score, next_day_bias, trend_regime,
        up_count, down_count, flat_count, up_ratio, avg_change, med_change,
        total_value, market_volume_ratio, avg_open_to_close, avg_close_position,
        breadth_score, volume_score, overnight_score,
        overnight_values.get("nasdaq_ret"), overnight_values.get("sox_ret"),
        overnight_values.get("qqq_ret"), overnight_values.get("sp500_ret"),
        overnight_values.get("tw_futures_ret"),
        top_theme, top_theme_score, ai_theme_score, semicon_theme_score,
        summary, datetime.now().isoformat(timespec="seconds"),
    ))

    print(
        f"[MARKET_CONTEXT] {summary}；"
        f"breadth_score={breadth_score:.1f}, overnight_score={overnight_score:.1f}, "
        f"market_bias={market_bias_score:.1f}"
    )


def update_context_range(
    start_date: str | None = None,
    end_date: str | None = None,
    db_path: Path = DB_PATH,
    stale_days: int = 5,
) -> None:
    conn = _connect(db_path)
    _ensure_tables(conn)

    if start_date is None and end_date is None:
        start_date = end_date = _latest_trade_date(conn)
    elif start_date is None:
        start_date = end_date
    elif end_date is None:
        end_date = start_date

    assert start_date is not None and end_date is not None

    dates = _trade_dates(conn, start_date, end_date)
    if not dates:
        raise RuntimeError(f"找不到 ohlcv_daily 交易日：{start_date} ~ {end_date}")

    by_code = _load_rows_with_history(conn, dates[0], dates[-1])
    by_date = _compute_features(by_code)
    overnight = _load_overnight()

    for d in dates:
        all_rows = by_date.get(d, [])
        active = _active_rows(all_rows, stale_days=stale_days)

        _write_stock_structure(conn, d, active)
        theme_rows = _compute_theme_scores(d, active)
        _write_theme_scores(conn, d, theme_rows)
        _write_market_context(conn, d, all_rows, active, theme_rows, overnight.get(d))

    conn.commit()
    conn.close()


# 保留多種函式名稱，避免其他腳本 import 舊名稱時失效。
def run_market_context(context_date: str | None = None, start_date: str | None = None, end_date: str | None = None, **kwargs: Any) -> None:
    if context_date and not start_date and not end_date:
        update_context_range(context_date, context_date, **kwargs)
    else:
        update_context_range(start_date, end_date, **kwargs)


def update_market_context(context_date: str | None = None, **kwargs: Any) -> None:
    run_market_context(context_date=context_date, **kwargs)


def run(context_date: str | None = None, **kwargs: Any) -> None:
    run_market_context(context_date=context_date, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--db-path", default=str(DB_PATH))
    parser.add_argument("--stale-days", type=int, default=5)
    args = parser.parse_args()

    update_context_range(
        start_date=args.start_date,
        end_date=args.end_date,
        db_path=Path(args.db_path),
        stale_days=args.stale_days,
    )


if __name__ == "__main__":
    main()
