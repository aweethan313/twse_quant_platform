# V9.1-P0B 0050 Benchmark Patch

這個 patch 只修 P0 benchmark 問題：

- 不加入分鐘資料表。
- 不要求 TPEx。
- 刪除 0050 在 2025-06-11 ~ 2025-06-17 分割停止交易期間誤寫入的日 K 與下游 0050 分數。
- 重建 0050 benchmark，將 2025-06-18 前價格除以 4，統一為分割後價格基準。

## 使用方式

在專案根目錄執行：

```bash
unzip -o twse_quant_platform_v9_1_p0b_relative_patch.zip
bash apply_v9_1_p0b_patch.sh
```

跑完看報告：

```bash
cat data/reports/v9_1_p0b_0050_benchmark_report.md
```
