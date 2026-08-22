"""Oxydan 12 — Matchmaking & tournament management.

Major changes vs the previous version:

* Tournament auto-join is no longer gated by a single 10-minute
  cooldown. We now scan every ``tournament_scan_interval`` (default
  45s) but rate-limit actual *join* POSTs to ``tournament_cooldown``
  (default 120s) so we never hammer Lichess. This catches more
  short-notice tournaments without exhausting the API budget.
* ``_is_in_tournament_game`` was the only source of truth for "we're
  already in a tournament"; it relied on ``client.games.get_ongoing()``
  which is rate-limited and sometimes returns stale data. We now also
  consult a local set of "tournaments we have already joined", with
  a TTL that matches Lichess's tournament lifetime (~12h).
* Rating protection is opt-in and uses the Lichess API's per-mode
  rating rather than a hard-coded baseline.
* All public methods log to the same ``oxydan`` logger so a fork
  author can grep the GitHub Actions output for one trace.
"""

from __future__ import annotations

import itertools
import json
import logging
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from config import settings

LOG = logging.getLogger("oxydan.matchmaker")

USER_AGENT = f"OxydanBot/12.0 (+https://github.com/Scriptionz/Oxydan-Chess-Bot)"
TOURNAMENT_TTL_SECONDS = 12 * 3600  # forget a tournament after 12h


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_tc(tc_str: str) -> Tuple[int, int]:
    if "+" in tc_str:
        base, inc = tc_str.split("+", 1)
        return int(base), int(inc)
    return int(tc_str), 0


def _tier_name(tier: Tuple[int, int]) -> str:
    if tier == settings.matchmaking.tier_elite:
        return "Elite"
    if tier == settings.matchmaking.tier_high:
        return "High"
    if tier == settings.matchmaking.tier_mid:
        return "Mid"
    if tier == settings.matchmaking.tier_low:
        return "Low"
    return "?"


# ---------------------------------------------------------------------------
# Rating tracker (with protection mode)
# ---------------------------------------------------------------------------

class RatingTracker:
    """Tracks the bot's per-mode rating and triggers protection on streaks."""

    DEFAULT_BASELINES = {
        "bullet": 2800, "blitz": 2800, "rapid": 2800,
        "classical": 2700, "chess960": 2200,
    }

    def __init__(self, client: Any) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._baseline = dict(self.DEFAULT_BASELINES)
        self._current = dict(self.DEFAULT_BASELINES)
        self._losing_streak = 0
        self._protection_games = 0
        self._in_protection = False

    def initialize_baselines(self) -> None:
        if self._client is None:
            return
        try:
            data = self._client.account.get()
            perfs = data.get("perfs", {}) or {}
            with self._lock:
                for mode in list(self._baseline):
                    entry = perfs.get(mode) or {}
                    rating = entry.get("rating")
                    if isinstance(rating, int):
                        self._baseline[mode] = rating
                self._current = dict(self._baseline)
            LOG.info("📊 Baselines: %s", self._current)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("📊 Could not fetch baseline ratings: %s", exc)

    def record_result(self, result: str, mode: str, new_rating: Optional[int] = None) -> None:
        with self._lock:
            was_in_protection = self._in_protection

            if isinstance(new_rating, int) and mode in self._current:
                old = self._current[mode]
                self._current[mode] = new_rating
                drop = old - new_rating
                if drop >= settings.matchmaking.rating_drop_threshold:
                    self._activate_protection(
                        f"{mode} rating dropped {drop} ({old}→{new_rating})"
                    )

            if result == "loss":
                self._losing_streak += 1
                if self._losing_streak >= settings.matchmaking.losing_streak_limit:
                    self._activate_protection(
                        f"{self._losing_streak} losses in a row"
                    )
            else:
                self._losing_streak = 0

            if was_in_protection:
                self._protection_games -= 1
                if self._protection_games <= 0:
                    self._in_protection = False
                    self._losing_streak = 0
                    LOG.info("✅ Protection mode ended, returning to normal tiers.")

    def _activate_protection(self, reason: str) -> None:
        if not self._in_protection:
            LOG.warning("🛡️ Protection ON: %s", reason)
            LOG.warning("🛡️ Next %d games pinned to Mid tier.",
                        settings.matchmaking.protection_game_count)
        self._in_protection = True
        self._protection_games = settings.matchmaking.protection_game_count

    def in_protection(self) -> bool:
        with self._lock:
            return self._in_protection


# ---------------------------------------------------------------------------
# Matchmaker
# ---------------------------------------------------------------------------

class Matchmaker:
    def __init__(self, client: Any, config: Dict[str, Any],
                 active_games: Any, token: Optional[str] = None) -> None:
        self._client = client
        self._raw_config = config or {}
        self._enabled = bool(self._raw_config.get("matchmaking", {}).get("allow_feed", True))
        self._active_games = active_games
        self._token = token or settings.token

        self._my_id: Optional[str] = None
        self._bot_pool: List[str] = []
        self._blacklist: Dict[str, datetime] = {}
        self._opponent_tracker: Dict[str, int] = {}
        self._opponent_lock = threading.Lock()

        self._last_pool_update = 0.0
        self._wait_timeout = 120

        # Tournament state
        self._registered_tournaments: Dict[str, float] = {}  # id -> expires_at
        self._last_tournament_join = 0.0
        self._last_tournament_scan = 0.0

        # Cleanup state
        self._last_cleanup = 0.0

        self._rating_tracker = RatingTracker(self._client)
        self._rating_tracker.initialize_baselines()
        self._initialize_id()

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------
    def _active_game_count(self) -> int:
        try:
            return self._active_games.count(include_pending=False)
        except Exception:  # noqa: BLE001
            return 0

    def _auth_headers(self) -> Dict[str, str]:
        h = {"User-Agent": USER_AGENT}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _initialize_id(self) -> None:
        try:
            self._my_id = self._client.account.get()["id"]
            LOG.info("Matchmaker connected. ID=%s", self._my_id)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Could not fetch account id: %s", exc)
            self._my_id = "oxydan"

    # ------------------------------------------------------------------
    # Tournament management
    # ------------------------------------------------------------------
    def _is_in_tournament_game(self) -> bool:
        """We are currently playing in a tournament game."""
        try:
            ongoing = self._client.games.get_ongoing()
            for g in ongoing or []:
                if g.get("tournamentId") or g.get("swissId"):
                    return True
        except Exception as exc:  # noqa: BLE001
            LOG.debug("get_ongoing failed: %s", exc)
        return False

    def _remember_tournament(self, tid: str) -> None:
        self._registered_tournaments[tid] = time.time() + TOURNAMENT_TTL_SECONDS

    def _already_knows_tournament(self, tid: str) -> bool:
        expires = self._registered_tournaments.get(tid)
        if expires is None:
            return False
        if expires < time.time():
            self._registered_tournaments.pop(tid, None)
            return False
        return True

    def _prune_tournaments(self) -> None:
        now = time.time()
        stale = [tid for tid, exp in self._registered_tournaments.items() if exp < now]
        for tid in stale:
            self._registered_tournaments.pop(tid, None)
        if stale:
            LOG.debug("Pruned %d expired tournament entries.", len(stale))

    def _fetch_arena_tournaments(self) -> List[Dict[str, Any]]:
        try:
            r = requests.get(
                "https://lichess.org/api/tournament",
                headers=self._auth_headers(), timeout=10,
            )
            if r.status_code == 429:
                raise RuntimeError("HTTP 429")
            if r.status_code == 200:
                data = r.json() or {}
                return list(data.get("created", []) or []) + list(data.get("started", []) or [])
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            LOG.debug("Arena fetch failed: %s", exc)
        return []

    def _fetch_team_arena_tournaments(self, teams: List[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for team in teams:
            try:
                r = requests.get(
                    f"https://lichess.org/api/team/{team}/arena",
                    headers=self._auth_headers(), timeout=10,
                )
                if r.status_code == 429:
                    raise RuntimeError("HTTP 429")
                if r.status_code == 200:
                    for line in (r.text or "").strip().split("\n"):
                        if line:
                            try:
                                out.append(json.loads(line))
                            except Exception:  # noqa: BLE001
                                continue
            except RuntimeError:
                raise
            except Exception as exc:  # noqa: BLE001
                LOG.debug("Team arena %s fetch failed: %s", team, exc)
        return out

    def _fetch_swiss_tournaments(self, teams: List[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for team in teams:
            try:
                r = requests.get(
                    f"https://lichess.org/api/team/{team}/swiss",
                    headers=self._auth_headers(),
                    params={"status": "created"}, timeout=10,
                )
                if r.status_code == 429:
                    raise RuntimeError("HTTP 429")
                if r.status_code == 200:
                    for line in (r.text or "").strip().split("\n"):
                        if line:
                            try:
                                out.append(json.loads(line))
                            except Exception:  # noqa: BLE001
                                continue
            except RuntimeError:
                raise
            except Exception as exc:  # noqa: BLE001
                LOG.debug("Swiss %s fetch failed: %s", team, exc)
        return out

    def _join_arena(self, tid: str) -> bool:
        try:
            r = requests.post(
                f"https://lichess.org/api/tournament/{tid}/join",
                headers=self._auth_headers(), timeout=10,
            )
            if r.status_code == 429:
                raise RuntimeError("HTTP 429")
            return r.status_code == 200
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            LOG.debug("Arena join %s failed: %s", tid, exc)
            return False

    def _join_swiss(self, sid: str) -> bool:
        try:
            r = requests.post(
                f"https://lichess.org/api/swiss/{sid}/join",
                headers=self._auth_headers(), timeout=10,
            )
            if r.status_code == 429:
                raise RuntimeError("HTTP 429")
            return r.status_code == 200
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            LOG.debug("Swiss join %s failed: %s", sid, exc)
            return False

    def _tournament_is_acceptable(self, t: Dict[str, Any]) -> bool:
        if settings.matchmaking.only_bot_tourneys:
            name = (t.get("fullName") or t.get("name") or "").lower()
            if "bot" not in name:
                return False
        starts_at = (t.get("startsAt") or 0) / 1000
        if starts_at > 0 and (starts_at - time.time()) > settings.matchmaking.join_upcoming_mins * 60:
            return False
        return True

    def _manage_tournaments(self) -> None:
        if not settings.matchmaking.auto_tournament:
            return

        # Throttle the *scan* to a sensible interval; the join is
        # additionally throttled below.
        now = time.time()
        if now - self._last_tournament_scan < settings.matchmaking.tournament_scan_interval:
            return
        self._last_tournament_scan = now
        self._prune_tournaments()

        if now - self._last_tournament_join < settings.matchmaking.tournament_cooldown:
            return

        teams = list(settings.teams.get("allowed_teams") or ["lichess-bots"])

        candidates: List[Tuple[str, str, Dict[str, Any]]] = []  # (kind, id, data)

        try:
            for t in self._fetch_arena_tournaments():
                tid = t.get("id")
                if tid and not self._already_knows_tournament(tid) and self._tournament_is_acceptable(t):
                    candidates.append(("arena", tid, t))
            for t in self._fetch_team_arena_tournaments(teams):
                tid = t.get("id")
                if tid and not self._already_knows_tournament(tid) and self._tournament_is_acceptable(t):
                    candidates.append(("team_arena", tid, t))
            for s in self._fetch_swiss_tournaments(teams):
                sid = s.get("id")
                if sid and not self._already_knows_tournament(sid) and self._tournament_is_acceptable(s):
                    candidates.append(("swiss", sid, s))
        except RuntimeError:
            # 429: skip this cycle, retry sooner next time.
            self._last_tournament_scan = now - settings.matchmaking.tournament_scan_interval / 2
            return

        if not candidates:
            return

        # Prefer tournaments starting soonest.
        def _start_key(item: Tuple[str, str, Dict[str, Any]]) -> float:
            t = item[2]
            return (t.get("startsAt") or 0) / 1000

        candidates.sort(key=_start_key)

        for kind, tid, data in candidates:
            joined = False
            try:
                if kind in ("arena", "team_arena"):
                    joined = self._join_arena(tid)
                elif kind == "swiss":
                    joined = self._join_swiss(tid)
            except RuntimeError:
                self._last_tournament_scan = now - settings.matchmaking.tournament_scan_interval / 2
                return

            if joined:
                self._remember_tournament(tid)
                self._last_tournament_join = time.time()
                LOG.info("🏆 Tournament joined (%s): %s — %s",
                         kind, data.get("fullName") or data.get("name"), tid)
                return  # one tournament per scan is plenty

    # ------------------------------------------------------------------
    # Challenge acceptance policy
    # ------------------------------------------------------------------
    def is_challenge_acceptable(self, challenge: Dict[str, Any]) -> Tuple[bool, str]:
        if self._is_in_tournament_game():
            return False, "Currently in a tournament game."

        variant = (challenge.get("variant") or {}).get("key", "standard")
        if variant not in ("standard", "chess960"):
            return False, f"Variant '{variant}' not supported."

        challenger = challenge.get("challenger") or {}
        user_id = (challenger.get("id") or "").lower()
        if not user_id:
            return False, "No challenger info."

        title = (challenger.get("title") or "").upper()
        is_bot = title == "BOT"
        rating = challenger.get("rating") or 0
        rated = bool(challenge.get("rated", False))

        if user_id in settings.matchmaking.permanent_blacklist:
            return False, f"{user_id} permanently blacklisted."

        tc = challenge.get("timeControl") or {}
        if tc.get("type") != "clock":
            return False, "Only clock games accepted."

        limit_sn = tc.get("limit", 0) or 0
        if limit_sn < 30 or limit_sn > settings.runtime.max_game_time_limit:
            return False, f"Time control out of range ({limit_sn}s)."

        with self._opponent_lock:
            games_with_user = self._opponent_tracker.get(user_id, 0)
        if games_with_user >= settings.matchmaking.max_games_per_opponent:
            return False, f"Already played {games_with_user} games with {user_id}."

        if not is_bot:
            if rating < 1500:
                return False, "Human rating below 1500."
            if rated:
                return False, "Humans must play casual."
            return True, f"Accepted human ({rating})"

        # Bot
        if rating < 1500:
            return False, "Bot rating below 1500."
        if 1500 <= rating < 2000 and rated:
            return False, "Bots 1500-2000 must play casual."
        if rating < 2300 and limit_sn > 600:
            return False, "Max 10+0 for sub-2300 bots."
        return True, f"Accepted bot ({rating})"

    # ------------------------------------------------------------------
    # Tier & target selection
    # ------------------------------------------------------------------
    def _pick_tier(self) -> Tuple[int, int]:
        if self._rating_tracker.in_protection():
            LOG.info("🛡️ Protection: pinning to Mid tier (%d games left).",
                     self._rating_tracker._protection_games)
            return settings.matchmaking.tier_mid

        t = settings.matchmaking.tier_thresholds
        r = random.random()
        if r < t["LOW"]:
            return settings.matchmaking.tier_low
        if r < t["MID"]:
            return settings.matchmaking.tier_mid
        if r < t["HIGH"]:
            return settings.matchmaking.tier_high
        return settings.matchmaking.tier_elite

    def _refresh_bot_pool(self) -> None:
        now = time.time()
        if self._bot_pool and (now - self._last_pool_update) < settings.matchmaking.pool_refresh_seconds:
            return
        try:
            stream = self._client.bots.get_online_bots()
            online = list(itertools.islice(stream, 200))
            self._bot_pool = [
                b.get("id")
                for b in online
                if b.get("id")
                and b.get("id").lower() != (self._my_id or "").lower()
                and b.get("id", "").lower() not in settings.matchmaking.permanent_blacklist
            ]
            random.shuffle(self._bot_pool)
            self._last_pool_update = now
            LOG.info("Bot pool refreshed: %d bots online.", len(self._bot_pool))
        except Exception as exc:  # noqa: BLE001
            if "429" in str(exc):
                raise
            LOG.warning("Bot pool refresh failed: %s", exc)
            time.sleep(10)

    def _find_suitable_target(self) -> Tuple[Optional[str], int, int, int, bool, str]:
        try:
            self._refresh_bot_pool()
        except Exception as exc:  # noqa: BLE001
            if "429" in str(exc):
                raise
            return None, 0, 0, 0, False, "?"

        tier = self._pick_tier()
        tier_name = _tier_name(tier)
        now = datetime.now()

        if tier == settings.matchmaking.tier_low:
            tc_pool = settings.matchmaking.tc_pool_max_10
            is_rated = False
        elif tier == settings.matchmaking.tier_mid:
            tc_pool = settings.matchmaking.tc_pool_max_10
            is_rated = settings.matchmaking.rated_mode and not self._rating_tracker.in_protection()
        else:
            tc_pool = settings.matchmaking.tc_pool_all
            is_rated = settings.matchmaking.rated_mode

        tc_str = random.choice(tc_pool)
        limit_sn, inc_sn = _parse_tc(tc_str)

        if limit_sn < 180:
            mode = "bullet"
        elif limit_sn < 480:
            mode = "blitz"
        elif limit_sn < 1500:
            mode = "rapid"
        else:
            mode = "classical"

        with self._opponent_lock:
            candidates = [
                b for b in self._bot_pool
                if (b.lower() not in self._blacklist or self._blacklist[b.lower()] <= now)
                and self._opponent_tracker.get(b.lower(), 0) < settings.matchmaking.max_games_per_opponent
            ][:50]

        if not candidates:
            return None, 0, 0, 0, False, tier_name

        try:
            r = requests.post(
                "https://lichess.org/api/users",
                headers=self._auth_headers(),
                data=",".join(candidates),
                timeout=10,
            )
            if r.status_code == 429:
                raise RuntimeError("HTTP 429")
            if r.status_code == 200:
                users_data = r.json() or []
                random.shuffle(users_data)
                for user in users_data:
                    bot_id = user.get("id")
                    rating = (user.get("perfs", {}) or {}).get(mode, {}).get("rating", 0) or 0
                    if tier[0] <= rating <= tier[1]:
                        return bot_id, rating, limit_sn, inc_sn, is_rated, tier_name
            else:
                raise RuntimeError(f"HTTP {r.status_code}")
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            LOG.debug("Bulk user fetch failed: %s — falling back to single lookups.", exc)
            for bot_id in candidates[:5]:
                try:
                    data = self._client.users.get_public_data(bot_id)
                    rating = (data.get("perfs", {}) or {}).get(mode, {}).get("rating", 0) or 0
                    time.sleep(0.3)
                    if tier[0] <= rating <= tier[1]:
                        return bot_id, rating, limit_sn, inc_sn, is_rated, tier_name
                except Exception as ex:  # noqa: BLE001
                    if "429" in str(ex):
                        raise
                    continue

        return None, 0, 0, 0, False, tier_name

    # ------------------------------------------------------------------
    # Result tracking
    # ------------------------------------------------------------------
    def record_game_result(self, result: str, mode: str,
                           new_rating: Optional[int] = None,
                           opponent_id: Optional[str] = None) -> None:
        self._rating_tracker.record_result(result, mode, new_rating)
        if opponent_id:
            key = opponent_id.lower()
            with self._opponent_lock:
                self._opponent_tracker[key] = self._opponent_tracker.get(key, 0) + 1

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def start(self) -> None:
        if not self._enabled:
            LOG.info("Matchmaker disabled via config.")
            return

        LOG.info("🚀 Matchmaker v12 active — distribution Low %s | Mid %s | High %s | Elite %s",
                 f"{int(settings.matchmaking.tier_thresholds['LOW']*100)}%",
                 f"{int((settings.matchmaking.tier_thresholds['MID']-settings.matchmaking.tier_thresholds['LOW'])*100)}%",
                 f"{int((settings.matchmaking.tier_thresholds['HIGH']-settings.matchmaking.tier_thresholds['MID'])*100)}%",
                 f"{int((1-settings.matchmaking.tier_thresholds['HIGH'])*100)}%")
        LOG.info("   Max per opponent: %d", settings.matchmaking.max_games_per_opponent)

        while True:
            try:
                # Periodic cleanup
                if (time.time() - self._last_cleanup) > settings.matchmaking.opponent_history_seconds:
                    self._cleanup_history()
                    self._last_cleanup = time.time()

                self._manage_tournaments()

                if self._is_in_tournament_game():
                    time.sleep(60)
                    continue

                if self._active_game_count() >= settings.runtime.max_parallel_games:
                    time.sleep(10)
                    continue

                try:
                    target, rating, limit_sn, inc_sn, is_rated, tier_name = \
                        self._find_suitable_target()
                except RuntimeError as exc:
                    if "429" in str(exc):
                        LOG.warning("Rate limit (429), waiting %ds.", self._wait_timeout)
                        time.sleep(self._wait_timeout)
                        self._wait_timeout = min(self._wait_timeout * 2, 900)
                        continue
                    raise

                if not target:
                    time.sleep(45)
                    continue

                variant = "chess960" if random.random() < settings.matchmaking.chess960_chance else "standard"
                rated_str = "Rated" if is_rated else "Casual"
                mins, secs = divmod(limit_sn, 60)
                tc_label = f"{mins}:{secs:02d}+{inc_sn}" if secs else f"{mins}+{inc_sn}"

                with self._opponent_lock:
                    played = self._opponent_tracker.get(target.lower(), 0)
                LOG.info("[%s] → %s (%d) | %s | %s | %s | game %d/%d",
                         tier_name, target, rating, rated_str, tc_label, variant,
                         played, settings.matchmaking.max_games_per_opponent)

                self._blacklist[target.lower()] = datetime.now() + timedelta(
                    minutes=settings.matchmaking.blacklist_minutes
                )
                try:
                    self._client.challenges.create(
                        username=target,
                        rated=is_rated,
                        variant=variant,
                        clock_limit=limit_sn,
                        clock_increment=inc_sn,
                    )
                    self._wait_timeout = 120
                except Exception as exc:  # noqa: BLE001
                    if "429" in str(exc):
                        raise
                    LOG.warning("Challenge creation failed: %s", exc)
                    self._blacklist[target.lower()] = datetime.now() + timedelta(
                        minutes=settings.matchmaking.failed_challenge_blacklist_minutes
                    )

                time.sleep(settings.matchmaking.safety_lock_time)

            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                if "429" in err:
                    LOG.warning("Rate limit (429), waiting %ds.", self._wait_timeout)
                    time.sleep(self._wait_timeout)
                    self._wait_timeout = min(self._wait_timeout * 2, 900)
                else:
                    LOG.warning("Matchmaker error: %s", err)
                    time.sleep(30)

    def _cleanup_history(self) -> None:
        # Forget blacklisted opponents after the period; opponent_tracker
        # is reset every opponent_history_seconds to keep memory bounded.
        cutoff = datetime.now()
        self._blacklist = {
            k: v for k, v in self._blacklist.items() if v > cutoff
        }
        with self._opponent_lock:
            old_count = len(self._opponent_tracker)
            self._opponent_tracker.clear()
        LOG.debug("Cleanup: %d opponent records cleared.", old_count)
