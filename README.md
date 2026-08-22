# Oxydan Chess Bot (v12.0)

Oxydan is an advanced Lichess bot built upon the open-source **Ethereal**
chess engine, fine-tuned and optimized for stable deployment, tournament
matchmaking, and master-level time management.

* **Lichess Profile:** [@OxydanBot](https://lichess.org/@/OxydanBot)
* **Estimated Strength:** ~2800 ELO (Blitz / Bullet)
* **License:** GNU General Public License v3.0 (GPLv3)

> The dashboard at <https://scriptionz.github.io/Oxydan-Chess-Bot/> is
> generated from the public Lichess API and reflects this repository's
> `index.html`.

---

## ✨ What's new in v12

| Area | What changed |
|---|---|
| **Chat** | New `oxydan_chat.py` module; every message is logged with `chat: ✔/✘`. The previous `except TypeError: pass` branches that silently dropped every chat are gone. |
| **Tournaments** | Auto-join scans every 45s and joins at most every 120s, with a 12h local cache to prevent re-joining the same event. |
| **Time management** | Four-layer policy (panic / transition / opening / complexity) replaces the single time-allocation block. |
| **Config** | Single `config.py` with typed `Settings`; `config.yml` is now self-documenting with sane defaults for every key. |
| **Experimental** | `oxydan_learn.py` — book re-weighting based on the bot's own win/loss history. |
| **Ops** | `--self-test` validates setup offline; UTF-8 stdout so Windows logs render correctly. |

See [`ChangeLog.md`](./ChangeLog.md) for the full diff.

---

## 🚀 Key Features

* **Automated tournament management (`matchmaking.py`)** — scans
  general arena, team arena and Swiss tournaments every 45s and
  joins based on a tier distribution (Low 10 % | Mid 23 % | High
  35 % | Elite 32 % by default).
* **Master-level time management (`lichess-bot.py`)** — a four-layer
  policy allocates engine time based on the remaining clock, opening
  depth, and middlegame complexity.
* **CI/CD automation (`.github/workflows`)** — every 6 hours the bot
  is rebuilt, the opening book is downloaded, and the process is
  restarted cleanly.
* **Pre-flight diagnostics (`check_bot.py`)** — clears zombie
  processes and validates that the engine binary, config and chat
  plumbing are healthy before the bot ever opens a Lichess
  connection.
* **Self-test (`python lichess-bot.py --self-test`)** — runs in
  seconds without a Lichess token, so fork authors can verify
  their setup locally.
* **Oxydan Learn (`oxydan_learn.py`)** — experimental per-opening
  win/loss tracking. Off by default; flip
  `oxydan_learn.enabled: true` in `config.yml` to try it.

### 🏆 Notable Milestones
* **Chess960 Defeat against Calico1** — beat `calico1` (3008 ELO) in
  a Chess960 variant match, despite a 1000 ELO underdog differential.
* **Bullet Mastery** — out-calculated a 3218 ELO Bullet bot in
  sub-60-second time scrambles.

---

## 🛠️ Fork & Self-host Setup

The bot is designed to be forkable. To run your own instance:

1. **Fork this repository.**
2. **Generate a Lichess OAuth token** with the `bot:play` and
   `challenge:write` scopes from
   <https://lichess.org/account/oauth/token>. Add it as a GitHub
   Actions secret named `LICHESS_TOKEN` in your fork.
3. **Edit `config.yml`** — at minimum, set:
   * `engine.name` if your compiled binary isn't named `Ethereal`
   * `matchmaking.permanent_blacklist` to add bots you don't want
     to face
4. **(Optional) Tweak the new Oxydan-only blocks**:
   * `time_management` — the four time-allocation layers
   * `oxydan_chat` — toggle chat on/off, change message thresholds
   * `oxydan_learn` — enable the experimental opening learning
5. **Trigger the workflow** from the Actions tab. Every key in
   `config.yml` has a default, so the bot will start even if you
   don't change a thing.

### Local development

```bash
# Install dependencies
pip install -r requirements.txt

# Validate config + engine without connecting to Lichess
python lichess-bot.py --self-test

# Run for real (requires LICHESS_TOKEN in the environment)
export LICHESS_TOKEN=lip_xxx
python lichess-bot.py
```

### Where things live

```
.
├── lichess-bot.py        # main entry point, per-game handler, event loop
├── matchmaking.py        # Matchmaker (challenges) + tournament scanner
├── config.py             # single source of truth for all settings
├── oxydan_chat.py        # branded chat engine + retry + stats
├── oxydan_learn.py       # experimental opening learning
├── check_bot.py          # pre-flight diagnostics
├── config.yml            # user-editable knobs (Lichess-bot spec + Oxydan)
├── index.html            # public dashboard
├── ChangeLog.md          # version history
├── src/                  # Ethereal engine sources + NNUE weights
└── .github/workflows/    # CI/CD pipeline
```

---

## ⚙️ UCI & Configuration Reference

Oxydan/Ethereal supports standard UCI options via `config.yml`:

| Option | Recommended | Notes |
|---|---|---|
| `Hash` | `512` MB | Size of the transposition table. |
| `Threads` | `2` | More than 2 rarely helps on shared hosts. |
| `MoveOverhead` | `100` ms | Buffer for network latency. Increase if you see time losses. |
| `Ponder` | `true` | Think on opponent's time. |
| `SyzygyPath` | `./syzygy` | Local 7-piece tablebases. SSD recommended. |
| `SyzygyProbeDepth` | `1` | How deep the engine looks before consulting TBs. |

The full key list with explanations lives in `config.yml`.

---

## 🤖 Core Engine & Credits (Ethereal)

Oxydan's chess evaluations are powered by **Ethereal**, a UCI-compliant
chess engine operating under the alpha-beta framework paired with an
NNUE (Efficiently Updatable Neural Network) for positional
evaluations.

### Attribution & Acknowledgments
In compliance with the **GPLv3**, we give explicit attribution to
the original creator of Ethereal, **Andy Grant**, and the Computer
Chess Community:

* **Ethereal Project:** <http://chess.grantnet.us/Ethereal> ·
  [Chess Programming Wiki](https://www.chessprogramming.org/Ethereal)
* **Syzygy Tablebases:** [syzygy1/tb](https://github.com/syzygy1/tb) (GPLv2)
* **Fathom:** forked from [jdart1/Fathom](https://github.com/jdart1/Fathom)
  (MIT License) for Syzygy probing.
* **Windows support code:** originally written by Texel author
  Peter Österlund and Stockfish contributors.
* **Cerebellum opening book:** downloaded at build time from the
  project releases page; the file is over 1 GB of curated master
  games.

---

## 📜 GPLv3 License Note
This project is fully open-source under the **GPLv3**. You have the
right to access, modify, and redistribute this code, provided that
any derivative works carry the same GPLv3 license and acknowledge
the contributions of the open-source chess community listed above.
