"""Replay the frozen R1 grid over the extended 1-second historical window."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jevmesh.data import write_json
from jevmesh.fmz_config import FMZConfig
from jevmesh.fmz_engine import backtest_fmz
from jevmesh.fmz_data import EMA_WINDOWS

BASE_STUDY = ROOT / "data/long_backtest_202406_202609"
DEFAULT_STUDY = ROOT / "data/long_backtest_202406_202609_blockbeats"
DEFAULT_REPORT = ROOT / "reports/fmz_v2/historical_202406_202609_blockbeats"
SIM_START = "2024-06-01T00:00:00Z"
SIM_END = "2026-09-22T00:00:00Z"


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8*1024*1024), b""):
            value.update(block)
    return value.hexdigest()


def millis(value):
    return int(pd.Timestamp(value).timestamp()*1000)


def second_archive(day, processed, market_study_dir=BASE_STUDY):
    name = f"{day}.npz"
    market_study_dir = Path(market_study_dir)
    for folder in (processed / "seconds", market_study_dir / "processed/seconds",
                   ROOT / "data/processed/seconds"):
        path = folder / name
        if path.is_file():
            return path
    raise FileNotFoundError(f"Missing 1-second data: {day}")


def prepare_features(market):
    close = market.close
    parts = [close.ewm(span=window, adjust=False, min_periods=window).mean().shift(1).to_numpy()
             for window in EMA_WINDOWS]
    parts.append(close.shift(1).to_numpy())
    parts.append(np.log(close).diff().rolling(60).std().shift(1).to_numpy())
    return np.column_stack(parts).astype(np.float64, copy=False)


def prepare_arrays(study_dir=DEFAULT_STUDY, start=SIM_START, end=SIM_END,
                   market_study_dir=BASE_STUDY):
    study = Path(study_dir).resolve()
    market_study_dir = Path(market_study_dir).resolve()
    processed = study / "processed"
    arrays_dir = study / "engine_arrays"
    market_processed = market_study_dir / "processed"
    market_path = market_processed / "market_1m.pkl"
    funding_path = market_processed / "funding.pkl"
    prediction_path = processed / "event_predictions_walkforward.jsonl"
    coverage_path = market_study_dir / "market_coverage.json"
    model_audit_path = study / "walkforward_model_audit.json"
    embedding_audit_path = study / "nanojev_embedding_audit.json"
    news_audit_path = study / "news_coverage.json"
    for path in (market_path, funding_path, prediction_path, coverage_path, model_audit_path,
                 embedding_audit_path, news_audit_path):
        if not path.exists():
            raise FileNotFoundError(f"Required long-backtest input missing: {path}")
    first_ms, end_ms = millis(start), millis(end)
    count = (end_ms-first_ms)//1000
    if count <= 0 or (end_ms-first_ms) % 1000:
        raise ValueError("Simulation interval must align to whole seconds")
    if first_ms % 86_400_000 or end_ms % 86_400_000:
        raise ValueError("Array interval must start and end at UTC midnight")
    dates = pd.date_range(pd.to_datetime(first_ms, unit="ms", utc=True),
                          pd.to_datetime(end_ms, unit="ms", utc=True),
                          inclusive="left", freq="D")
    day_files = [second_archive(day.strftime("%Y-%m-%d"), processed, market_study_dir) for day in dates]
    sec_audit = []
    for day, path in zip(dates, day_files):
        stamp = day.strftime("%Y-%m-%d")
        sidecar = path.with_suffix(".json")
        receipt = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else None
        if receipt and receipt.get("processed_sha256"):
            expected_hash = receipt["processed_sha256"]
        else:
            main_audit = ROOT / "data/audit/seconds" / f"{stamp}.json"
            if not main_audit.exists():
                raise FileNotFoundError(f"Missing hash receipt for second bars {stamp}")
            expected_hash = json.loads(main_audit.read_text(encoding="utf-8")).get("processed_sha256")
        actual_hash = digest(path)
        if not expected_hash or actual_hash != expected_hash:
            raise ValueError(f"Processed second-bar hash mismatch for {stamp}")
        sec_audit.append({"date": stamp, "sha256": actual_hash})
    market_audit = json.loads(coverage_path.read_text(encoding="utf-8"))
    model_audit = json.loads(model_audit_path.read_text(encoding="utf-8"))
    embedding_audit = json.loads(embedding_audit_path.read_text(encoding="utf-8"))
    news_audit = json.loads(news_audit_path.read_text(encoding="utf-8"))
    start_iso = pd.to_datetime(first_ms, unit="ms", utc=True).isoformat().replace("+00:00", "Z")
    end_iso = pd.to_datetime(end_ms, unit="ms", utc=True).isoformat().replace("+00:00", "Z")
    signature_data = {"start": start_iso, "end_exclusive": end_iso,
        "market_sha256": market_audit["market_sha256"],
        "funding_sha256": market_audit["funding_sha256"],
        "predictions_sha256": digest(prediction_path), "news_source": news_audit.get("source"),
        "seconds": sec_audit}
    signature = hashlib.sha256(json.dumps(signature_data, separators=(",", ":")).encode()).hexdigest()
    manifest_path = arrays_dir / "dataset_manifest.json"
    names = {"times": ("times.npy", np.int64, (count,)),
             "bars": ("bars.npy", np.float64, (count, 8)),
             "funding": ("funding.npy", np.float64, (count,)),
             "news": ("news.npy", np.float32, (count, 2)),
             "minute_index": ("minute_index.npy", np.int32, (count,))}
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_features = (len(pd.read_pickle(market_path)), len(EMA_WINDOWS)+2)
        cache_paths = [arrays_dir/name[0] for name in names.values()] + [arrays_dir/"features.npy"]
        if previous.get("dataset_signature") == signature and all(path.exists() for path in cache_paths):
            arrays = {key: np.load(arrays_dir/values[0], mmap_mode="r") for key, values in names.items()}
            arrays["features"] = np.load(arrays_dir/"features.npy", mmap_mode="r")
            for key, (filename, dtype, shape) in names.items():
                if arrays[key].shape != shape or arrays[key].dtype != np.dtype(dtype):
                    raise ValueError(f"Cached {filename} has an invalid shape or dtype")
            if arrays["features"].shape != expected_features or arrays["features"].dtype != np.dtype(np.float64):
                raise ValueError("Cached minute features have an invalid shape or dtype")
            previous.update({
                "model_method": model_audit.get("method"),
                "walk_forward_metrics": model_audit.get("walk_forward_metrics"),
                "past_only_prior_baseline": model_audit.get("past_only_prior_baseline"),
                "accuracy_lift_vs_prior_baseline_percentage_points": model_audit.get("accuracy_lift_vs_prior_baseline_percentage_points"),
                "log_loss_delta_vs_prior_baseline": model_audit.get("log_loss_delta_vs_prior_baseline"),
                "brier_delta_vs_prior_baseline": model_audit.get("brier_delta_vs_prior_baseline"),
                "predicted_class_counts": model_audit.get("predicted_class_counts"),
                "confusion_matrix_rows_true_columns_predicted": model_audit.get("confusion_matrix_rows_true_columns_predicted"),
                "head_device": model_audit.get("head_device"),
                "mark_basis_reconstructed_timestamps": market_audit.get("mark_basis_reconstructed_timestamps", []),
                "news_source": news_audit.get("source"),
                "news_availability_basis": news_audit.get("availability_basis"),
                "news_content_basis": news_audit.get("content_basis"),
                "inference_device": embedding_audit.get("device"),
                "inference_precision": embedding_audit.get("precision"),
                "inference_batch_size": embedding_audit.get("batch_size"),
                "inference_threads": embedding_audit.get("threads"),
                "inference_mean_batch_forward_seconds": embedding_audit.get("mean_batch_forward_seconds"),
                "inference_mean_forward_per_event_seconds": (embedding_audit.get("mean_batch_forward_seconds")/embedding_audit.get("batch_size")
                    if embedding_audit.get("mean_batch_forward_seconds") is not None and embedding_audit.get("batch_size") else None),
            })
            write_json(manifest_path, previous)
            arrays["resolution"] = "1s"
            print(json.dumps({"array_cache": "reused", "bars": count,
                              "dataset_signature": signature}), flush=True)
            return arrays, previous
    arrays_dir.mkdir(parents=True, exist_ok=True)
    market = pd.read_pickle(market_path)
    market_times = market.index.to_numpy(dtype=np.int64)
    market_close = market.close.to_numpy(dtype=np.float64)
    market_mark_close = market.mark_close.to_numpy(dtype=np.float64)
    minute_features = prepare_features(market)
    np.save(arrays_dir/"features.npy", minute_features)
    arrays = {key: np.lib.format.open_memmap(arrays_dir/filename, mode="w+", dtype=dtype, shape=shape)
              for key, (filename, dtype, shape) in names.items()}
    arrays["funding"][:] = 0.
    arrays["news"][:] = 0.
    for day_index, (day, path) in enumerate(zip(dates, day_files)):
        day_start = int(day.timestamp()*1000)
        offset = day_index*86_400
        finish = offset+86_400
        expected_times = day_start+np.arange(86_400,dtype=np.int64)*1000
        with np.load(path) as archive:
            timestamps = archive["timestamp"]
            ohlcv = archive["ohlcv"]
        if len(timestamps) != 86_400 or not np.array_equal(timestamps, expected_times) or ohlcv.shape != (86_400,5):
            raise ValueError(f"Invalid one-second archive for {day.strftime('%Y-%m-%d')}")
        minute_start = int(np.searchsorted(market_times, day_start))
        minute_expected = day_start+np.arange(1440,dtype=np.int64)*60_000
        if not np.array_equal(market_times[minute_start:minute_start+1440], minute_expected):
            raise ValueError(f"Minute data incomplete on {day.strftime('%Y-%m-%d')}")
        previous = np.maximum(0, minute_start+np.arange(1440,dtype=np.int64)-1)
        basis = market_mark_close[previous]/market_close[previous]
        if minute_start == 0:
            basis[0] = market.mark_open.iloc[0]/market.open.iloc[0]
        basis_seconds = np.repeat(basis,60)
        arrays["times"][offset:finish] = expected_times
        arrays["minute_index"][offset:finish] = np.repeat(
            np.arange(minute_start,minute_start+1440,dtype=np.int32),60)
        arrays["bars"][offset:finish,:4] = ohlcv[:,:4]
        arrays["bars"][offset:finish,4:] = ohlcv[:,:4]*basis_seconds[:,None]
        if (day_index+1)%30==0 or day_index+1==len(dates):
            for value in arrays.values():
                value.flush()
            print(json.dumps({"dataset_days_built": day_index+1, "days": len(dates)}), flush=True)
    rates = pd.read_pickle(funding_path)
    for row in rates.itertuples(index=False):
        settlement = int(row.timestamp)//60_000*60_000
        if first_ms <= settlement < end_ms:
            index = (settlement-first_ms)//1000
            if arrays["times"][index] == settlement:
                arrays["funding"][index] = float(row.rate)
    predictions = pd.read_json(prediction_path, lines=True)
    for row in predictions.itertuples(index=False):
        timestamp = int(row.available_ms)+5000
        if timestamp < first_ms or timestamp >= end_ms:
            continue
        index = int(np.searchsorted(arrays["times"], timestamp, side="left"))
        if index >= count:
            continue
        score = float(row.p_breakout)
        if score > arrays["news"][index,0]:
            arrays["news"][index,0] = score
            arrays["news"][index,1] = (float(row.p_up)-float(row.p_down))/max(score,1e-9)
    for value in arrays.values():
        value.flush()
    arrays["features"] = np.load(arrays_dir/"features.npy", mmap_mode="r")
    arrays["resolution"] = "1s"
    manifest = {"dataset_signature": signature, **signature_data,
        "bars": count, "calendar_days": len(dates), "seconds_start": pd.to_datetime(first_ms,unit="ms",utc=True).isoformat(),
        "seconds_end_exclusive": pd.to_datetime(end_ms,unit="ms",utc=True).isoformat(),
        "news_events_in_window": int(len(predictions)), "news_nonzero_seconds": int(np.count_nonzero(arrays["news"][:,0])),
        "market_bars": market_audit.get("bars"), "market_start": market_audit.get("start"),
        "market_end_exclusive": market_audit.get("end_exclusive"),
        "trade_minute_gaps": market_audit.get("trade_minute_gaps"),
        "funding_records": market_audit.get("funding_records"),
        "funding_start": market_audit.get("funding_start"), "funding_last": market_audit.get("funding_last"),
        "mark_basis_reconstructed_rows": market_audit.get("mark_basis_reconstructed_rows",0),
        "mark_basis_reconstructed_timestamps": market_audit.get("mark_basis_reconstructed_timestamps", []),
        "news_source": news_audit.get("source"),
        "news_availability_basis": news_audit.get("availability_basis"),
        "news_content_basis": news_audit.get("content_basis"),
        "model_method": model_audit.get("method"),
        "walk_forward_metrics": model_audit.get("walk_forward_metrics"),
        "past_only_prior_baseline": model_audit.get("past_only_prior_baseline"),
        "accuracy_lift_vs_prior_baseline_percentage_points": model_audit.get("accuracy_lift_vs_prior_baseline_percentage_points"),
        "log_loss_delta_vs_prior_baseline": model_audit.get("log_loss_delta_vs_prior_baseline"),
        "brier_delta_vs_prior_baseline": model_audit.get("brier_delta_vs_prior_baseline"),
        "predicted_class_counts": model_audit.get("predicted_class_counts"),
        "confusion_matrix_rows_true_columns_predicted": model_audit.get("confusion_matrix_rows_true_columns_predicted"),
        "head_device": model_audit.get("head_device"),
        "inference_device": embedding_audit.get("device"),
        "inference_precision": embedding_audit.get("precision"),
        "inference_batch_size": embedding_audit.get("batch_size"),
        "inference_threads": embedding_audit.get("threads"),
        "inference_mean_batch_forward_seconds": embedding_audit.get("mean_batch_forward_seconds"),
        "inference_mean_forward_per_event_seconds": (embedding_audit.get("mean_batch_forward_seconds")/embedding_audit.get("batch_size")
            if embedding_audit.get("mean_batch_forward_seconds") is not None and embedding_audit.get("batch_size") else None)}
    write_json(manifest_path, manifest)
    print(json.dumps({"array_cache": "built", "bars": count, "calendar_days": len(dates),
                      "dataset_signature": signature}), flush=True)
    return arrays, manifest


def write_report(result, curve, decisions, cfg, manifest, report_dir=DEFAULT_REPORT,
                 start=SIM_START, end=SIM_END, study_dir=DEFAULT_STUDY,
                 parameters_source=None):
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    source_path = Path(parameters_source) if parameters_source else None
    if source_path:
        try:
            parameter_label = source_path.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            parameter_label = source_path.as_posix()
    else:
        parameter_label = "runtime configuration"
    start_ts = pd.to_datetime(start, utc=True)
    end_ts = pd.to_datetime(end, utc=True)
    start_iso = start_ts.isoformat().replace("+00:00", "Z")
    end_iso = end_ts.isoformat().replace("+00:00", "Z")
    gap_timestamps = pd.to_datetime(manifest.get("mark_basis_reconstructed_timestamps", []), utc=True)
    mark_basis_gap_count = int(((gap_timestamps >= start_ts) & (gap_timestamps < end_ts)).sum())
    predictions = pd.read_json(Path(study_dir) / "processed/event_predictions_walkforward.jsonl", lines=True)
    news_source = manifest.get("news_source", "unknown")
    news_availability_basis = manifest.get("news_availability_basis", "unrecorded")
    news_content_basis = manifest.get("news_content_basis", "historical content snapshots unavailable")
    prediction_delivery = predictions.decision_ms.to_numpy(dtype=np.int64)
    start_ms, end_ms = millis(start_iso), millis(end_iso)
    window_predictions = predictions.loc[(prediction_delivery >= start_ms) & (prediction_delivery < end_ms)]
    probabilities = window_predictions[["p_up", "p_range", "p_down"]].to_numpy(dtype=np.float64)
    labels = window_predictions.label.to_numpy(dtype=np.int64)
    if len(labels):
        predicted_labels = probabilities.argmax(axis=1)
        jev_window_metrics = {
            "n": int(len(labels)),
            "accuracy": float((predicted_labels == labels).mean()),
            "log_loss": float(-np.log(np.clip(probabilities[np.arange(len(labels)), labels], 1e-15, 1.)).mean()),
            "multiclass_brier": float(np.square(probabilities - np.eye(3)[labels]).sum(axis=1).mean()),
            "label_counts": np.bincount(labels, minlength=3).tolist(),
            "predicted_class_counts": np.bincount(predicted_labels, minlength=3).tolist(),
        }
    else:
        jev_window_metrics = {"n": 0, "accuracy": None, "log_loss": None,
                              "multiclass_brier": None, "label_counts": [0, 0, 0],
                              "predicted_class_counts": [0, 0, 0]}
    equity_path = report_dir/"minute_equity.csv"
    decisions_path = report_dir/"trade_decisions.csv"
    parameters_path = report_dir/"strategy_parameters.json"
    cfg.save(parameters_path)
    curve.to_csv(equity_path,index=False)
    decisions.to_csv(decisions_path,index=False)
    minute = curve.copy()
    minute["datetime"] = pd.to_datetime(minute.timestamp,unit="ms",utc=True)
    minute["date"] = minute.datetime.dt.strftime("%Y-%m-%d")
    minute["month"] = minute.datetime.dt.strftime("%Y-%m")
    initial_equity = float(result["initial_equity"])
    peak = minute.equity.cummax().clip(lower=initial_equity)
    minute["drawdown_pct"] = (1.-minute.equity/peak)*100.
    daily = minute.groupby("date",sort=True).agg(equity=("equity","last"),
                    daily_max_drawdown_pct=("drawdown_pct","max"), fills=("fills","sum")).reset_index()
    previous_daily = np.r_[initial_equity, daily.equity.to_numpy(dtype=np.float64)[:-1]]
    daily["net_pnl"] = daily.equity.to_numpy(dtype=np.float64)-previous_daily
    daily["return_pct"] = (daily.equity.to_numpy(dtype=np.float64)/previous_daily-1.)*100.
    daily_path = report_dir/"daily.csv"
    daily.to_csv(daily_path,index=False)
    month_end = minute.groupby("month",sort=True).tail(1).reset_index(drop=True)
    previous = np.r_[initial_equity,month_end.equity.to_numpy(dtype=np.float64)[:-1]]
    monthly_fill_counts = minute.groupby("month",sort=True).agg(fills=("fills","sum")).reset_index()
    monthly = pd.DataFrame({"month":month_end.month,"end_equity":month_end.equity,
        "net_pnl":month_end.equity.to_numpy(dtype=np.float64)-previous,
        "return_pct":(month_end.equity.to_numpy(dtype=np.float64)/previous-1.)*100.,
        "month_max_drawdown_pct":minute.groupby("month",sort=True).drawdown_pct.max().to_numpy()})
    monthly = monthly.merge(monthly_fill_counts,on="month",how="left")
    decisions = decisions.copy()
    decisions["month"] = pd.to_datetime(decisions.timestamp,unit="ms",utc=True).dt.strftime("%Y-%m")
    decision_monthly = decisions.groupby("month",sort=True).agg(
        tp_fills=("tp_count","sum"), grid_adds=("add_count","sum"),
        jev_signals=("jev_trigger_count","sum"), opposite_closes=("opposite_close_count","sum"),
        fees=("fee","sum")).reset_index()
    monthly = monthly.merge(decision_monthly,on="month",how="left").fillna(
        {"fills":0,"tp_fills":0,"grid_adds":0,"jev_signals":0,"opposite_closes":0,"fees":0.})
    monthly_path=report_dir/"monthly.csv"
    monthly.to_csv(monthly_path,index=False)
    daily_returns = daily.return_pct.to_numpy(dtype=np.float64)/100.
    elapsed_years = float(result["bars"])/(365.25*86_400.)
    flat_day_mask = daily.return_pct.abs() <= 1e-9
    flat_month_mask = monthly.net_pnl.abs() <= 1e-9
    active_month_mask = monthly.fills.to_numpy(dtype=np.float64) > 0
    longest_flat_month_streak = 0
    current_flat_month_streak = 0
    for active in active_month_mask:
        if active:
            current_flat_month_streak = 0
        else:
            current_flat_month_streak += 1
            longest_flat_month_streak = max(longest_flat_month_streak, current_flat_month_streak)
    halt_crossings = (minute.loc[minute.drawdown_pct >= cfg.account_drawdown_stop*100., "timestamp"]
                      if cfg.account_drawdown_stop > 0 else pd.Series(dtype=np.int64))
    halt_observed_minute = (pd.to_datetime(halt_crossings.iloc[0], unit="ms", utc=True).isoformat()
                            if result["halted"] and len(halt_crossings) else None)
    result.update({
        "calendar_days": int(len(daily)),
        "elapsed_years": elapsed_years,
        "cagr_pct": float(((result["final_equity"]/result["initial_equity"])**(1./elapsed_years)-1.)*100.)
            if elapsed_years > 0 and result["final_equity"] > 0 else None,
        "daily_sharpe_365": float(daily_returns.mean()/daily_returns.std(ddof=1)*np.sqrt(365.))
            if len(daily_returns) > 1 and daily_returns.std(ddof=1) > 0 else None,
        "best_daily_return_pct": float(daily.return_pct.max()),
        "worst_daily_return_pct": float(daily.return_pct.min()),
        "winning_days": int((daily.return_pct > 1e-9).sum()),
        "losing_days": int((daily.return_pct < -1e-9).sum()),
        "flat_days": int(flat_day_mask.sum()),
        "winning_months": int((monthly.net_pnl > 0).sum()),
        "losing_months": int((monthly.net_pnl < 0).sum()),
        "flat_months": int(flat_month_mask.sum()),
        "active_months": int(active_month_mask.sum()),
        "longest_no_fill_month_streak": int(longest_flat_month_streak),
        "account_drawdown_stop_enabled": cfg.account_drawdown_stop > 0,
        "halt_drawdown_limit": cfg.account_drawdown_stop*100. if cfg.account_drawdown_stop > 0 else None,
        "first_minute_curve_at_or_beyond_halt_drawdown": halt_observed_minute,
        "jev_walkforward_metrics": manifest.get("walk_forward_metrics"),
        "jev_window_walkforward_metrics": jev_window_metrics,
        "jev_past_only_prior_baseline": manifest.get("past_only_prior_baseline"),
        "jev_accuracy_lift_vs_prior_baseline_percentage_points": manifest.get("accuracy_lift_vs_prior_baseline_percentage_points"),
        "jev_log_loss_delta_vs_prior_baseline": manifest.get("log_loss_delta_vs_prior_baseline"),
        "jev_brier_delta_vs_prior_baseline": manifest.get("brier_delta_vs_prior_baseline"),
        "jev_predicted_class_counts": manifest.get("predicted_class_counts"),
        "jev_confusion_matrix_rows_true_columns_predicted": manifest.get("confusion_matrix_rows_true_columns_predicted"),
        "jev_inference_device": manifest.get("inference_device"),
        "jev_inference_precision": manifest.get("inference_precision"),
        "jev_head_device": manifest.get("head_device"),
        "jev_inference_batch_size": manifest.get("inference_batch_size"),
        "jev_inference_threads": manifest.get("inference_threads"),
        "jev_inference_mean_batch_forward_seconds": manifest.get("inference_mean_batch_forward_seconds"),
        "jev_inference_mean_forward_per_event_seconds": manifest.get("inference_mean_forward_per_event_seconds"),
        "news_source": news_source,
        "news_availability_basis": news_availability_basis,
        "news_content_basis": news_content_basis,
    })
    result.update({"strategy_parameters":cfg.to_dict(),"strategy_parameters_sha256":digest(parameters_path),
        "requested_start":start_iso,"end_exclusive":end_iso,
        "dataset_signature":manifest["dataset_signature"],"mark_basis_reconstructed_rows":mark_basis_gap_count,
        "monthly_csv":"monthly.csv","daily_csv":"daily.csv","equity_minute_csv":"minute_equity.csv","decisions_csv":"trade_decisions.csv"})
    write_json(report_dir/"metrics.json",result)
    summary_path = report_dir/"README.md"
    last_bar_ts = pd.to_datetime(result["last_bar"], utc=True)
    daily_sharpe_text = ("N/A" if result["daily_sharpe_365"] is None
                         else f"{result['daily_sharpe_365']:.4f}")
    halt_description = (f"账户触发停机：{result['halted']}；首次分钟曲线达到 {result['halt_drawdown_limit']:.2f}% 回撤阈值：{result['first_minute_curve_at_or_beyond_halt_drawdown']}。"
                        if cfg.account_drawdown_stop > 0 else
                        f"账户回撤停机：已关闭；因强平触发的停机：{result['halted']}。")
    summary_path.write_text(
        "\n".join([
            f"# {start_ts.strftime('%Y-%m')} 至 {last_bar_ts.strftime('%Y-%m')} 历史回测（R1 / {cfg.news_delay_seconds:g} 秒 JEV）",
            "",
            f"- 回放区间：{start_iso} 至 {end_iso}（UTC，右端不含；实际最后一根 1 秒 bar：{result['last_bar']}）",
            f"- 本区间独立从 {result['initial_equity']:.4f} U 起始，未继承区间开始前的持仓或权益。",
            f"- 初始资金 / 期末权益：{result['initial_equity']:.4f} U / {result['final_equity']:.4f} U",
            f"- 净收益 / 区间回报 / CAGR：{result['net_profit']:.4f} U / {result['return_pct']:.4f}% / {result['cagr_pct']:.4f}%",
            f"- 最大回撤 / 日频 Sharpe（无风险利率 0，年化 365）：{result['max_drawdown_pct']:.4f}% / {daily_sharpe_text}",
            f"- 成交 / 网格加仓 / 止盈 / 风控止损 / 强平：{result['fills']} / {result['martingale_adds']} / {result['take_profit_fills']} / {result['risk_stops']} / {result['liquidations']}",
            f"- 每笔成交名义金额下限：{cfg.min_trade_notional:.4f} U；严格高于交易所历史最低名义额 100 U（后续规则最低 50 U）。",
            f"- 正收益 / 亏损 / 持平月份：{result['winning_months']} / {result['losing_months']} / {result['flat_months']}；{halt_description}",
            f"- 有成交月份 / 最长连续无成交月份：{result['active_months']} / {result['longest_no_fill_month_streak']}。",
            f"- JEV 信号 / JEV 平仓腿数 / JEV veto：{result['jev_signals']} / {result['jev_forced_leg_closes']} / {result['jev_veto_checks']}",
            f"- 单边风险锁存：{'开启' if cfg.risk_guard else '关闭'}；切换 / 保护性 veto / 反向腿平仓：{result.get('risk_guard_switches', 0)} / {result.get('risk_guard_veto_checks', 0)} / {result.get('risk_guard_closes', 0)}。",
            f"- 区间内 JEV 事件数 / accuracy / log-loss / Brier：{jev_window_metrics['n']} / {jev_window_metrics['accuracy']} / {jev_window_metrics['log_loss']} / {jev_window_metrics['multiclass_brier']}。",
            f"- 区间内 JEV 真实类别 up/range/down：{jev_window_metrics['label_counts']}；预测类别 up/range/down：{jev_window_metrics['predicted_class_counts']}。",
            f"- 手续费 / 净资金费：{result['fees']:.4f} U / {result['funding_net']:.4f} U",
            "",
            "## 回测口径",
            f"- 参数文件：`{parameter_label}`；账户回撤停机阈值：{'关闭' if cfg.account_drawdown_stop == 0 else f'{cfg.account_drawdown_stop*100:.2f}%'}；账户初始权益 {result['initial_equity']:.4f} U，逐秒回放，分钟指标只用于因果特征，不重调 R1 网格参数。",
            f"- JEV 概率由冻结 NanoJev backbone 与按月 walk-forward 训练/选择/校准的 ChoiceHead 产生；事件在 `available_ms + {cfg.news_delay_seconds:g}s` 才进入策略，阈值与持有期保持冻结参数。",
            "- 事件训练标签是未来 30 秒 BTCUSDT 价格区间代理，不代表新闻对价格的因果影响，也不等同于强平概率。",
        "- 行情为 Binance 公共 USD-M BTCUSDT 1 分钟归档及聚合逐笔成交构造的 1 秒 OHLC；资金费率来自公开历史归档。",
        f"- 区间内行情缺失 mark 分钟并用因果 basis 重建：{mark_basis_gap_count} 条；行情分钟数和资金费率覆盖审计见 dataset manifest。",
            f"- 新闻数据源：{news_source}；时间依据：{news_availability_basis}，策略另加 {cfg.news_delay_seconds:g} 秒延迟。",
            f"- 新闻内容口径：{news_content_basis}。",
            f"- JEV 主干推理：{manifest.get('inference_device')} / {manifest.get('inference_precision')} / batch {manifest.get('inference_batch_size')} / {manifest.get('inference_threads')} 线程；评分头训练与推理设备：{manifest.get('head_device')}。",
            "",
            "## 重要限制",
            "- 这是历史区间扩展压力测试，不是独立样本外收益承诺：R1 策略参数曾用 2026 数据筛选。",
            f"- 区间数据截至 {result['last_bar']} UTC；未将最后可用 bar 之后的数据外推。",
            "- 详细指标：`metrics.json`；逐日/逐月汇总：`daily.csv`、`monthly.csv`；完整分钟权益与交易决策轨迹为同目录 CSV。",
            "",
        ]), encoding="utf-8")
    write_json(report_dir/"run_manifest.json",{"start":start_iso,"end_exclusive":end_iso,
        "market_sha256":manifest["market_sha256"],"funding_sha256":manifest["funding_sha256"],
        "predictions_sha256":manifest["predictions_sha256"],"dataset_signature":manifest["dataset_signature"],
        "strategy_parameters_sha256":digest(parameters_path),
        "metrics_sha256":digest(report_dir/"metrics.json"),"equity_sha256":digest(equity_path),
        "summary_sha256":digest(summary_path),"daily_sha256":digest(daily_path),
        "monthly_sha256":digest(monthly_path),"decisions_sha256":digest(decisions_path)})
    print(json.dumps({"metrics":result,"monthly_rows":len(monthly),"daily_rows":len(daily)},indent=2),flush=True)


def run_backtest(start=SIM_START, end=SIM_END, disable_account_drawdown_stop=False,
                 report_dir=DEFAULT_REPORT, study_dir=DEFAULT_STUDY,
                 market_study_dir=BASE_STUDY,
                 parameters_path=ROOT/"reports/fmz_v2/final_parameters.json"):
    arrays, manifest = prepare_arrays(study_dir, start, end, market_study_dir)
    cfg=FMZConfig.load(parameters_path)
    if disable_account_drawdown_stop:
        cfg=replace(cfg, account_drawdown_stop=0.)
    data={**arrays}
    result,curve=backtest_fmz(data,cfg,start=start,end=end,record=True)
    decisions=curve.attrs["decisions"]
    write_report(result,curve,decisions,cfg,manifest,report_dir,start=start,end=end,study_dir=study_dir,
                 parameters_source=parameters_path)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("stage",choices=["prepare","run"])
    parser.add_argument("--start",default=SIM_START)
    parser.add_argument("--end",default=SIM_END)
    parser.add_argument("--disable-account-drawdown-stop",action="store_true")
    parser.add_argument("--report-dir",type=Path,default=DEFAULT_REPORT)
    parser.add_argument("--study-dir",type=Path,default=DEFAULT_STUDY)
    parser.add_argument("--market-study-dir",type=Path,default=BASE_STUDY)
    parser.add_argument("--parameters",type=Path,default=ROOT/"reports/fmz_v2/final_parameters.json")
    args=parser.parse_args()
    if args.stage=="prepare":
        prepare_arrays(args.study_dir, args.start, args.end, args.market_study_dir)
    else:
        run_backtest(args.start,args.end,args.disable_account_drawdown_stop,args.report_dir,args.study_dir,
                     args.market_study_dir,args.parameters)


if __name__=="__main__":
    main()
