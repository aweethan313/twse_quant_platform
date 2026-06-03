V9.1 P0 relative patch 使用方式

這包是給「你已經有舊專案資料夾與 data/db/quant.db」的版本，不含完整 DB。

正確用法：
1. 把這個 zip 放到你的專案根目錄：
   /Users/yangyichen/Downloads/twse_quant_platform 第一版 3

2. 在專案根目錄執行：
   unzip -o twse_quant_platform_v9_1_p0_relative_patch.zip
   bash apply_v9_1_p0_patch.sh

它會：
- 備份 data/db/quant.db
- 套用 P0 修正檔
- 編譯檢查 Python 檔
- 執行 scripts/v9_1_p0_fix.py --apply
- 產生 data/reports/v9_1_p0_fix_report.md

注意：
- 這版不加入分鐘資料表。
- 這版不要求 TPEx，上限維持上市股票 + ETF。
