## [11.0] — June 3, 2026

### Added
* **NNUE Evaluation Support**: Enabled advanced neural network evaluation via `-DUSE_NNUE=1` compilation for sharper tactical play.
* **Pre-Flight Diagnostics**: Integrated `check_bot.py` to automatically clear zombie processes and validate core module readiness before launch.
* **Live Dashboard Panel**: Created a mobile-responsive web interface (`index.html`) using the Lichess API for real-time rating and live match telemetry tracking.
* **Branded Chat Engine**: Implemented dynamic conversational pools (`greeting_human`, `greeting_bot`, `losing_realization`) featuring specialized developer branding for Emir Karadağ.

### Changed
* **Core Architecture Overhaul**: Upgraded and refactored the primary execution framework class from `OxydanAegisV4` to `OxydanV11` to ensure codebase uniformity.
* **Smart Time Allocation**: Optimized `LATENCY_BUFFER` to `0.07` seconds and refined calculation divisors based on move-stack depth and position tension.
* **Dynamic Parameter Syncing**: Bound `config.yml` fields directly to runtime environments to automatically adjust `max_games` and engine `MoveOverhead` values.

### Fixed
* **Missing Function Crash**: Fixed a critical runtime vulnerability by fully implementing the missing `get_score` telemetry routine inside the engine pool.
