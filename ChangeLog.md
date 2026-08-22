# ChangeLog

All notable changes to **Oxydan Chess Bot** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/) and the
project adheres to [Semantic Versioning](https://semver.org/).

---

## [12.0] — 2026-08-22

### Added
* **`config.py` module** — single source of truth for every runtime knob.
  Exposes a typed `Settings` dataclass (engine, matchmaking, time
  management, runtime safety, tablebase, Oxydan chat, Oxydan Learn)
  that `lichess-bot.py` and `matchmaking.py` both import. Defaults are
  baked in, so every new key is genuinely optional in `config.yml`.
* **`oxydan_chat.py` module** — replaces the previous inline chat
  implementation that had two `except TypeError: pass` branches
  silently dropping every message. The new `ChatSender` retries on
  429, surfaces every error to the logger with a stable `chat:`
  prefix, and exposes per-process stats
  (`attempts / success / skipped / failed`).
* **`oxydan_learn.py` module** — experimental opening-book weighting.
  Persists per-prefix win/loss/draw counts to `oxydan_learn.json`
  and re-weights book moves the next time the position appears. Off
  by default (`oxydan_learn.enabled: false`).
* **Master-level time allocation** in `OxydanV12._allocate_time`:
  four layered policies — panic (<2s), transition (2-12s), opening
  budget (first 10 plies at 35% clock), and complexity bonus past
  move 30 (+10% clock). All thresholds configurable via
  `time_management:` in `config.yml`.
* **`--self-test` CLI flag** on `lichess-bot.py`. Validates
  `config.yml`, engine binary, chat plumbing and Oxydan Learn
  without connecting to Lichess — completes in ~5 seconds.
* **UTF-8 stdout reconfiguration** in `_setup_logging()` so the bot
  renders emoji in log lines correctly on Windows consoles.
* **`config.yml` is now a self-documenting reference** with
  per-section comments explaining every key.
* **Docstrings on every public class and method** across
  `config.py`, `oxydan_chat.py`, `oxydan_learn.py`, `matchmaking.py`,
  and `lichess-bot.py`. Fork authors no longer have to read the
  source to understand behaviour.
* **`tournament_scan_interval`** (default 45s) — separate from
  `tournament_cooldown` (default 120s), so the bot catches more
  last-minute tournaments without hammering Lichess.

### Changed
* **Two legacy `SETTINGS` dicts consolidated** into `config.Settings`.
  The previous version had one in `lichess-bot.py` and another in
  `matchmaking.py` that drifted apart over time. There is now exactly
  one configuration object.
* **Matchmaking tier distribution is documented in the log** on
  start-up ("Low 10% | Mid 23% | High 35% | Elite 32%") so the bot's
  current behaviour is observable without reading code.
* **`config.yml` reorganised** to follow the Lichess-bot spec for
  the blocks the upstream toolchain cares about (`engine`,
  `challenge_policy`, `matchmaking`) and to expose Oxydan extensions
  in dedicated blocks (`time_management`, `runtime`, `tablebase`,
  `oxydan_chat`, `oxydan_learn`).
* **Tournament auto-join is decoupled from the main cooldown**.
  Previously a single 600s gate meant the bot could miss a
  tournament that was created 599s ago. v12 scans every 45s and
  joins at most every 120s.
* **`_is_in_tournament_game()`** now also consults a local 12h
  cache of tournaments we have already joined, in addition to
  `get_ongoing()`. The previous version was reliant on a single
  API call that was sometimes rate-limited or returned stale data.
* **Matchmaker uses the lichess.org `User-Agent` header** so the
  API team can identify Oxydan traffic in their dashboards
  (`OxydanBot/12.0 (+https://github.com/Scriptionz/Oxydan-Chess-Bot)`).
* **`index.html` bumped to v12** with a "What's new" grid and an
  embedded ChangeLog snippet. The dashboard is also more honest
  about its fallback behaviour when the API is unreachable.

### Fixed
* **Chat messages were silently dropped.** The fallback path in
  the old `_send_message` tried `client.bots.post_message(..., room="player")`,
  which is not a valid berserk parameter on 0.14+. The
  `except TypeError: continue` branch swallowed the error and the
  loop exited without printing anything. v12 only uses the
  documented `spectator=` kwarg and logs every send.
* **Greeting chat was sometimes rejected** because it was sent in
  the `gameFull` event before Lichess officially opened the game
  chat (state still `created`). v12 defers the greeting to the
  first `gameState` event after the game is `started` or after
  any move has been played.
* **Move parser tried `parse_san` first**; Lichess bot streams
  send moves in UCI notation, so the parser now goes straight to
  `parse_uci`. The SAN fallback was unreachable anyway and added
  latency on every move.
* **The matchmaker's `_is_in_tournament_game()`** was a single
  remote call with no local cache. The bot could spam the API
  and still miss events. v12 uses a hybrid local + remote check.
* **Tournament "already joined" detection** was naive. If the
  bot joined a tournament then the network blipped, the previous
  version might re-join on the next cycle. v12 records joined
  tournaments with a 12h TTL and short-circuits on subsequent
  attempts.
* **Engine pool cleanup at startup** was relying on a fragile
  `pool.task_done()` call that assumed the queue was a `JoinableQueue`.
  v12 uses a plain `Queue` and `get_nowait()` with explicit error
  handling.
* **The hard-coded `MESSAGES` dict lived in `lichess-bot.py` and
  shadowed the `chat:` block in `config.yml`**, meaning fork authors
  editing `chat.welcome` saw no effect. v12 puts branding in
  `oxydan_chat.py` and the on-by-default toggle in
  `oxydan_chat.enabled` / `oxydan_chat.chat_in_rated`.

### Removed
* `MM_SETTINGS` re-export from `matchmaking.py`. The matchmaking
  module now reads its own settings via `config.settings` and no
  longer publishes a second namespace.
* The unused `Tuning.pdf` reference from `config.yml` examples
  (the bot wrapper does not need tuning data; the engine's tuning
  files live alongside its source).

### Security
* **Token redaction** in `config.dump_for_debug()`. The raw token
  is replaced with `***REDACTED***` when the config is dumped to
  logs.
* The `--self-test` flag deliberately **does not** read or print
  the Lichess token. Fork authors can verify their setup offline.

---

## [11.0] — 2026-06-03

### Added
* **NNUE Evaluation Support** — enabled advanced neural network
  evaluation via `-DUSE_NNUE=1` compilation for sharper tactical play.
* **Pre-Flight Diagnostics** — `check_bot.py` automatically clears
  zombie processes and validates core module readiness before launch.
* **Live Dashboard Panel** — `index.html` powered by the Lichess
  API for real-time rating and live match telemetry tracking.
* **Branded Chat Engine** — dynamic conversational pools
  (`greeting_human`, `greeting_bot`, `losing_realization`).

### Changed
* **Core Architecture Overhaul** — `OxydanAegisV4` → `OxydanV11`.
* **Smart Time Allocation** — `LATENCY_BUFFER` to `0.07s`, refined
  calculation divisors.
* **Dynamic Parameter Syncing** — `config.yml` bound to runtime
  to adjust `max_games` and `MoveOverhead`.

### Fixed
* **Missing Function Crash** — implemented the missing `get_score`
  telemetry routine inside the engine pool.
