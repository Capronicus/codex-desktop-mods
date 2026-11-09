# Codex Chat Sync

This local desktop plugin exchanges Codex chat histories through a folder on each computer. Nextcloud, OneDrive and Google Drive can provide the transport through their desktop clients. Choose the same **private, available-offline** folder on each device. It contains prompts, replies, tool calls and saved tool output in readable, compressed form.

## Try it

1. On each desktop install Python 3.10+ on PATH as `python3` (3.14+ includes Zstandard). In Plugin Explorer, add the GitHub marketplace `https://github.com/Capronicus/codex-desktop-mods` or refresh the existing **Capronicus Desktop Mods** source. Select **Codex Chat Sync** and install it. The CLI equivalent for an existing marketplace is `codex plugin marketplace upgrade capronicus-desktop-mods`, then `codex plugin add chat-sync@capronicus-desktop-mods`.
2. Open a new desktop chat, select **Codex Chat Sync** and ask “Check chat sync status.” A fresh install starts disconnected.
3. When ready, ask “Set up automatic chat sync in [absolute path to my private folder].” Run a sync on the first desktop, let the cloud client finish transferring its files, then sync on the second. Each machine can use a different local path to the same shared folder. Automatic exchange runs about once a minute while the plugin server is running. A manual pass is available with “Sync my chats now.”
4. Reopen a chat after the receiving device has synced. Codex can hold an open chat’s writer lock until you close the app. If status reports a busy chat, close it or quit Codex and run the command-line sync. A copied chat keeps its original ID.

The installed plugin folder contains `sync.py`. Run `python3 /path/to/sync.py status`, `sync`, `setup "/path/to/folder" --automatic`, `watch` or `disable`. The standalone watcher can run while the desktop is closed. Disabled sync leaves existing local and shared copies in place. To remove the plugin, run `codex plugin remove chat-sync@capronicus-desktop-mods`.

## What travels

The first sync takes the union of eligible local Codex chats. It includes chat messages, stored tool results, natively recorded fork ancestry and display names. Conversations found in native Zstandard storage require Python 3.14+ or the `zstd` executable. Imported archived chats start archived. Later archive and delete actions apply to the device where you take them.

If one history continues the other, the receiver checks the complete snapshot and updates its local copy after Codex releases the thread. The plugin recognises the settings record Codex adds just by reopening a chat. If both desktops add independent turns, status reports a conflict with the two device IDs. To reunite the original chat, ask the desktop whose copy you want to replace to adopt the other device’s version. It saves its current conversation as a separate branch first. On the next sync both desktops get the same original chat and the preserved branch. Repeat on any additional desktop that made independent edits. You can also copy a version to a branch without replacing the original. The CLI equivalents are `adopt THREAD_ID DEVICE_ID` and `keep-branch THREAD_ID DEVICE_ID`.

Projects, sidebar sections and colours, working directories, credentials, external attachments, repository files and regular cloud ChatGPT conversations are outside this plugin’s scope. The original working-directory path is part of the transcript. A different computer may need its own checkout or a local working directory before using tools.

## Storage and verification

The plugin creates `codex-chat-sync-v1` inside your chosen folder. Each desktop publishes its own head files. Snapshots refer to immutable checksummed blocks. If a cloud client delivers a head before its data, the receiver retries. Each local replacement uses a temporary file and the native writer lock. When an adopted history differs from the existing timeline, the plugin clears that chat’s derived timeline index so Codex can reconstruct it. A local journal allows an interrupted index refresh to retry. The shared folder retains previous snapshots and grows as you use it. Keep the provider’s desktop client running on each device.

The round trip, reply continuation, busy-chat deferral, branch preservation, rename propagation and plugin installation were exercised on isolated Linux profiles. Live provider services and Windows/macOS desktop builds have not been tested.

To repeat the tests with disposable profiles, run `python3 tests/roundtrip.py` (Codex CLI and Node required) and `python3 tests/storage.py` (zstd required).
