"""Kronos wrapper plumbing with a tiny RANDOMLY INITIALISED model (no pretrained weights needed):
checks shapes, causality (only history is used) and feature definitions, not forecast quality."""
import os

import numpy as np
import pandas as pd
import pytest

torch = pytest.importorskip("torch")
SRC = os.path.join(os.path.dirname(__file__), "..", "vendor", "Kronos")
if not os.path.isdir(SRC):
    pytest.skip("vendor/Kronos not cloned", allow_module_level=True)

from nq.kronos_bot.forecaster import KronosForecaster, _import_kronos, cached_features  # noqa: E402


def tiny():
    Kronos, KronosTokenizer, _ = _import_kronos()
    torch.manual_seed(0)
    tok = KronosTokenizer(d_in=6, d_model=16, n_heads=2, ff_dim=32, n_enc_layers=1, n_dec_layers=1, ffn_dropout_p=0.0,
                          attn_dropout_p=0.0, resid_dropout_p=0.0, s1_bits=4, s2_bits=4, beta=0.05, gamma0=1.0,
                          gamma=1.1, zeta=0.05, group_size=4)
    mdl = Kronos(s1_bits=4, s2_bits=4, n_layers=1, d_model=16, n_heads=2, ff_dim=32, ffn_dropout_p=0.0,
                 attn_dropout_p=0.0, resid_dropout_p=0.0, token_dropout_p=0.0, learn_te=True)
    return KronosForecaster("small", n_samples=4, context=64, model=mdl, tokenizer=tok)


def frame(n=200):
    idx = pd.date_range("2024-01-02 09:45", periods=n, freq="15min", tz="America/New_York")
    c = 16000 + np.cumsum(np.random.default_rng(1).normal(0, 5, n))
    return pd.DataFrame({"open": c, "high": c + 3, "low": c - 3, "close": c, "volume": 100.0}, index=idx)


def test_features_shape_and_ranges():
    f = frame()
    feats = tiny().features(f.iloc[:150], f.index[150:152])
    assert set(feats) == {"k_exp_ret", "k_prob_up", "k_disp", "k_range", "k_median_ret"}
    assert 0 <= feats["k_prob_up"] <= 1 and feats["k_disp"] >= 0 and feats["k_range"] >= 0


def test_future_candles_do_not_change_the_forecast(tmp_path):
    f = frame()
    k = tiny()
    a = cached_features(k, f, [f.index[150]], 2, str(tmp_path / "a.parquet"))
    g = f.copy()
    g.iloc[151:, :4] *= 1.5                                    # wild future
    b = cached_features(k, g, [g.index[150]], 2, str(tmp_path / "b.parquet"))
    pd.testing.assert_frame_equal(a, b)
    again = cached_features(k, f, [f.index[150]], 2, str(tmp_path / "a.parquet"))   # cached: not recomputed
    pd.testing.assert_frame_equal(a, again)
