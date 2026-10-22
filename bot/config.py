"""Settings loaded from environment variables / .env file."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

DEFAULT_WATCHLIST = ["QQQ", "SPY", "VOO", "AAPL", "MSFT", "NVDA", "AMZN", "TSLA"]


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return list(default)
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


@dataclass
class Settings:
    alpaca_key: str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    alpaca_secret: str = field(default_factory=lambda: os.getenv("ALPACA_SECRET_KEY", ""))
    data_feed: str = field(default_factory=lambda: os.getenv("ALPACA_DATA_FEED", "iex"))
    watchlist: list[str] = field(default_factory=lambda: _list("WATCHLIST", DEFAULT_WATCHLIST))
    timeframe: str = field(default_factory=lambda: os.getenv("TIMEFRAME", "15Min"))
    short_window: int = field(default_factory=lambda: int(os.getenv("SHORT_WINDOW", "20")))
    long_window: int = field(default_factory=lambda: int(os.getenv("LONG_WINDOW", "50")))
    starting_cash: float = field(default_factory=lambda: float(os.getenv("STARTING_CASH", "10000")))
    slippage_pct: float = field(default_factory=lambda: float(os.getenv("SLIPPAGE_PCT", "0.05")))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "paper_trades.db"))

    @property
    def has_alpaca_keys(self) -> bool:
        return bool(self.alpaca_key and self.alpaca_secret)


# ---------------------------------------------------------------------------
# Prop Pass bot configuration (config.yaml)
# ---------------------------------------------------------------------------
class ConfigError(ValueError):
    """Raised when config.yaml is missing something the bot needs to run safely."""


@dataclass
class Instrument:
    group: str = "other"
    leverage: float = 1.0
    spread_bps: float | None = None


@dataclass
class FilterConfig:
    htf_trend: dict = field(default_factory=lambda: {"enabled": False, "minutes": 30, "ema": 20})
    atr_band: dict = field(default_factory=lambda: {"enabled": False, "period": 14, "lookback_bars": 390,
                                                    "min_ratio": 0.5, "max_ratio": 2.5})
    liquidity: dict = field(default_factory=lambda: {"enabled": False, "min_dollar_volume_sip": 250_000,
                                                     "min_dollar_volume_iex": 5_000, "max_spread_bps": 15})
    chop: dict = field(default_factory=lambda: {"enabled": False, "period": 12, "min_efficiency": 0.3})

    def enabled(self) -> list[str]:
        return [name for name in ("htf_trend", "atr_band", "liquidity", "chop") if getattr(self, name)["enabled"]]


@dataclass
class StrategyConfig:
    timeframe: str = "5Min"
    fast: int = 9
    slow: int = 21
    allow_short: bool = True
    exit_on_opposite: bool = True
    entry_start: str = "09:45"
    entry_end: str = "15:30"
    flatten_at: str = "15:50"
    filters: FilterConfig = field(default_factory=FilterConfig)


@dataclass
class StopConfig:
    type: str = "atr"
    atr_mult: float = 1.5
    min_pct: float = 0.15
    max_pct: float = 1.5
    fixed_pct: float = 0.3


@dataclass
class TrailingConfig:
    enabled: bool = False
    activate_r: float = 1.0
    atr_mult: float = 1.0


@dataclass
class RiskSettings:
    risk_per_trade_pct: float = 0.25
    stop: StopConfig = field(default_factory=StopConfig)
    reward_risk: float = 2.0
    trailing: TrailingConfig = field(default_factory=TrailingConfig)
    max_positions: int = 3
    max_per_group: int = 2
    max_gross_exposure: float = 3.0
    max_trades_per_day: int = 20
    sizing_buffer_pct: float = 0.05


# Challenge rules have no defaults on purpose: firms differ, so every value must be set.
CHALLENGE_REQUIRED = ("profit_target_pct", "max_daily_loss_pct", "max_total_loss_pct", "daily_loss_base",
                      "day_start_reference", "drawdown_type", "daily_reset", "safety_buffer_pct")


@dataclass
class ChallengeSettings:
    profit_target_pct: float
    max_daily_loss_pct: float
    max_total_loss_pct: float
    daily_loss_base: str
    day_start_reference: str
    drawdown_type: str
    daily_reset: str
    safety_buffer_pct: float
    trailing_basis: str = "equity"
    trailing_lock_at_initial: bool = True
    stop_at_target: bool = True
    window_days: int = 30
    confirmed_firm_rules: bool = False


@dataclass
class CostSettings:
    commission_per_share: float = 0.0
    min_commission: float = 0.0
    sec_fee_rate: float = 0.0000278
    taf_per_share: float = 0.000166
    taf_max: float = 8.30
    slippage_bps: float = 1.0
    default_spread_bps: float = 2.0


@dataclass
class BacktestSettings:
    data_feed: str = "sip"
    cache_dir: str = "data/cache"
    starting_cash: float = 25_000
    train: list = field(default_factory=lambda: ["2023-01-01", "2024-06-30"])
    validation: list = field(default_factory=lambda: ["2024-07-01", "2025-03-31"])
    test: list = field(default_factory=lambda: ["2025-04-01", "2026-09-30"])
    challenge_every_n_days: int = 5
    monte_carlo_runs: int = 5000
    min_trades_per_day: float = 2
    regime_symbol: str = "QQQ"


@dataclass
class PaperSettings:
    data_feed: str = "iex"
    poll_seconds: int = 15
    state_dir: str = "state"
    log_dir: str = "logs"
    flatten_on_exit: bool = True
    close_unknown_positions: bool = True


@dataclass
class BotConfig:
    watchlist: list[str]
    instruments: dict[str, Instrument]
    strategy: StrategyConfig
    risk: RiskSettings
    challenge: ChallengeSettings
    costs: CostSettings
    backtest: BacktestSettings
    paper: PaperSettings

    def instrument(self, symbol: str) -> Instrument:
        return self.instruments.get(symbol, Instrument(group=symbol))

    def spread_bps(self, symbol: str) -> float:
        spread = self.instrument(symbol).spread_bps
        return self.costs.default_spread_bps if spread is None else spread


def _build(cls, raw: dict | None, where: str):
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a mapping")
    known = cls.__dataclass_fields__
    unknown = set(raw) - set(known)
    if unknown:
        raise ConfigError(f"Unknown setting(s) in {where}: {', '.join(sorted(unknown))}")
    return cls(**raw)


def _time(value: str, where: str) -> str:
    try:
        hh, mm = str(value).split(":")
        if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError
    except ValueError:
        raise ConfigError(f"{where} must be HH:MM, got {value!r}") from None
    return f"{int(hh):02d}:{int(mm):02d}"


def parse_config(raw: dict) -> BotConfig:
    """Turn the parsed YAML into a validated BotConfig, failing loudly on anything unsafe."""
    if not isinstance(raw, dict):
        raise ConfigError("config file is empty or not a mapping")
    watchlist = [str(s).upper() for s in raw.get("watchlist") or []]
    if not watchlist:
        raise ConfigError("watchlist is empty")

    instruments = {str(k).upper(): _build(Instrument, v, f"instruments.{k}") for k, v in (raw.get("instruments") or {}).items()}

    s_raw = dict(raw.get("strategy") or {})
    filters = FilterConfig()
    for name, values in (s_raw.pop("filters", None) or {}).items():
        if not hasattr(filters, name):
            raise ConfigError(f"Unknown filter: strategy.filters.{name}")
        setattr(filters, name, {**getattr(filters, name), **(values or {})})
    strategy = _build(StrategyConfig, s_raw, "strategy")
    strategy.filters = filters
    for key in ("entry_start", "entry_end", "flatten_at"):
        setattr(strategy, key, _time(getattr(strategy, key), f"strategy.{key}"))
    if not 1 < strategy.fast < strategy.slow:
        raise ConfigError("strategy.fast must be > 1 and smaller than strategy.slow")
    if not strategy.entry_start < strategy.entry_end <= strategy.flatten_at:
        raise ConfigError("need strategy.entry_start < entry_end <= flatten_at")

    r_raw = dict(raw.get("risk") or {})
    stop = _build(StopConfig, r_raw.pop("stop", None), "risk.stop")
    trailing = _build(TrailingConfig, r_raw.pop("trailing", None), "risk.trailing")
    risk = _build(RiskSettings, r_raw, "risk")
    risk.stop, risk.trailing = stop, trailing
    if not 0 < risk.risk_per_trade_pct <= 1:
        raise ConfigError("risk.risk_per_trade_pct must be in (0, 1]")
    if stop.type not in ("atr", "fixed"):
        raise ConfigError("risk.stop.type must be 'atr' or 'fixed'")
    if not 0 < stop.min_pct <= stop.max_pct:
        raise ConfigError("need 0 < risk.stop.min_pct <= risk.stop.max_pct")
    if risk.max_positions < 1 or risk.max_per_group < 1:
        raise ConfigError("risk.max_positions and risk.max_per_group must be >= 1")

    c_raw = raw.get("challenge")
    if not c_raw:
        raise ConfigError("challenge section is missing: set your firm's rules in config.yaml")
    missing = [k for k in CHALLENGE_REQUIRED if c_raw.get(k) is None]
    if missing:
        raise ConfigError(f"challenge settings missing: {', '.join(missing)}")
    challenge = _build(ChallengeSettings, c_raw, "challenge")
    challenge.daily_reset = _time(challenge.daily_reset, "challenge.daily_reset")
    choices = {"daily_loss_base": ("initial", "day_start"), "day_start_reference": ("balance", "equity", "higher"),
               "drawdown_type": ("static", "trailing"), "trailing_basis": ("equity", "balance")}
    for key, allowed in choices.items():
        if getattr(challenge, key) not in allowed:
            raise ConfigError(f"challenge.{key} must be one of {allowed}, got {getattr(challenge, key)!r}")
    for key in ("profit_target_pct", "max_daily_loss_pct", "max_total_loss_pct"):
        if not getattr(challenge, key) > 0:
            raise ConfigError(f"challenge.{key} must be positive")
    if challenge.safety_buffer_pct < 0:
        raise ConfigError("challenge.safety_buffer_pct can't be negative")

    return BotConfig(watchlist, instruments, strategy, risk, challenge,
                     _build(CostSettings, raw.get("costs"), "costs"),
                     _build(BacktestSettings, raw.get("backtest"), "backtest"),
                     _build(PaperSettings, raw.get("paper"), "paper"))


def load_config(path: str = "config.yaml") -> BotConfig:
    import yaml

    try:
        with open(path) as fh:
            raw = yaml.safe_load(fh)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    return parse_config(raw)
