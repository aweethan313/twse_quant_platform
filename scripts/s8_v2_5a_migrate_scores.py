"""
scripts/s8_v2_5a_migrate_scores.py
S8 v2-5A：安全新增 daily_scores 新欄位（可重複執行）
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from pathlib import Path
from config.settings import settings

DB_PATH = str(settings.DB_PATH)


NEW_COLUMNS = [
    ("volume_score",    "REAL"),
    ("candidate_score", "REAL"),
    ("entry_score",     "REAL"),
    ("risk_score",      "REAL"),
    ("risk_flags",      "TEXT"),   # JSON array
    ("final_score",     "REAL"),
    ("final_action",    "TEXT"),
    ("core_score",      "REAL"),
    ("stock_class",     "TEXT"),
]


def migrate():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 取得現有欄位
    cur.execute("PRAGMA table_info(daily_scores)")
    existing = {row[1] for row in cur.fetchall()}

    added = []
    for col_name, col_type in NEW_COLUMNS:
        if col_name not in existing:
            cur.execute(f"ALTER TABLE daily_scores ADD COLUMN {col_name} {col_type}")
            added.append(col_name)
            print(f"  ✓ 新增欄位: {col_name} {col_type}")
        else:
            print(f"  - 已存在: {col_name}")

    conn.commit()
    conn.close()

    if added:
        print(f"\n✓ migration 完成，新增 {len(added)} 個欄位")
    else:
        print("\n✓ 所有欄位已存在，無需 migration")


if __name__ == "__main__":
    print(f"DB: {DB_PATH}")
    migrate()
