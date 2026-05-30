"""ml_eval/models.py
模型工廠。優先用 LightGBM（你的 requirements.txt 有裝），
這個沙盒沒裝就自動 fallback 到 sklearn 的 GradientBoosting / RandomForest。
所有模型都「預測前瞻報酬」，之後一律用預測值排序，所以評估指標與模型無關。
"""
from __future__ import annotations


def available_backends() -> list:
    backends = []
    try:
        import lightgbm  # noqa: F401
        backends.append("lgbm")
    except ImportError:
        pass
    try:
        import sklearn  # noqa: F401
        backends += ["gbr", "rf"]
    except ImportError:
        pass
    return backends


def make_model(name: str = "auto"):
    """回傳一個有 .fit(X,y) / .predict(X) 的回歸器。"""
    backends = available_backends()
    if not backends:
        raise RuntimeError("沒有可用的 ML 後端，請 pip install lightgbm 或 scikit-learn")

    if name == "auto":
        name = backends[0]   # 有 lgbm 就用 lgbm
    if name not in backends:
        raise RuntimeError(f"後端 '{name}' 不可用。可用：{backends}")

    if name == "lgbm":
        from lightgbm import LGBMRegressor
        return LGBMRegressor(
            n_estimators=400, learning_rate=0.03, num_leaves=31,
            max_depth=6, subsample=0.8, colsample_bytree=0.8,
            min_child_samples=50, random_state=42, n_jobs=-1, verbosity=-1,
        ), "lgbm"

    if name == "gbr":
        from sklearn.ensemble import GradientBoostingRegressor
        return GradientBoostingRegressor(
            n_estimators=300, learning_rate=0.03, max_depth=4,
            subsample=0.8, random_state=42,
        ), "gbr"

    from sklearn.ensemble import RandomForestRegressor
    return RandomForestRegressor(
        n_estimators=300, max_depth=8, min_samples_leaf=50,
        random_state=42, n_jobs=-1,
    ), "rf"
