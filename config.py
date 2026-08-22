"""Oxydan Bot — Centralized configuration.

This module is the single source of truth for every runtime knob the bot
exposes. It reads ``config.yml`` (Lichess-bot spec + Oxydan extensions),
merges it with sane defaults, validates critical fields, and exports a
typed-ish :class:`Settings` object that other modules should import.

Why a dedicated module?
-----------------------
* Two legacy ``SETTINGS`` dicts used to live in ``lichess-bot.py`` and
  ``matchmaking.py``. They drifted apart, which made forks a nightmare.
* ``config.yml`` was a mix of Lichess-bot spec fields (engine, challenge)
  and Oxydan-only fields (chat, teams) but neither file actually parsed
  it consistently. Now everything goes through this one place.
* Fork authors only need to edit ``config.yml`` (or override via
  environment variables). Python knowledge is no longer required to
  retune the bot.

Usage
-----
>>> from config import settings
>>> print(settings.engine.threads, settings.matchmaking.max_games)
2 2
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

LOG = logging.getLogger("oxydan.config")

CONFIG_PATH = os.environ.get("OXYDAN_CONFIG", "config.yml")
LICHESS_TOKEN_ENV = "LICHESS_TOKEN"


# ---------------------------------------------------------------------------
# Default values — applied when the user omits a key in config.yml
# ---------------------------------------------------------------------------

_DEFAULTS: Dict[str, Any] = {
    # --- Connection ------------------------------------------------------
    "token": f"${LICHESS_TOKEN_ENV}",
    "url": "https://lichess.org/",

    # --- Engine (UCI) ----------------------------------------------------
    "engine": {
        "dir": "./src/",
        "name": "Ethereal",
        "protocol": "uci",
        "uci_options": {
            "Threads": 2,
            "Hash": 512,
            "MoveOverhead": 100,
            "Ponder": True,
            "SyzygyPath": "./syzygy",
            "SyzygyProbeDepth": 1,
        },
    },

    # --- Chat (in-game) --------------------------------------------------
    "chat": {
        "welcome": "Hi! Oxydan 11 active. Good luck!",
        "goodbye": "GG! Thanks for the game.",
    },

    # --- Teams -----------------------------------------------------------
    "teams": {
        "priority_mode": True,
        "allowed_teams": [
            "lichess-bots",
            "computer-chess-club",
        ],
    },

    # --- Incoming challenges --------------------------------------------
    "challenge_policy": {
        "accept_bot": True,
        "accept_human": True,
        "rated": True,
        "time_controls": ["bullet", "blitz", "rapid", "classical"],
        "min_time": 30,
        "max_time": 2000,
    },

    # --- Matchmaking (custom Oxydan logic, NOT the upstream lichess-bot) -
    "matchmaking": {
        "allow_feed": True,
        "challenge_interval": 60,
        "challenge_timeout": 30,
        "max_games": 2,
        "chess960_chance": 0.10,
        "rated_mode": True,
        "auto_tournament": True,
        "join_upcoming_mins": 15,
        "only_bot_tourneys": False,
        "tournament_cooldown": 120,
        "tournament_scan_interval": 45,
        "opponent_min_rating": 1500,
        "opponent_max_rating": 4000,
        "opponent_rating_difference": 700,
        "max_games_per_opponent": 3,
        "opponent_history_seconds": 3600,
        "blacklist_minutes": 60,
        "failed_challenge_blacklist_minutes": 10,
        "pool_refresh_seconds": 600,
        "safety_lock_time": 45,
        "time_controls": [
            "1+0", "1+1", "2+1", "3+0", "3+2",
            "5+0", "5+2", "10+0", "10+2",
        ],
        "challenge_bots": True,
        "challenge_humans": True,
        "selection": "random",
        "tier_elite": [2700, 4000],
        "tier_high":  [2300, 2700],
        "tier_mid":   [2000, 2300],
        "tier_low":   [1500, 2000],
        "tier_thresholds": {"LOW": 0.10, "MID": 0.33, "HIGH": 0.68},
        "losing_streak_limit": 3,
        "rating_drop_threshold": 50,
        "protection_game_count": 10,
        "permanent_blacklist": ["waychess-bot"],
        "tc_pool_max_10": ["1+0", "1+1", "2+1", "3+0", "3+2", "5+0", "5+2", "10+0", "10+2"],
        "tc_pool_all": [
            "1+0", "1+1", "2+1", "3+0", "3+2",
            "5+0", "5+2", "10+0", "10+2", "10+3", "30+0",
        ],
    },

    # --- Time management (bot-level, on top of UCI Move Overhead) -------
    "time_management": {
        "latency_buffer_ms": 70,
        "panic_threshold_s": 2.0,
        "transition_threshold_s": 12.0,
        "opening_move_count": 10,
        "opening_time_fraction": 0.35,
        "complexity_moves_threshold": 30,
        "complexity_extra_fraction": 0.10,
    },

    # --- Bot behaviour & runtime safety ---------------------------------
    "runtime": {
        "max_total_runtime_seconds": 21600,   # 6h GitHub Actions window
        "max_game_time_limit": 1800,          # refuse >30+0
        "min_game_seconds_remaining": 300,     # 5min safety before runtime ends
        "abort_wait_seconds": 60,
        "min_time_to_decline": 600,
        "max_parallel_games": 2,
        "stop_file": "STOP.txt",
    },

    # --- Tablebase lookup -----------------------------------------------
    "tablebase": {
        "online_enabled": True,
        "min_time_for_lookup": 12.0,
        "max_pieces": 7,
    },

    # --- Chat engine (Oxydan branding) ----------------------------------
    "oxydan_chat": {
        "enabled": True,
        "chat_in_rated": True,
        "score_chat_enabled": False,
        "losing_score_threshold": -300,
    },

    # --- Oxydan Learn (experimental opening learning) ------------------
    "oxydan_learn": {
        "enabled": False,
        "data_file": "oxydan_learn.json",
        "min_games_for_weighting": 5,
        "win_weight_multiplier": 1.5,
        "loss_weight_multiplier": 0.3,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``overlay`` into ``base`` without mutating either."""
    out = dict(base)
    for key, value in (overlay or {}).items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, dict)
        ):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _sub(d: Dict[str, Any], *path: str, default: Any = None) -> Any:
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


# ---------------------------------------------------------------------------
# Typed-ish settings — modules import this, not the raw dict
# ---------------------------------------------------------------------------

@dataclass
class EngineSettings:
    dir: str = "./src/"
    name: str = "Ethereal"
    protocol: str = "uci"
    uci_options: Dict[str, Any] = field(default_factory=dict)
    binary_path: str = ""

    def __post_init__(self) -> None:
        self.binary_path = os.path.join(self.dir, self.name)


@dataclass
class ChallengePolicy:
    accept_bot: bool = True
    accept_human: bool = True
    rated: bool = True
    time_controls: List[str] = field(default_factory=lambda: ["bullet", "blitz", "rapid", "classical"])
    min_time: int = 30
    max_time: int = 2000


@dataclass
class MatchmakingSettings:
    allow_feed: bool = True
    challenge_interval: int = 60
    challenge_timeout: int = 30
    max_games: int = 2
    chess960_chance: float = 0.10
    rated_mode: bool = True
    auto_tournament: bool = True
    join_upcoming_mins: int = 15
    only_bot_tourneys: bool = False
    tournament_cooldown: int = 120
    tournament_scan_interval: int = 45
    opponent_min_rating: int = 1500
    opponent_max_rating: int = 4000
    opponent_rating_difference: int = 700
    max_games_per_opponent: int = 3
    opponent_history_seconds: int = 3600
    blacklist_minutes: int = 60
    failed_challenge_blacklist_minutes: int = 10
    pool_refresh_seconds: int = 600
    safety_lock_time: int = 45
    time_controls: List[str] = field(default_factory=list)
    challenge_bots: bool = True
    challenge_humans: bool = True
    selection: str = "random"
    tier_elite: Tuple[int, int] = (2700, 4000)
    tier_high:  Tuple[int, int] = (2300, 2700)
    tier_mid:   Tuple[int, int] = (2000, 2300)
    tier_low:   Tuple[int, int] = (1500, 2000)
    tier_thresholds: Dict[str, float] = field(default_factory=lambda: {"LOW": 0.10, "MID": 0.33, "HIGH": 0.68})
    losing_streak_limit: int = 3
    rating_drop_threshold: int = 50
    protection_game_count: int = 10
    permanent_blacklist: Set[str] = field(default_factory=set)
    tc_pool_max_10: List[str] = field(default_factory=list)
    tc_pool_all:   List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Cast list-typed fields defensively
        self.time_controls = list(self.time_controls or [])
        self.tc_pool_max_10 = list(self.tc_pool_max_10 or [])
        self.tc_pool_all   = list(self.tc_pool_all or [])
        self.permanent_blacklist = {b.lower() for b in (self.permanent_blacklist or set())}
        # Tuples (YAML may give lists)
        self.tier_elite = tuple(self.tier_elite)
        self.tier_high  = tuple(self.tier_high)
        self.tier_mid   = tuple(self.tier_mid)
        self.tier_low   = tuple(self.tier_low)


@dataclass
class TimeManagementSettings:
    latency_buffer_ms: int = 70
    panic_threshold_s: float = 2.0
    transition_threshold_s: float = 12.0
    opening_move_count: int = 10
    opening_time_fraction: float = 0.35
    complexity_moves_threshold: int = 30
    complexity_extra_fraction: float = 0.10


@dataclass
class RuntimeSettings:
    max_total_runtime_seconds: int = 21600
    max_game_time_limit: int = 1800
    min_game_seconds_remaining: int = 300
    abort_wait_seconds: int = 60
    min_time_to_decline: int = 600
    max_parallel_games: int = 2
    stop_file: str = "STOP.txt"


@dataclass
class TablebaseSettings:
    online_enabled: bool = True
    min_time_for_lookup: float = 12.0
    max_pieces: int = 7


@dataclass
class OxydanChatSettings:
    enabled: bool = True
    chat_in_rated: bool = True
    score_chat_enabled: bool = False
    losing_score_threshold: int = -300


@dataclass
class OxydanLearnSettings:
    enabled: bool = False
    data_file: str = "oxydan_learn.json"
    min_games_for_weighting: int = 5
    win_weight_multiplier: float = 1.5
    loss_weight_multiplier: float = 0.3


@dataclass
class Settings:
    token: str = ""
    url: str = "https://lichess.org/"
    engine: EngineSettings = field(default_factory=EngineSettings)
    chat: Dict[str, str] = field(default_factory=dict)
    teams: Dict[str, Any] = field(default_factory=dict)
    challenge_policy: ChallengePolicy = field(default_factory=ChallengePolicy)
    matchmaking: MatchmakingSettings = field(default_factory=MatchmakingSettings)
    time_management: TimeManagementSettings = field(default_factory=TimeManagementSettings)
    runtime: RuntimeSettings = field(default_factory=RuntimeSettings)
    tablebase: TablebaseSettings = field(default_factory=TablebaseSettings)
    oxydan_chat: OxydanChatSettings = field(default_factory=OxydanChatSettings)
    oxydan_learn: OxydanLearnSettings = field(default_factory=OxydanLearnSettings)
    config_path: str = CONFIG_PATH
    raw: Dict[str, Any] = field(default_factory=dict)

    # Backwards-compat flat accessors (legacy lichess-bot.py / matchmaking.py)
    @property
    def engine_path(self) -> str:
        return self.engine.binary_path

    @property
    def book_path(self) -> str:
        return _sub(self.raw, "engine", "book_path", default="./book.bin")

    @property
    def max_parallel_games(self) -> int:
        return self.runtime.max_parallel_games

    @property
    def max_total_runtime(self) -> int:
        return self.runtime.max_total_runtime_seconds

    @property
    def max_game_time_limit(self) -> int:
        return self.runtime.max_game_time_limit

    @property
    def min_game_seconds_remaining(self) -> int:
        return self.runtime.min_game_seconds_remaining

    @property
    def min_time_to_decline(self) -> int:
        return self.runtime.min_time_to_decline

    @property
    def latency_buffer(self) -> float:
        return self.time_management.latency_buffer_ms / 1000.0

    @property
    def tablebase_piece_limit(self) -> int:
        return self.tablebase.max_pieces

    @property
    def online_tablebase_enabled(self) -> bool:
        return self.tablebase.online_enabled

    @property
    def min_time_for_tablebase(self) -> float:
        return self.tablebase.min_time_for_lookup

    @property
    def abort_wait_seconds(self) -> int:
        return self.runtime.abort_wait_seconds

    @property
    def losing_score_threshold(self) -> int:
        return self.oxydan_chat.losing_score_threshold

    @property
    def chat_enabled(self) -> bool:
        return self.oxydan_chat.enabled

    @property
    def chat_in_rated(self) -> bool:
        return self.oxydan_chat.chat_in_rated

    @property
    def score_chat_enabled(self) -> bool:
        return self.oxydan_chat.score_chat_enabled

    @property
    def stop_file(self) -> str:
        return self.runtime.stop_file

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-style accessor for legacy code paths."""
        return _sub(self.raw, *key.split("."), default=default)

    def has_token(self) -> bool:
        return bool(self.token) and not self.token.startswith("$")


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _resolve_token(token_value: str) -> str:
    """Allow ``$LICHESS_TOKEN`` substitution or env-var direct value."""
    if not token_value:
        return os.environ.get(LICHESS_TOKEN_ENV, "") or ""
    if token_value.startswith(f"${LICHESS_TOKEN_ENV}"):
        env_val = os.environ.get(LICHESS_TOKEN_ENV, "")
        if env_val:
            return env_val
        LOG.warning(
            "$LICHESS_TOKEN placeholder in config.yml but no env var set. "
            "Set LICHESS_TOKEN or replace the placeholder in config.yml."
        )
        return ""
    return token_value


def load_settings(path: str = CONFIG_PATH) -> Settings:
    """Read ``config.yml``, merge with defaults, validate, return Settings."""
    raw: Dict[str, Any] = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
                if not isinstance(loaded, dict):
                    raise ValueError(f"{path} must be a YAML mapping, got {type(loaded).__name__}")
                raw = loaded
        except Exception as exc:
            LOG.error("Failed to read %s: %s — using defaults only.", path, exc)
    else:
        LOG.warning("%s not found — running with built-in defaults.", path)

    merged = _deep_merge(_DEFAULTS, raw)

    token = _resolve_token(merged.get("token", ""))

    settings = Settings(
        token=token,
        url=merged.get("url", "https://lichess.org/"),
        engine=EngineSettings(**merged.get("engine", {})),
        chat=merged.get("chat", {}),
        teams=merged.get("teams", {}),
        challenge_policy=ChallengePolicy(**merged.get("challenge_policy", {})),
        matchmaking=MatchmakingSettings(**merged.get("matchmaking", {})),
        time_management=TimeManagementSettings(**merged.get("time_management", {})),
        runtime=RuntimeSettings(**merged.get("runtime", {})),
        tablebase=TablebaseSettings(**merged.get("tablebase", {})),
        oxydan_chat=OxydanChatSettings(**merged.get("oxydan_chat", {})),
        oxydan_learn=OxydanLearnSettings(**merged.get("oxydan_learn", {})),
        config_path=path,
        raw=merged,
    )

    _validate(settings)
    return settings


def _validate(s: Settings) -> None:
    if not s.has_token():
        LOG.warning(
            "No LICHESS_TOKEN available — the bot will not be able to authenticate. "
            "Set the LICHESS_TOKEN env var (recommended) or fill in config.yml."
        )
    if s.matchmaking.max_games < 1:
        raise ValueError("matchmaking.max_games must be >= 1")
    if not (0.0 <= s.matchmaking.chess960_chance <= 1.0):
        raise ValueError("matchmaking.chess960_chance must be in [0, 1]")
    if s.time_management.panic_threshold_s >= s.time_management.transition_threshold_s:
        raise ValueError("time_management.panic_threshold_s must be < transition_threshold_s")
    if not s.engine.binary_path or s.engine.binary_path.endswith("/"):
        # Binary path may legitimately be a directory ending with / in some forks;
        # we just warn instead of failing so docker-mounted layouts keep working.
        LOG.debug("Engine binary path looks unusual: %r", s.engine.binary_path)


# ---------------------------------------------------------------------------
# Singleton — modules can `from config import settings`
# ---------------------------------------------------------------------------

settings: Settings = load_settings()


def reload(path: Optional[str] = None) -> Settings:
    """Re-read config (used by tests)."""
    global settings
    settings = load_settings(path or CONFIG_PATH)
    return settings


def dump_for_debug() -> str:
    """Return a redacted summary for logging."""
    s = settings
    safe = asdict(s)
    safe["token"] = "***REDACTED***" if s.token else ""
    safe["raw"] = "<redacted>"
    import json
    return json.dumps(safe, indent=2, default=str)


if __name__ == "__main__":
    # Manual smoke test: `python config.py`
    print(dump_for_debug())
