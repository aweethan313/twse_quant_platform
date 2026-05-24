"""
backend/v4/data_quality.py
V4-1：資料品質中控台
"""
from __future__ import annotations
import json
from datetime import date, datetime
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal


def _write_check(db, check_date, check_type, status, severity, message,
                 suggestion="", table="", issue_count=0, total_count=0,
                 health_score=100.0, affected_codes=None):
    db.execute(text("""
        INSERT INTO data_quality_checks
        (check_date, check_time, check_type, table_name, status, severity,
         affected_codes_json, issue_count, total_count, health_score,
         message, suggestion)
        VALUES (:cd,:ct,:ctype,:tbl,:status,:sev,:ac,:ic,:tc,:hs,:msg,:sug)
    """), {
        "cd": str(check_date),
        "ct": datetime.now().strftime("%H:%M:%S"),
        "ctype": check_type, "tbl": table,
        "status": status, "sev": severity,
        "ac": json.dumps(affected_codes or [], ensure_ascii=False),
        "ic": issue_count, "tc": total_count,
        "hs": health_score, "msg": message, "sug": suggestion,
    })


def run_data_quality_checks(check_date: date = None) -> dict:
    if check_date is None:
        check_date = date.today()

    db = SessionLocal()
    results = []

    try:
        now = str(check_date)

        # 1. OHLCV 日K 覆蓋率
        total = db.execute(text(
            "SELECT COUNT(DISTINCT code) FROM ohlcv_daily WHERE trade_date=:d"
        ), {"d": now}).scalar() or 0
        prev_total = db.execute(text(
            "SELECT COUNT(DISTINCT code) FROM ohlcv_daily WHERE trade_date<:d ORDER BY trade_date DESC LIMIT 1"
        ), {"d": now}).scalar() or total
        if total == 0:
            status, sev, msg = "FAIL", "CRITICAL", f"ohlcv_daily 今日無資料"
        elif total < prev_total * 0.8:
            status, sev, msg = "WARN", "HIGH", f"ohlcv_daily 今日{total}檔，低於前日{prev_total}檔"
        else:
            status, sev, msg = "PASS", "LOW", f"ohlcv_daily 今日{total}檔，覆蓋正常"
        hs = min(100, total / max(prev_total, 1) * 100)
        _write_check(db, check_date, "DAILY_OHLCV_COVERAGE", status, sev, msg,
                     table="ohlcv_daily", total_count=total, health_score=hs)
        results.append({"type": "DAILY_OHLCV_COVERAGE", "status": status, "msg": msg})

        # 2. 分鐘K
        try:
            min_count = db.execute(text(
                "SELECT COUNT(*) FROM ohlcv_1min WHERE trade_date=:d"
            ), {"d": now}).scalar() or 0
            if min_count == 0:
                _write_check(db, check_date, "MINUTE_DATA_COVERAGE", "WARN", "MEDIUM",
                             "ohlcv_1min 無資料，盤中策略跳過",
                             "可忽略，盤中功能需另行收集分鐘資料", "ohlcv_1min", health_score=0)
                results.append({"type": "MINUTE_DATA_COVERAGE", "status": "WARN",
                                "msg": "ohlcv_1min 無資料（SKIPPED）"})
            else:
                _write_check(db, check_date, "MINUTE_DATA_COVERAGE", "PASS", "LOW",
                             f"ohlcv_1min {min_count} 筆", table="ohlcv_1min",
                             total_count=min_count, health_score=100)
                results.append({"type": "MINUTE_DATA_COVERAGE", "status": "PASS",
                                "msg": f"{min_count} 筆"})
        except Exception:
            _write_check(db, check_date, "MINUTE_DATA_COVERAGE", "SKIPPED", "LOW",
                         "ohlcv_1min 資料表不存在或無法查詢")
            results.append({"type": "MINUTE_DATA_COVERAGE", "status": "SKIPPED"})

        # 3. 異常跳價
        jumps = db.execute(text("""
            SELECT code, close, open,
                   ABS(close - open) / open * 100 as jump_pct
            FROM ohlcv_daily WHERE trade_date=:d
              AND open > 0 AND ABS(close - open) / open > 0.20
        """), {"d": now}).fetchall()
        if len(jumps) > 5:
            codes = [r[0] for r in jumps[:10]]
            _write_check(db, check_date, "PRICE_JUMP_CHECK", "WARN", "MEDIUM",
                         f"發現{len(jumps)}檔異常跳價(>20%)",
                         "請確認是否為除權息或資料錯誤",
                         "ohlcv_daily", len(jumps), total, 90, codes)
        else:
            _write_check(db, check_date, "PRICE_JUMP_CHECK", "PASS", "LOW",
                         f"跳價異常{len(jumps)}檔，正常", table="ohlcv_daily", health_score=100)
        results.append({"type": "PRICE_JUMP_CHECK", "status": "WARN" if len(jumps) > 5 else "PASS"})

        # 4. 零成交量
        zero_vol = db.execute(text("""
            SELECT COUNT(*) FROM ohlcv_daily
            WHERE trade_date=:d AND (volume IS NULL OR volume=0)
        """), {"d": now}).scalar() or 0
        hs = max(0, 100 - zero_vol / max(total, 1) * 100)
        _write_check(db, check_date, "ZERO_VOLUME_CHECK",
                     "WARN" if zero_vol > total * 0.1 else "PASS",
                     "MEDIUM" if zero_vol > total * 0.1 else "LOW",
                     f"零成交量 {zero_vol} 檔",
                     table="ohlcv_daily", issue_count=zero_vol, total_count=total, health_score=hs)
        results.append({"type": "ZERO_VOLUME_CHECK",
                        "status": "WARN" if zero_vol > total * 0.1 else "PASS"})

        # 5. daily_scores S8 欄位
        score_miss = db.execute(text("""
            SELECT COUNT(*) FROM daily_scores
            WHERE score_date=:d AND candidate_score IS NULL
        """), {"d": now}).scalar() or 0
        score_total = db.execute(text(
            "SELECT COUNT(*) FROM daily_scores WHERE score_date=:d"
        ), {"d": now}).scalar() or 0
        hs = max(0, 100 - score_miss / max(score_total, 1) * 100)
        _write_check(db, check_date, "FACTOR_AVAILABLE_AT_CHECK",
                     "WARN" if score_miss > score_total * 0.3 else "PASS",
                     "HIGH" if score_miss > score_total * 0.3 else "LOW",
                     f"daily_scores 缺 candidate_score: {score_miss}/{score_total}",
                     "執行 compute_scores 補算", "daily_scores",
                     score_miss, score_total, hs)
        results.append({"type": "FACTOR_AVAILABLE_AT_CHECK",
                        "status": "WARN" if score_miss > score_total * 0.3 else "PASS"})

        # 6. Realistic fills 時間順序
        fill_violations = 0
        try:
            fill_violations = db.execute(text("""
                SELECT COUNT(*) FROM realistic_trade_fills
                WHERE fill_time IS NOT NULL AND signal_time IS NOT NULL
                  AND fill_time <= signal_time
                  AND execution_status='FILLED'
            """)).scalar() or 0
        except Exception:
            pass
        _write_check(db, check_date, "TRADE_FILL_TIME_CHECK",
                     "FAIL" if fill_violations > 0 else "PASS",
                     "CRITICAL" if fill_violations > 0 else "LOW",
                     f"fill_time早於signal_time違規: {fill_violations}筆",
                     table="realistic_trade_fills",
                     issue_count=fill_violations, health_score=100 if fill_violations == 0 else 0)
        results.append({"type": "TRADE_FILL_TIME_CHECK",
                        "status": "FAIL" if fill_violations > 0 else "PASS"})

        # 7. 0050 短線賣出
        etf_sells = 0
        try:
            etf_sells = db.execute(text("""
                SELECT COUNT(*) FROM realistic_trade_fills
                WHERE code='0050' AND action='sell'
                  AND execution_status='FILLED'
            """)).scalar() or 0
        except Exception:
            pass
        _write_check(db, check_date, "BACKTEST_LOOKAHEAD_CHECK",
                     "FAIL" if etf_sells > 0 else "PASS",
                     "CRITICAL" if etf_sells > 0 else "LOW",
                     f"0050 被短線強制賣出: {etf_sells}次",
                     table="realistic_trade_fills",
                     issue_count=etf_sells, health_score=100 if etf_sells == 0 else 0)
        results.append({"type": "0050_PROTECTION",
                        "status": "FAIL" if etf_sells > 0 else "PASS"})

        db.commit()

        # 計算總體健康分
        pass_count = sum(1 for r in results if r["status"] == "PASS")
        fail_count = sum(1 for r in results if r["status"] == "FAIL")
        warn_count = sum(1 for r in results if r["status"] == "WARN")
        overall = max(0, 100 - fail_count * 20 - warn_count * 5)

        logger.success(f"[DQ] {check_date} 資料品質檢查完成 "
                       f"PASS={pass_count} WARN={warn_count} FAIL={fail_count} 健康分={overall}")

        return {
            "check_date": str(check_date),
            "overall_health": overall,
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
            "checks": results,
        }

    except Exception as e:
        logger.error(f"[DQ] 資料品質檢查失敗: {e}")
        db.rollback()
        return {"check_date": str(check_date), "overall_health": 0, "error": str(e)}
    finally:
        db.close()


def get_quality_report(check_date: str = None, limit: int = 50) -> list[dict]:
    db = SessionLocal()
    try:
        q = "SELECT * FROM data_quality_checks WHERE 1=1"
        params = {}
        if check_date:
            q += " AND check_date=:cd"
            params["cd"] = check_date
        q += " ORDER BY created_at DESC LIMIT :limit"
        params["limit"] = limit
        rows = db.execute(text(q), params).fetchall()
        cols = ["id","check_date","check_time","check_type","data_source","table_name",
                "status","severity","affected_codes_json","issue_count","total_count",
                "health_score","message","suggestion","created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()
