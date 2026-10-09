# Developing Mirror

For installation and everyday use, see the [README](../README.md).

The engine finds Codex in the supported app bundle locations, then on `PATH`. Set `CODEX_BIN` to an executable path when using a different installation. Internal storage and app-server compatibility must be checked again after provider updates.

## Local data

The synchronization engine has no remote service or telemetry. It reads local files and uses the installed Codex app-server; Codex and Claude retain their own network behavior.

| Location | Purpose |
| --- | --- |
| `~/.codex` | Codex conversations, database and writer locks |
| `~/.claude` | Claude Code conversations and session metadata |
| `~/Library/Application Support/Claude` | Claude desktop's local session registry |
| `~/.local/share/codex-claude-mirror` | Mirror's pairing state and incremental index |

Do not remove pairing state as routine cleanup: it records what has already been transferred. Existing data paths and the bundle identifier retain their original names for compatibility.

## CLI and tests

Show commands with `python3 codex_mirror.py`. For example:

```sh
python3 codex_mirror.py status --days 7
python3 codex_mirror.py sync --days 7 --manual
```

`sync` changes chat files. Use `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `MIRROR_STATE_DIR` to select isolated data directories. `MIRROR_CLAUDE_DESKTOP` optionally selects a separate desktop registry; overriding `CLAUDE_CONFIG_DIR` disables access to the real desktop registry by default.

Run the parity suite in its sandbox:

```sh
python3 tests/test_parity.py
```

It creates synthetic conversations and launches an isolated Codex app-server. No model generation is requested. By default it uses checked-in synthetic session metadata; it does not need your chat history. Set `MIRROR_E2E_ROOT` to keep results in a chosen directory, or `MIRROR_E2E_TEMPLATE` to explicitly supply a compatible local session metadata record for diagnostics.

- `codex_mirror.py` — synchronization engine (Python standard library).
- `app/Sources/MirrorBar` — SwiftUI/AppKit app.
- `app/Resources` — icon and installer assets; `app/tools` — asset tooling.
- `tests` — sandbox parity tests and synthetic fixtures.
- [Engine details in Russian](ENGINE.ru.md), [interface design](../app/DESIGN.md), [branding](branding/README.md).
- [Release process](RELEASING.md).

## Installer

Build a drag-to-install disk image:

```sh
./app/make_dmg.sh
```

The result is `app/build/Mirror-<version>.dmg`. This script briefly opens a Finder window to arrange the installer.

![Mirror installer](branding/previews/installer.png)

