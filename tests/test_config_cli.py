import copy

import pytest
import yaml

import propbot
from bot.config import ConfigError, load_config, parse_config

RAW = yaml.safe_load(open("config.yaml"))


def test_shipped_config_is_valid():
    cfg = load_config("config.yaml")
    assert cfg.watchlist == ["QQQ", "SPY", "IWM", "DIA", "XLK", "SMH", "TQQQ", "SQQQ", "SOXL", "VOO"]
    assert cfg.risk.risk_per_trade_pct == 0.25 and cfg.risk.max_trades_per_day == 20
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
    propbot.main(["--config", cfg_path, "reset", "--yes"])
    assert not (tmp_path / "state" / "KILL").exists()


def test_cli_bad_config_path(tmp_path):
    with pytest.raises(SystemExit, match="config file not found"):
        propbot.main(["--config", str(tmp_path / "nope.yaml"), "status"])
