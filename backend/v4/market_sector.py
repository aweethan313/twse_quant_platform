"""
backend/v4/market_sector.py
V4-13：股市分類系統
"""
from __future__ import annotations
import json
from datetime import date, datetime
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal

# 核心分類資料庫（代號 → 分類）
SECTOR_MAP = {
    # AI/半導體
    "2330":{"primary":"AI/半導體","secondary":"晶圓代工","tags":["AI","半導體","先進製程","HPC"],"risk":"LOW","defensive":False},
    "2454":{"primary":"AI/半導體","secondary":"IC設計","tags":["AI","半導體","IC設計","HPC"],"risk":"MEDIUM","defensive":False},
    "3443":{"primary":"AI/半導體","secondary":"IC設計","tags":["AI","半導體","ASIC"],"risk":"HIGH","defensive":False},
    "3661":{"primary":"AI/半導體","secondary":"IC設計","tags":["AI","半導體","ASIC","HPC"],"risk":"HIGH","defensive":False},
    "3034":{"primary":"AI/半導體","secondary":"IC設計","tags":["半導體","IC設計","驅動IC"],"risk":"MEDIUM","defensive":False},
    "2379":{"primary":"AI/半導體","secondary":"IC設計","tags":["半導體","IC設計","網通IC"],"risk":"MEDIUM","defensive":False},
    "5274":{"primary":"AI/半導體","secondary":"IC設計","tags":["AI","半導體","BMC","伺服器"],"risk":"HIGH","defensive":False},
    "6271":{"primary":"AI/半導體","secondary":"封裝測試","tags":["半導體","封裝","感測器"],"risk":"MEDIUM","defensive":False},
    "3711":{"primary":"AI/半導體","secondary":"封裝測試","tags":["半導體","封測","先進封裝"],"risk":"MEDIUM","defensive":False},
    "2303":{"primary":"AI/半導體","secondary":"晶圓代工","tags":["半導體","晶圓代工"],"risk":"MEDIUM","defensive":False},

    # AI伺服器/電子代工
    "2382":{"primary":"AI伺服器","secondary":"ODM","tags":["AI伺服器","ODM","電子代工"],"risk":"MEDIUM","defensive":False},
    "2356":{"primary":"AI伺服器","secondary":"ODM","tags":["AI伺服器","ODM","伺服器組裝"],"risk":"MEDIUM","defensive":False},
    "3231":{"primary":"AI伺服器","secondary":"ODM","tags":["AI伺服器","ODM","電子代工"],"risk":"MEDIUM","defensive":False},
    "6669":{"primary":"AI伺服器","secondary":"CSP伺服器","tags":["AI伺服器","CSP","雲端"],"risk":"HIGH","defensive":False},
    "2317":{"primary":"AI伺服器","secondary":"EMS","tags":["AI伺服器","EMS","電子代工"],"risk":"LOW","defensive":False},
    "4938":{"primary":"AI伺服器","secondary":"ODM","tags":["AI伺服器","ODM","電子代工"],"risk":"MEDIUM","defensive":False},
    "2324":{"primary":"AI伺服器","secondary":"ODM","tags":["AI伺服器","ODM","PC/NB"],"risk":"MEDIUM","defensive":False},
    "2357":{"primary":"AI伺服器","secondary":"品牌PC","tags":["品牌PC","電子代工"],"risk":"MEDIUM","defensive":False},

    # 電源/散熱
    "2308":{"primary":"電源/散熱","secondary":"電源管理","tags":["電源","散熱","AI伺服器","工業"],"risk":"LOW","defensive":True},
    "3653":{"primary":"電源/散熱","secondary":"散熱","tags":["散熱","AI伺服器","均熱板"],"risk":"HIGH","defensive":False},
    "3017":{"primary":"電源/散熱","secondary":"散熱","tags":["散熱","AI伺服器","水冷"],"risk":"HIGH","defensive":False},
    "3324":{"primary":"電源/散熱","secondary":"散熱","tags":["散熱","AI伺服器","液冷"],"risk":"HIGH","defensive":False},
    "2421":{"primary":"電源/散熱","secondary":"散熱","tags":["散熱","風扇","AI伺服器"],"risk":"MEDIUM","defensive":False},

    # PCB/載板
    "3037":{"primary":"PCB/載板","secondary":"ABF載板","tags":["PCB","ABF","載板","AI伺服器"],"risk":"HIGH","defensive":False},
    "3189":{"primary":"PCB/載板","secondary":"ABF載板","tags":["PCB","ABF","載板","AI伺服器"],"risk":"HIGH","defensive":False},
    "2383":{"primary":"PCB/載板","secondary":"CCL","tags":["PCB","CCL","高階材料","AI伺服器"],"risk":"HIGH","defensive":False},
    "8046":{"primary":"PCB/載板","secondary":"ABF載板","tags":["PCB","ABF","載板"],"risk":"HIGH","defensive":False},
    "3481":{"primary":"PCB/載板","secondary":"LCD面板","tags":["面板","LCD"],"risk":"HIGH","defensive":False},

    # 金融/防守
    "2881":{"primary":"金融","secondary":"壽險金控","tags":["金融","壽險","防守","高股息"],"risk":"LOW","defensive":True},
    "2882":{"primary":"金融","secondary":"壽險金控","tags":["金融","壽險","防守","高股息"],"risk":"LOW","defensive":True},
    "2891":{"primary":"金融","secondary":"銀行金控","tags":["金融","銀行","防守","高股息"],"risk":"LOW","defensive":True},
    "2886":{"primary":"金融","secondary":"銀行金控","tags":["金融","銀行","防守"],"risk":"LOW","defensive":True},
    "2884":{"primary":"金融","secondary":"銀行金控","tags":["金融","銀行","防守"],"risk":"LOW","defensive":True},
    "2892":{"primary":"金融","secondary":"銀行金控","tags":["金融","銀行","防守"],"risk":"LOW","defensive":True},
    "2885":{"primary":"金融","secondary":"證券金控","tags":["金融","證券","防守"],"risk":"LOW","defensive":True},
    "2890":{"primary":"金融","secondary":"銀行金控","tags":["金融","銀行","防守"],"risk":"LOW","defensive":True},
    "2887":{"primary":"金融","secondary":"銀行金控","tags":["金融","銀行","防守"],"risk":"LOW","defensive":True},
    "5880":{"primary":"金融","secondary":"銀行","tags":["金融","銀行","防守","高股息"],"risk":"LOW","defensive":True},
    "5871":{"primary":"金融","secondary":"租賃","tags":["金融","租賃","KY"],"risk":"MEDIUM","defensive":False},

    # 傳產/塑化
    "1301":{"primary":"傳產/塑化","secondary":"石化","tags":["傳產","塑化","原物料","景氣循環"],"risk":"LOW","defensive":True},
    "1303":{"primary":"傳產/塑化","secondary":"石化","tags":["傳產","塑化","原物料"],"risk":"LOW","defensive":True},
    "6505":{"primary":"傳產/塑化","secondary":"煉油","tags":["傳產","塑化","煉油"],"risk":"LOW","defensive":True},
    "2002":{"primary":"傳產/塑化","secondary":"鋼鐵","tags":["傳產","鋼鐵","原物料"],"risk":"MEDIUM","defensive":False},
    "1101":{"primary":"傳產/塑化","secondary":"水泥","tags":["傳產","水泥","原物料"],"risk":"LOW","defensive":True},
    "1102":{"primary":"傳產/塑化","secondary":"水泥","tags":["傳產","水泥","原物料"],"risk":"LOW","defensive":True},

    # 航運/航空
    "2603":{"primary":"航運","secondary":"貨櫃航運","tags":["航運","貨櫃","運價","景氣循環"],"risk":"HIGH","defensive":False},
    "2609":{"primary":"航運","secondary":"貨櫃航運","tags":["航運","貨櫃","運價"],"risk":"HIGH","defensive":False},
    "2615":{"primary":"航運","secondary":"貨櫃航運","tags":["航運","貨櫃","運價"],"risk":"HIGH","defensive":False},
    "2618":{"primary":"航運","secondary":"航空","tags":["航空","觀光","匯率"],"risk":"HIGH","defensive":False},
    "2610":{"primary":"航運","secondary":"航空","tags":["航空","觀光"],"risk":"HIGH","defensive":False},

    # ETF
    "0050":{"primary":"ETF","secondary":"大盤型ETF","tags":["ETF","核心","大盤"],"risk":"LOW","defensive":True,"is_etf":True},
    "006208":{"primary":"ETF","secondary":"大盤型ETF","tags":["ETF","核心","大盤"],"risk":"LOW","defensive":True,"is_etf":True},
    "00981A":{"primary":"ETF","secondary":"收益型ETF","tags":["ETF","收益","高股息"],"risk":"LOW","defensive":True,"is_etf":True},
    "00878":{"primary":"ETF","secondary":"收益型ETF","tags":["ETF","收益","高股息"],"risk":"LOW","defensive":True,"is_etf":True},
    "00919":{"primary":"ETF","secondary":"收益型ETF","tags":["ETF","收益","高股息"],"risk":"LOW","defensive":True,"is_etf":True},
    "00929":{"primary":"ETF","secondary":"收益型ETF","tags":["ETF","收益","高股息"],"risk":"LOW","defensive":True,"is_etf":True},

    # 通訊/網通
    "2412":{"primary":"通訊","secondary":"電信","tags":["電信","防守","高股息"],"risk":"LOW","defensive":True},
    "4904":{"primary":"通訊","secondary":"電信","tags":["電信","防守"],"risk":"LOW","defensive":True},
    "3045":{"primary":"通訊","secondary":"電信","tags":["電信","防守"],"risk":"LOW","defensive":True},

    # 其他大型股
    "1216":{"primary":"民生消費","secondary":"食品","tags":["民生","食品","防守"],"risk":"LOW","defensive":True},
    "2912":{"primary":"民生消費","secondary":"零售","tags":["民生","零售","防守"],"risk":"LOW","defensive":True},
    "2207":{"primary":"民生消費","secondary":"汽車","tags":["汽車","代理","電動車"],"risk":"MEDIUM","defensive":False},
    "2395":{"primary":"工業電腦","secondary":"工業電腦","tags":["工業","IoT","AI"],"risk":"MEDIUM","defensive":False},
}

CORE_ETF_CODES = {"0050", "006208"}
WATCHLIST_CODES = {
    "2330","2454","2317","2382","3711","2308","2382","2356",
    "3231","6669","3037","3189","2383","2881","2882","0050"
}


def build_classification(target_date: date = None) -> int:
    if target_date is None:
        target_date = date.today()

    db = SessionLocal()
    try:
        # 取所有股票
        codes = db.execute(text("""
            SELECT DISTINCT code FROM ohlcv_daily
            WHERE trade_date=(SELECT MAX(trade_date) FROM ohlcv_daily)
        """)).fetchall()

        # 取主題熱度
        theme_heat = {}
        try:
            rows = db.execute(text("""
                SELECT theme, score FROM theme_trend_daily
                WHERE context_date=(SELECT MAX(context_date) FROM theme_trend_daily)
            """)).fetchall()
            for r in rows:
                theme_heat[r[0]] = float(r[1] or 50)
        except Exception:
            pass

        inserted = 0
        for (code,) in codes:
            info = SECTOR_MAP.get(code, {})
            primary = info.get("primary", "其他")
            secondary = info.get("secondary", "")
            tags = info.get("tags", [])
            risk_type = info.get("risk", "NORMAL")
            is_defensive = 1 if info.get("defensive", False) else 0
            is_etf = 1 if info.get("is_etf", False) else 0
            is_core_etf = 1 if code in CORE_ETF_CODES else 0
            is_watchlist = 1 if code in WATCHLIST_CODES else 0

            # 計算 theme_heat_score
            heat = 50.0
            for tag in tags:
                for theme_key, score in theme_heat.items():
                    if tag in theme_key or theme_key in tag:
                        heat = max(heat, score)
                        break

            confidence = 95 if code in SECTOR_MAP else 60
            reason = f"已知分類" if code in SECTOR_MAP else "自動分類（無詳細資料）"

            name_row = db.execute(text(
                "SELECT name FROM stock_meta WHERE code=:c LIMIT 1"
            ), {"c": code}).fetchone()
            name = name_row[0] if name_row else code

            db.execute(text("""
                INSERT INTO market_sector_classification
                    (code, name, primary_category, secondary_category, theme_tags_json,
                     risk_type, is_core_watchlist, is_core_etf, is_defensive,
                     theme_heat_score, classification_confidence, classification_reason,
                     updated_at)
                VALUES (:code,:name,:pc,:sc,:tags,:rt,:cw,:ce,:def,:heat,:conf,:reason,:now)
                ON CONFLICT(code) DO UPDATE SET
                    primary_category=excluded.primary_category,
                    secondary_category=excluded.secondary_category,
                    theme_tags_json=excluded.theme_tags_json,
                    risk_type=excluded.risk_type,
                    is_core_watchlist=excluded.is_core_watchlist,
                    is_core_etf=excluded.is_core_etf,
                    is_defensive=excluded.is_defensive,
                    theme_heat_score=excluded.theme_heat_score,
                    classification_confidence=excluded.classification_confidence,
                    classification_reason=excluded.classification_reason,
                    updated_at=excluded.updated_at
            """), {
                "code": code, "name": name,
                "pc": primary, "sc": secondary,
                "tags": json.dumps(tags, ensure_ascii=False),
                "rt": risk_type, "cw": is_watchlist,
                "ce": is_core_etf, "def": is_defensive,
                "heat": round(heat, 1), "conf": confidence,
                "reason": reason,
                "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            inserted += 1

        db.commit()
        logger.success(f"[SECTOR] 分類完成 {inserted} 檔")
        return inserted
    except Exception as e:
        logger.error(f"[SECTOR] 分類失敗: {e}")
        db.rollback()
        return 0
    finally:
        db.close()


def get_classification(code: str = None, primary_category: str = None,
                       min_heat: float = None, limit: int = 200) -> list[dict]:
    db = SessionLocal()
    try:
        q = "SELECT * FROM market_sector_classification WHERE 1=1"
        params = {}
        if code: q += " AND code=:code"; params["code"] = code
        if primary_category: q += " AND primary_category=:pc"; params["pc"] = primary_category
        if min_heat: q += " AND theme_heat_score>=:mh"; params["mh"] = min_heat
        q += " ORDER BY theme_heat_score DESC, classification_confidence DESC LIMIT :limit"
        params["limit"] = limit
        rows = db.execute(text(q), params).fetchall()
        cols = ["id","code","name","primary_category","secondary_category","theme_tags_json",
                "industry_group","risk_type","is_core_watchlist","is_core_etf","is_defensive",
                "theme_heat_score","classification_confidence","classification_reason",
                "updated_at","created_at"]
        result = []
        for r in rows:
            d = dict(zip(cols, r))
            try: d["theme_tags_json"] = json.loads(d["theme_tags_json"] or "[]")
            except: d["theme_tags_json"] = []
            result.append(d)
        return result
    finally:
        db.close()


def get_theme_exposure(account_id: int = None) -> dict:
    """取得當前持倉的主題曝險"""
    db = SessionLocal()
    try:
        holdings = db.execute(text("""
            SELECT t.code, t.lots * t.price as value
            FROM trade_logs t
            WHERE (:aid IS NULL OR t.account_id=:aid)
              AND t.direction='buy'
            GROUP BY t.code
            HAVING SUM(CASE WHEN t.direction='buy' THEN t.lots ELSE -t.lots END) > 0
        """), {"aid": account_id}).fetchall()

        exposure = {}
        total = sum(float(r[1] or 0) for r in holdings)

        for code, value in holdings:
            cls_row = db.execute(text(
                "SELECT primary_category FROM market_sector_classification WHERE code=:c"
            ), {"c": code}).fetchone()
            cat = cls_row[0] if cls_row else "其他"
            exposure[cat] = exposure.get(cat, 0) + float(value or 0)

        return {
            "total_value": total,
            "by_category": {k: {"value": round(v), "ratio": round(v/total*100, 1) if total else 0}
                            for k, v in sorted(exposure.items(), key=lambda x: x[1], reverse=True)}
        }
    finally:
        db.close()
