"""Pretrained Kronos as a feature source (official code: github.com/shiyu-coder/Kronos, MIT).

Setup (not vendored into git):
    git clone https://github.com/shiyu-coder/Kronos vendor/Kronos   # pinned: 67b630e
    pip install torch einops safetensors huggingface_hub
Checkpoints are downloaded from Hugging Face (NeoQuasar/Kronos-mini + Kronos-Tokenizer-2k, context
2048; NeoQuasar/Kronos-small + Kronos-Tokenizer-base, context 512) or loaded from local folders.

`KronosForecaster.features()` draws `n_samples` independent sample paths in ONE batch (the
official predict() averages samples internally, which would hide the forecast's uncertainty) and
returns per-decision features: expected return to the horizon, probability of finishing up,
dispersion of the sampled returns, and the expected high-low range, all relative to the last
close. Only candles up to the decision time are passed in.
"""
import os
import sys

import numpy as np
import pandas as pd

CHECKPOINTS = {
    "mini": ("NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-Tokenizer-2k", 2048),
    "small": ("NeoQuasar/Kronos-small", "NeoQuasar/Kronos-Tokenizer-base", 512),
}


def _import_kronos(src: str | None = None):
    src = src or os.environ.get("KRONOS_SRC", os.path.join(os.path.dirname(__file__), "..", "..", "vendor", "Kronos"))
    if src not in sys.path:
        sys.path.insert(0, os.path.abspath(src))
    from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402
    return Kronos, KronosTokenizer, KronosPredictor


class KronosForecaster:
    def __init__(self, size: str = "mini", n_samples: int = 16, context: int | None = None, T: float = 1.0,
                 top_p: float = 0.9, seed: int = 0, model=None, tokenizer=None, device: str = "cpu"):
        import torch
        Kronos, KronosTokenizer, KronosPredictor = _import_kronos()
        model_id, tok_id, max_ctx = CHECKPOINTS[size]
        self.context = min(context or max_ctx, max_ctx)
        tokenizer = tokenizer or KronosTokenizer.from_pretrained(os.environ.get("KRONOS_TOKENIZER", tok_id))
        model = model or Kronos.from_pretrained(os.environ.get("KRONOS_MODEL", model_id))
        model.eval()
        tokenizer.eval()
        self.predictor = KronosPredictor(model, tokenizer, device=device, max_context=self.context)
        self.n_samples, self.T, self.top_p, self.seed = n_samples, T, top_p, seed
        self.name = f"kronos-{size}-ctx{self.context}-n{n_samples}"
        self._torch = torch

    def features(self, history: pd.DataFrame, future_index: pd.DatetimeIndex) -> dict:
        """history: candles up to and including the decision candle (columns open/high/low/close/
        volume, DatetimeIndex); future_index: timestamps of the candles to forecast."""
        h = history.iloc[-self.context:]
        x_ts = pd.Series(h.index.tz_localize(None) if h.index.tz is not None else h.index)
        y_ts = pd.Series(future_index.tz_localize(None) if future_index.tz is not None else future_index)
        df = h[["open", "high", "low", "close", "volume"]].reset_index(drop=True)
        self._torch.manual_seed(self.seed)
        np.random.seed(self.seed)
        paths = self.predictor.predict_batch([df] * self.n_samples, [x_ts] * self.n_samples, [y_ts] * self.n_samples,
                                             pred_len=len(future_index), T=self.T, top_p=self.top_p, sample_count=1,
                                             verbose=False)
        last = float(h["close"].iloc[-1])
        end = np.array([p["close"].iloc[-1] for p in paths])
        ret = end / last - 1.0
        rng = np.array([(p["high"].max() - p["low"].min()) / last for p in paths])
        return {"k_exp_ret": float(ret.mean()), "k_prob_up": float((ret > 0).mean()), "k_disp": float(ret.std()),
                "k_range": float(rng.mean()), "k_median_ret": float(np.median(ret))}


def cached_features(forecaster, frame_all: pd.DataFrame, decisions: list, horizon: int, cache_path: str,
                    log_every: int = 25) -> pd.DataFrame:
    """Features for each decision timestamp (a candle CLOSE time in `frame_all`), forecasting the
    next `horizon` candles of the same series. Resumable: rows already in `cache_path` are skipped."""
    done = pd.read_parquet(cache_path) if os.path.exists(cache_path) else pd.DataFrame()
    have = set(done.index) if len(done) else set()
    rows, idx = [], frame_all.index
    for n, ts in enumerate(decisions):
        if ts in have:
            continue
        pos = idx.get_loc(ts)
        if pos + horizon >= len(idx) or pos < 64:
            continue
        hist = frame_all.iloc[:pos + 1]
        fut = idx[pos + 1:pos + 1 + horizon]
        rows.append({"ts": ts, **forecaster.features(hist, fut)})
        if len(rows) % log_every == 0:
            _save(done, rows, cache_path)
            print(f"  {forecaster.name}: {len(have) + len(rows)} forecasts (at {ts})", flush=True)
    return _save(done, rows, cache_path)


def _save(done, rows, path):
    new = pd.DataFrame(rows).set_index("ts") if rows else pd.DataFrame()
    out = pd.concat([done, new]) if len(done) else new
    if len(out):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        out.sort_index().to_parquet(path)
    return out
