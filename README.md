# Oxydan Chess Bot (v11.0)

Oxydan is an advanced Lichess bot built upon the open-source Ethereal chess engine, fine-tuned and optimized for stable deployment, tournament matchmaking, and fast time management.

* **Lichess Profile:** [@OxydanBot](https://lichess.org/@/OxydanBot)
* **Estimated Strength:** ~2800 ELO (Blitz / Bullet)
* **License:** GNU General Public License v3.0 (GPLv3)

---

## 🚀 Key Features & Custom Implementations

While the core chess brain leverages Ethereal's powerful neural network architecture, this repository introduces custom modules and scripts to fully adapt the engine into a production-ready Lichess bot:

* **Automated Tournament Management (`matchmaking.py`):** Features real-time team arena fetching and automated tournament registration to keep the bot constantly active.
* **Refactored Time Management (`lichess-bot.py`):** Fine-tuned the engine's search constraints and move overhead to maximize performance and prevent flag-losses under extreme Blitz/Bullet time scrambles.
* **CI/CD Automation (`.github/workflows`):** Integrated GitHub Actions workflows to ensure system telemetry, stability checks, and seamless deployment tracking.
* **System Diagnostics (`check_bot.py`):** Implemented automated fallback mechanisms and real-time status monitoring for the bot's runtime.

### 🏆 Notable Milestones
* **Chess960 Defeat against Calico1:** Successfully defeated `calico1` (3008 ELO) in a Chess960 variant match, overcoming a 1000 ELO underdog differential.
* **Bullet Mastery:** Successfully out-calculated and defeated a 3218 ELO Bullet bot in speed conditions.

---

## 🤖 Core Engine & Credits (Ethereal)

Oxydan's chess evaluations are powered by **Ethereal**, a UCI-compliant chess engine operating under the alpha-beta framework paired with an NNUE (Efficiently Updatable Neural Network) for positional evaluations. 

### Attribution & Acknowledgments
In compliance with the **GPLv3**, we give explicit attribution to the original creator of Ethereal, **Andy Grant**, and the Computer Chess Community:
* **Ethereal Project:** Orijinal engine source code can be referenced at [the official webpage](http://chess.grantnet.us/Ethereal) and [Chess Programming Wiki](https://www.chessprogramming.org/Ethereal).
* **Syzygy Tablebases:** Uses universally adopted [Syzygy Tablebases](https://github.com/syzygy1/tb) (GPLv2).
* **Fathom:** Uses a forked version of [Fathom](https://github.com/jdart1/Fathom) (MIT License) to implement Syzygy.
* **Windows Support Code:** Includes refined Windows OS logic originally written by Texel author Peter Österlund and Stockfish contributors.

---

## ⚙️ Configuration & UCI Options

Oxydan/Ethereal supports standard UCI options via `config.yml`. Recommended configurations for optimal bot performance:

* **Hash:** Size of the hash table in megabytes. Recommended: `64MB` per thread/minute.
* **Threads:** Sets the number of threads given to the engine. Maximum available hardware threads recommended.
* **MoveOverhead:** Time buffer (in milliseconds) to prevent losing on time due to network lag. Increase this value if you notice time losses on Lichess.
* **SyzygyPath / SyzygyProbeDepth:** Path to Syzygy table bases. An SSD path is strongly recommended. Default probe depth is 0, but can be set to 6 or 8 on slower storage.

---

## 📜 GPLv3 License Note
This project is fully open-source under the **GPLv3**. You have the right to access, modify, and redistribute this code, provided that any derivative works carry on the same GPLv3 license and acknowledge the immense contributions of the open-source chess community.
