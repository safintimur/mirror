# Mirror — Codex ↔ Claude Code chat sync for macOS

**Switch coding agents. Keep the conversation.**

Mirror is a free, open-source macOS menu bar app that syncs local conversations between OpenAI Codex and Anthropic Claude Code. Continue a Codex chat in Claude Code, or a Claude Code session in Codex, with messages and supported tool history.

[Build and install](#build-and-install) · [FAQ](#faq) · [Report a bug](https://github.com/safintimur/mirror/issues)

![Mirror app icon in different sizes](docs/branding/previews/icon-sizes.png)

> **Experimental beta · macOS 26+**
>
> Mirror writes to the local conversation stores used by both apps. Their formats can change between updates. Back up your chats before your first sync. Mirror is an independent project and is not affiliated with OpenAI or Anthropic.

## What it does

- Creates a conversation in the other app and transfers completed turns in both directions.
- Carries over messages and supported command, file-change and tool-call records.
- Shows queued work, active conversations and sync errors from the menu bar.
- Supports manual synchronization and optional automatic synchronization.
- Queues work while a conversation is locked; the restart flow can close an app, sync, and reopen it.
- Mirrors manual titles and archive/unarchive changes. Deletion is represented by archiving on the other side.

Changes may need an app restart before they appear in its sidebar. Forks and sessions continued under a new ID are separate conversations.

## Continue a conversation in another app

1. Finish the current turn in Codex or Claude Code.
2. Open Mirror from the menu bar, review the queue, and sync. If an app holds a conversation lock, use Mirror's restart-and-sync flow; it warns about active sessions before closing anything.
3. Open the mirrored conversation in the other app and continue there. Mirror transfers conversation history; each provider still uses its own account and usage limits.

Manual sync works with automatic sync disabled. Enable automatic synchronization in Settings if you prefer background updates.

## Requirements

- macOS 26 or later; Codex and Claude Code installed and used at least once.
- Python 3.9+ available at `/usr/bin/python3` for the menu bar app. Python is not bundled yet.
- Xcode 26+ with Swift 6.2+ to build the native app and Icon Composer assets.
- Disable Codex's built-in Claude import before using Mirror to avoid duplicate conversations.

The engine finds Codex in the supported app bundle locations, then on `PATH`. Set `CODEX_BIN` to an executable path when using a different installation. Internal storage and app-server compatibility must be checked again after provider updates.

## Build and install

```sh
./app/build.sh
```

The result is `app/build/Mirror.app`. Copy it to Applications and open it. New installations start with automatic sync disabled; review the queue and run the first sync manually. Settings control automatic sync and idle restarts independently.

Build a drag-to-install disk image:

```sh
./app/make_dmg.sh
```

The result is `app/build/Mirror-<version>.dmg`. This script briefly opens a Finder window to arrange the installer.

![Mirror installer](docs/branding/previews/installer.png)

Local builds are **ad-hoc signed and not notarized**. macOS may block a downloaded copy; review the source and publisher before allowing it in Privacy & Security. No hosted binary release has been published yet.

## Local data

The synchronization engine has no remote service or telemetry. It reads local files and uses the installed Codex app-server; Codex and Claude retain their own network behavior.

| Location | Purpose |
| --- | --- |
| `~/.codex` | Codex conversations, database and writer locks |
| `~/.claude` | Claude Code conversations and session metadata |
| `~/Library/Application Support/Claude` | Claude desktop's local session registry |
| `~/.local/share/codex-claude-mirror` | Mirror's pairing state and incremental index |

Do not remove pairing state as routine cleanup: it records what has already been transferred. Existing data paths and the bundle identifier retain their original names for compatibility.

## FAQ

### Can I sync Codex chats with Claude Code in both directions?

Yes. Mirror creates a corresponding local conversation and transfers completed turns in both directions. It also mirrors manual titles and archive/unarchive changes; deletion becomes an archive on the other side. Supported command, file-change and tool-call records accompany the messages, but not every provider-specific record has an equivalent.

### Why is a chat waiting, or missing from the other app's sidebar?

An open session can hold a writer lock, and desktop apps can cache their conversation lists. Mirror queues blocked work. Its restart-and-sync flow closes the relevant app, applies queued changes, and reopens it. Provider updates can change local formats; see the [engine details](docs/ENGINE.ru.md) for compatibility limits.

### Does Mirror upload my conversations?

Mirror has no remote synchronization service or telemetry. It reads local conversation stores and uses the installed Codex app-server. Codex and Claude retain their own network behavior. See [local data](#local-data) for the directories Mirror accesses.

### Does this sync regular Claude chats or cloud-only Codex tasks?

Mirror works with local Codex and Claude Code sessions, including Claude desktop's Code session registry. It does not sync ordinary Claude web chats or Codex tasks that exist only in the cloud.

## CLI and development

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
- [Engine details in Russian](docs/ENGINE.ru.md), [interface design](app/DESIGN.md), [branding](docs/branding/README.md).
- [Release process](docs/RELEASING.md).

When reporting a bug, include app versions and the error message. Remove prompts, tool output, local paths and credentials from any logs you share. Do not attach your chat databases or transcripts.

## License

[MIT](LICENSE), copyright © 2026 Timur Safin.
