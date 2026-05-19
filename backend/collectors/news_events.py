"""
backend/collectors/news_events.py

新聞 / 事件收集器。

目前先做「結構化事件代理」：不假裝抓到真正新聞，而是把資料庫中已公開的
月營收、異常價量、法人籌碼變化轉成 news_events，讓 news_score 不再永遠固定 50。

重點：
- 只使用 target_date 當下已存在、且日期 <= target_date 的資料，避免回測偷看未來。
- 之後若要串真實新聞 RSS / 官方公告，只要寫進同一張 news_events 表，scorer 會自動吃。
"""
from datetime import date, datetime, timedelta
from loguru import logger
from sqlalchemy import text

from backend.models.database import SessionLocal, NewsEvent


SOURCE = "structured_proxy"



def _as_date(x):
    """把 SQLite / SQLAlchemy 可能回傳的字串、datetime、date 統一轉成 date。"""
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    text_value = str(x).strip()
    if not text_value:
        return None
    # SQLite 常見格式：YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS
    try:
        return date.fromisoformat(text_value[:10])
    except ValueError:
        return None

def _safe_float(x, default=None):
    if x is None:
        return default
    try:
        if isinstance(x, str):
            x = x.strip().replace(",", "").replace("%", "")
            if x in ("", "-", "None", "nan", "NaN"):
                return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _latest_trade_date(db, target_date: date):
    row = db.execute(
        text("""
            SELECT MAX(trade_date)
            FROM ohlcv_daily
            WHERE trade_date <= :d
        """),
        {"d": target_date},
    ).fetchone()
    return _as_date(row[0]) if row and row[0] is not None else None


def _latest_codes(db, trade_date) -> list[str]:
    rows = db.execute(
        text("""
            SELECT DISTINCT code
            FROM ohlcv_daily
            WHERE trade_date = :d
            ORDER BY code
        """),
        {"d": trade_date},
    ).fetchall()
    return [str(r[0]).strip() for r in rows if r[0]]


def _exists(db, code: str, event_date, event_type: str, title: str) -> bool:
    row = db.execute(
        text("""
            SELECT 1
            FROM news_events
            WHERE code = :code
              AND event_date = :event_date
              AND event_type = :event_type
              AND title = :title
              AND source = :source
            LIMIT 1
        """),
        {
            "code": code,
            "event_date": event_date,
            "event_type": event_type,
            "title": title,
            "source": SOURCE,
        },
    ).fetchone()
    return row is not None


def _add_event(db, code: str, event_date, event_type: str, title: str,
               sentiment: float, importance: float):
    event_date = _as_date(event_date)
    if event_date is None:
        return False

    if _exists(db, code, event_date, event_type, title):
        return False

    db.add(NewsEvent(
        code=code,
        event_date=event_date,
        event_type=event_type,
        title=title,
        sentiment=_clip(float(sentiment), -1.0, 1.0),
        importance=_clip(float(importance), 0.05, 1.0),
        source=SOURCE,
        url=None,
    ))
    return True


def _price_event(db, code: str, target_date: date):
    rows = db.execute(
        text("""
            SELECT trade_date, close, volume
            FROM ohlcv_daily
            WHERE code = :code
              AND trade_date <= :d
              AND close IS NOT NULL
            ORDER BY trade_date DESC
            LIMIT 21
        """),
        {"code": code, "d": target_date},
    ).fetchall()
    if len(rows) < 2:
        return None

    rows = list(reversed(rows))
    trade_date = rows[-1][0]
    closes = [_safe_float(r[1]) for r in rows]
    vols = [_safe_float(r[2]) for r in rows]
    if closes[-1] is None or closes[-2] is None or closes[-2] <= 0:
        return None

    ret_1d = (closes[-1] / closes[-2] - 1) * 100
    ret_5d = None
    if len(closes) >= 6 and closes[-6] and closes[-6] > 0:
        ret_5d = (closes[-1] / closes[-6] - 1) * 100

    valid_prev_vols = [v for v in vols[:-1] if v is not None and v > 0]
    vol_ratio = None
    if len(valid_prev_vols) >= 5 and vols[-1] is not None and vols[-1] > 0:
        avg_vol = sum(valid_prev_vols[-20:]) / len(valid_prev_vols[-20:])
        vol_ratio = vols[-1] / (avg_vol + 1e-9)

    is_large_1d = abs(ret_1d) >= 3.0
    is_large_5d = ret_5d is not None and abs(ret_5d) >= 8.0
    is_volume_shock = vol_ratio is not None and vol_ratio >= 1.8 and abs(ret_1d) >= 1.5

    if not (is_large_1d or is_large_5d or is_volume_shock):
        return None

    sign = 1 if (ret_5d if ret_5d is not None and abs(ret_5d) > abs(ret_1d) else ret_1d) >= 0 else -1
    magnitude = max(abs(ret_1d), abs(ret_5d or 0.0))
    if vol_ratio is not None:
        magnitude *= min(max(vol_ratio, 0.8), 2.0) / 1.2

    sentiment = sign * _clip(magnitude / 10.0, 0.15, 1.0)
    importance = _clip(magnitude / 12.0, 0.25, 0.90)

    direction = "強勢上漲" if sign > 0 else "明顯轉弱"
    vol_text = "，且成交量放大" if vol_ratio is not None and vol_ratio >= 1.8 else ""
    if ret_5d is not None:
        title = f"{code} 價量事件：1日{ret_1d:+.2f}%、5日{ret_5d:+.2f}% {direction}{vol_text}"
    else:
        title = f"{code} 價量事件：1日{ret_1d:+.2f}% {direction}{vol_text}"

    return {
        "code": code,
        "event_date": trade_date,
        "event_type": "news",
        "title": title,
        "sentiment": sentiment,
        "importance": importance,
    }


def _revenue_event(db, code: str, target_date: date):
    start = target_date - timedelta(days=10)
    row = db.execute(
        text("""
            SELECT published_date, year, month, yoy_pct, mom_pct
            FROM monthly_revenue
            WHERE code = :code
              AND published_date BETWEEN :start AND :end
            ORDER BY published_date DESC, year DESC, month DESC
            LIMIT 1
        """),
        {"code": code, "start": start, "end": target_date},
    ).fetchone()
    if not row:
        return None

    published_date, year, month, yoy, mom = row
    yoy = _safe_float(yoy)
    mom = _safe_float(mom)
    if yoy is None and mom is None:
        return None

    signal = 0.0
    weight = 0.0
    if yoy is not None:
        signal += _clip(yoy / 40.0, -1.0, 1.0) * 0.75
        weight += 0.75
    if mom is not None:
        signal += _clip(mom / 20.0, -1.0, 1.0) * 0.25
        weight += 0.25
    sentiment = signal / weight if weight > 0 else 0.0

    if abs(sentiment) < 0.12:
        return None

    direction = "優於中性" if sentiment > 0 else "弱於中性"
    title = f"{code} 月營收事件：{year}/{month:02d} YoY {yoy if yoy is not None else 0:+.2f}%，MoM {mom if mom is not None else 0:+.2f}%，{direction}"

    return {
        "code": code,
        "event_date": published_date,
        "event_type": "news",
        "title": title,
        "sentiment": sentiment,
        "importance": _clip(abs(sentiment), 0.25, 0.80),
    }


def _chip_event(db, code: str, target_date: date):
    rows = db.execute(
        text("""
            SELECT trade_date, foreign_net, trust_net, dealer_net
            FROM chip_daily
            WHERE code = :code
              AND trade_date <= :d
            ORDER BY trade_date DESC
            LIMIT 5
        """),
        {"code": code, "d": target_date},
    ).fetchall()
    if len(rows) < 3:
        return None

    net_values = []
    latest_trade_date = rows[0][0]
    for _, f, t, d in rows:
        net_values.append(_safe_float(f, 0.0) + _safe_float(t, 0.0) + _safe_float(d, 0.0))

    net_5d = sum(net_values)

    vol_row = db.execute(
        text("""
            SELECT AVG(volume)
            FROM (
                SELECT volume
                FROM ohlcv_daily
                WHERE code = :code
                  AND trade_date <= :d
                  AND volume IS NOT NULL
                ORDER BY trade_date DESC
                LIMIT 20
            )
        """),
        {"code": code, "d": target_date},
    ).fetchone()
    avg_volume = _safe_float(vol_row[0] if vol_row else None)
    if avg_volume is None or avg_volume <= 0:
        return None

    flow_ratio = net_5d / max(avg_volume / 1000.0, 1.0)
    if abs(flow_ratio) < 0.15:
        return None

    sentiment = _clip(flow_ratio / 0.35, -1.0, 1.0)
    direction = "法人偏多" if sentiment > 0 else "法人偏空"
    title = f"{code} 籌碼事件：近5日三大法人合計 {net_5d:+.0f}，{direction}"

    return {
        "code": code,
        "event_date": latest_trade_date,
        "event_type": "news",
        "title": title,
        "sentiment": sentiment,
        "importance": _clip(abs(sentiment), 0.20, 0.75),
    }


def run_news(target_date: date | None = None):
    """產生結構化新聞事件代理資料。"""
    if target_date is None:
        target_date = date.today()
    else:
        target_date = _as_date(target_date)

    if target_date is None:
        raise ValueError("target_date 無法轉成 date")

    db = SessionLocal()
    created = 0
    try:
        latest = _latest_trade_date(db, target_date)
        if latest is None:
            logger.warning("[NEWS] ohlcv_daily 無資料，無法產生事件")
            return

        codes = _latest_codes(db, latest)
        for code in codes:
            for factory in (_revenue_event, _price_event, _chip_event):
                event = factory(db, code, target_date)
                if event is None:
                    continue
                if _add_event(db, **event):
                    created += 1

        db.commit()
        logger.success(f"[NEWS] {target_date} 結構化事件產生完成：新增 {created} 筆")
    except Exception as exc:
        db.rollback()
        logger.error(f"[NEWS] 產生事件失敗: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_news()
