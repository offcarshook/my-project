import copy

import pytest
import yaml

import propbot
from bot.config import ConfigError, load_config, parse_config

RAW = yaml.safe_load(open("config.yaml"))


def test_shipped_config_is_valid():
    cfg = load_config("config.yaml")
    assert cfg.watchlist == ["QQQ", "SPY", "IWM", "DIA", "XLK", "SMH", "TQQQ", "SQQQ", "SOXL", "VOO"]
    assert cfg.risk.risk_per_trade_pct == 0.25 and cfg.risk.max_trades_per_day == 15
    assert cfg.risk.max_gross_exposure == 4.0 and cfg.risk.sizing_buffer_pct == 0.02
    assert cfg.backtest.data_feed == cfg.paper.data_feed == "iex"
    assert cfg.instrument("SQQQ").leverage < 0


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r.pop("challenge"), "challenge section is missing"),
    (lambda r: r["challenge"].pop("drawdown_type"), "drawdown_type"),
    (lambda r: r["challenge"].update(drawdown_type="sometimes"), "drawdown_type must be one of"),
    (lambda r: r["challenge"].update(daily_reset="25:00"), "HH:MM"),
    (lambda r: r["risk"].update(risk_per_trade_pct=5), "risk_per_trade_pct"),
    (lambda r: r["strategy"].update(fast=30), "fast"),
    (lambda r: r["strategy"]["filters"].update(magic={}), "Unknown filter"),
    (lambda r: r["risk"].update(typo=1), "Unknown setting"),
    # review fixes: unsafe values that used to slip through
    (lambda r: r["watchlist"].append("SPXL"), "missing from instruments"),
    (lambda r: r["strategy"]["filters"]["htf_trend"].update(enable=True), "Unknown setting.*htf_trend"),
    (lambda r: r["strategy"]["filters"]["liquidity"].update(max_spread_bp=5), "Unknown setting.*liquidity"),
    (lambda r: r["strategy"]["filters"]["chop"].update(enabled="yes"), "true or false"),
    (lambda r: r["strategy"]["filters"]["atr_band"].update(min_ratio=3, max_ratio=2), "min_ratio"),
    (lambda r: r["strategy"]["filters"]["htf_trend"].update(minutes=7), "multiple"),
    (lambda r: r["strategy"].update(timeframe="5min"), "timeframe"),
    (lambda r: r["risk"].update(sizing_buffer_pct=-0.1), "sizing_buffer_pct"),
    (lambda r: r["risk"].update(reward_risk=-2), "reward_risk"),
    (lambda r: r["risk"]["trailing"].update(atr_mult=-1), "trailing.atr_mult"),
    (lambda r: r["risk"]["stop"].update(max_pct=40), "max_pct"),
    (lambda r: r["paper"].update(data_feed="nasdaq"), "data_feed"),
    (lambda r: r["backtest"].update(validation=["2024-07-01", "2025-09-30"]), "not overlap"),
    (lambda r: r["backtest"].update(test=["2025-04-01", "2099-12-31"]), "before today"),
    (lambda r: r["backtest"].update(min_trades_per_day=20), "min_trades_per_day"),
])
def test_unsafe_configs_fail_loudly(mutate, message):
    raw = copy.deepcopy(RAW)
    mutate(raw)
    with pytest.raises(ConfigError, match=message):
        parse_config(raw)


def write_cfg(tmp_path, **paper):
    raw = copy.deepcopy(RAW)
    raw["paper"].update(state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"), **paper)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    return str(path)


def test_cli_backtest_without_keys_explains(tmp_path, monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(SystemExit, match="ALPACA_API_KEY"):
        propbot.main(["--config", write_cfg(tmp_path), "backtest"])


def test_cli_stop_and_reset(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    cfg_path = write_cfg(tmp_path)
    propbot.main(["--config", cfg_path, "stop"])
    assert (tmp_path / "state" / "KILL").exists()
    with pytest.raises(SystemExit, match="--yes"):
        propbot.main(["--config", cfg_path, "reset"])
    with pytest.raises(SystemExit, match="--initial"):
        propbot.main(["--config", cfg_path, "reset", "--yes"])      # no keys and no balance given
    propbot.main(["--config", cfg_path, "reset", "--yes", "--initial", "25000"])
    assert not (tmp_path / "state" / "KILL").exists()


def test_cli_reset_refuses_while_bot_running(tmp_path, monkeypatch):
    import os
    cfg_path = write_cfg(tmp_path)
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "bot.pid").write_text(str(os.getpid()))   # this process is alive
    with pytest.raises(SystemExit, match="still running"):
        propbot.main(["--config", cfg_path, "reset", "--yes", "--initial", "25000"])


def test_cli_bad_config_path(tmp_path):
    with pytest.raises(SystemExit, match="config file not found"):
        propbot.main(["--config", str(tmp_path / "nope.yaml"), "status"])


def test_feed_names_are_case_insensitive():
    raw = copy.deepcopy(RAW)
    raw["paper"]["data_feed"] = "IEX"
    assert parse_config(raw).paper.data_feed == "iex"
