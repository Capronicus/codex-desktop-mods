# Codex Chat Sync

This local desktop plugin exchanges Codex chat histories through a folder on each computer. Nextcloud, OneDrive and Google Drive can provide the transport through their desktop clients. Choose the same **private, available-offline** folder on each device. It contains prompts, replies, tool calls and saved tool output in readable, compressed form.

## Try it

1. On each desktop install Python 3.10+ on PATH as `python3` (3.14+ includes Zstandard). For the Windows Store alias issue, follow the [one-time Windows Python setup](../../README.md#install-from-plugin-explorer). In Plugin Explorer, add the GitHub marketplace `https://github.com/Capronicus/codex-desktop-mods` or refresh the existing **Capronicus Desktop Mods** source. Select **Codex Chat Sync** and install it. The CLI equivalent for an existing marketplace is `codex plugin marketplace upgrade capronicus-desktop-mods`, then `codex plugin add chat-sync@capronicus-desktop-mods`.
2. Open a new desktop chat, select **Codex Chat Sync** and ask “Check chat sync status.” A fresh install starts disconnected.
3. When ready, ask “Set up automatic chat sync in [absolute path to my private folder].” Run a sync on the first desktop, let the cloud client finish transferring its files, then sync on the second. Each machine can use a different local path to the same shared folder. Automatic exchange runs about once a minute while the plugin server is running. A manual pass is available with “Sync my chats now.”
4. Reopen a chat after the receiving device has synced. Codex can hold an open chat’s writer lock until you close the app. If status reports a busy chat, close it or quit Codex and run the command-line sync. A copied chat keeps its original ID.

The installed plugin folder contains `sync.py`. Run `python3 /path/to/sync.py status`, `sync`, `setup "/path/to/folder" --automatic`, `watch` or `disable`. The standalone watcher can run while the desktop is closed. Disabled sync leaves existing local and shared copies in place. To remove the plugin, run `codex plugin remove chat-sync@capronicus-desktop-mods`.

## Mod Controls

Choose **Chat sync → Preview outgoing chats** to see which local histories are new or changed, unchanged, excluded, unavailable or awaiting a source or recovery check. Search by title or chat ID. **Keep local** excludes a chat from future transfers, and **Remove chat rule** clears its individual exclusion. Project rules still apply. Previewing works while paused and reads file metadata without opening transcripts or writing to the shared folder. **Resume sync** is a separate action. The CLI equivalent is `python3 sync.py preview-sharing`, with optional `--search`, `--group` and zero-based `--page`.

With **Projects and sections** enabled, open **Mod Controls → Sidebar changes → Preview changes**. Review the affected projects, sections, chats and pins, then choose **Apply reviewed changes**. If another desktop changes the batch, preview again. After the first approval, later syncs can apply incoming sidebar changes automatically. **Undo last batch** restores the previous sidebar on this desktop and turns off sidebar and order sync here. The panel shows when changes will finish on quit.

In **Device name and emoji**, choose a name and an optional emoji for this desktop. The CLI equivalent is `python3 sync.py name Work --emoji "💼"`. Clear the emoji to hide the origin label. After both desktops sync, Desktop Mods 0.10.0 shows it beside the chat title and above the composer. **Started on** identifies a recorded origin. **First seen on** identifies the first desktop to share an older chat. Ambiguous origins get no badge. Local file paths still refer to their original computer.

**Mod Controls** opens from the desktop sidebar or by asking Chat Sync to open it. It shows the private folder, last attempt, last full success and delivery receipts. **Pause sync** suspends automatic and manual transfers on this computer. **Resume sync** restores the previous schedule. The CLI also accepts `pause` and `resume`. Disconnecting retains local-only rules for future connections and restores the same device identity when you reconnect the same folder.

**Sync folder health** verifies the blocks used by current device revisions. **Check folder** saves the result on this computer and works while sync is paused. **Repair from this desktop** restores matching blocks from eligible local histories, including compressed archives. The CLI equivalents are `python3 sync.py check-folder` and `python3 sync.py repair-folder`. If blocks are still missing, run repair on another desktop that has the chat and wait for your folder client to transfer them. Missing revision metadata and unreadable files are reported separately.

Before exporting changed history, Chat Sync stages the transcript in this device’s profile and checks the file and writer state. When a chat is deferred as busy or changes during the read, its staged bytes stay off the shared folder.

The panel can also toggle wider chats, the pet dock, scroll animation, timestamps, token counts and branch lines. These switches are local to this computer. Reopen the desktop app with Desktop Mods 0.9.0 or later applied to see changes.

## Chat actions on Home and Work

Use version 0.15.0 of both plugins on each participating PC. The desktop menus require the patched launcher. Open **Mod Controls → Chat actions and sharing** for these actions.

The handoff form accepts an optional project file. At the destination, **Compare** checks the relative path in the receiver’s linked checkout against the sender’s fingerprint. Both PCs must enable **Projects and sections → Subsection tree** to exchange the three-level parent relationships. **Organize projects and sections** supports staging multiple creations, moves and chat assignments before quitting once. Message bookmarks in Mod Controls and saved sidebar views belong to the local account on this desktop.

- **Open local copy**. Link the receiving project to its checkout. Sync the original desktop with sidebar sync or project linking enabled, then let your folder client finish. Right-click a file reference in a local reply. Preview the file under the receiving checkout, open text files in an editor, or show the folder. If the original project root is unknown, enter the project-relative path. The plugin shares hashes of eligible project roots to match Windows and Linux paths. Files stay on their own computers.
- **Hand off this reply**. After both desktops have synced once, right-click the reply on Work, select Home, add an optional note and click **Send handoff**. On Home open the pet’s **Reply handoffs** menu or the panel’s inbox. **Read reply** shows the saved message, **Open in chat** navigates to it and **Done** removes it from this PC’s inbox. If history arrives later, the handoff remains available for retry. The note and message identifiers travel through the configured private folder.
- **Chat branches**. Right-click a local sidebar chat and choose **Chat branches**, then **Show parent and branches**. Choose a parent, sibling or child to open it across sidebar sections. Archived histories participate when present on the device.
- **Compare conflicting chats**. Select two device snapshots to see the last common visible message and the first twelve later messages on each side. The comparison is read-only. Use the existing branch or adopt actions separately to resolve a conflict.
- **Check responsiveness**. Start the check, scroll the sidebar and transcript, and switch to another local chat during the twelve-second sample. Refresh the panel to see frame counts, the 95th percentile frame interval and the last measured switch. The measurements stay on this PC.
- **Project and folder exclusions**. Choose **This PC** or **All connected PCs**. A device-only folder rule includes nested working directories. An all-PC folder rule uses its path relative to a project checkout. Shared rules take effect as updated desktops receive them. If paused, a change takes effect here immediately and is published when sync resumes. Removing one scope’s rule keeps any other matching rules in effect. Earlier copies remain wherever they were shared.

## What travels

The first sync takes the union of eligible local Codex chats. It includes chat messages, stored tool results, natively recorded fork ancestry and display names. Conversations found in native Zstandard storage require Python 3.14+ or the `zstd` executable. Imported archived chats start archived. Later archive and delete actions apply to the device where you take them.

If one history continues the other, the receiver checks the complete snapshot and updates its local copy after Codex releases the thread. The plugin recognises the settings record Codex adds just by reopening a chat. If both desktops add independent turns, status reports a conflict with the two device IDs. To reunite the original chat, ask the desktop whose copy you want to replace to adopt the other device’s version. It saves its current conversation as a separate branch first. On the next sync both desktops get the same original chat and the preserved branch. Repeat on any additional desktop that made independent edits. You can also copy a version to a branch without replacing the original. The CLI equivalents are `adopt THREAD_ID DEVICE_ID` and `keep-branch THREAD_ID DEVICE_ID`.

Working directories, credentials, external attachments, repository files and regular cloud ChatGPT conversations are outside this plugin’s scope. When a saved working directory is unavailable on the receiving computer, the chat opens in a dedicated local folder. Select your local project checkout before asking Codex to edit its files. Sync preserves the original workspace metadata in the shared history.

## Sync projects and custom sections

After configuring the private chat folder, ask **Enable sidebar sync** on each desktop, then **Sync my chats now**. From the CLI use `python3 sync.py sidebar on` or `sidebar off`. This setting starts off and is independent of colour sync.

The next pass exchanges local Codex project and custom section names, including empty groups. A synced chat can belong to both a project and a section. Subsequent renames, chat moves and removals from a group also travel. Independent groups with identical names keep their own identities. Local group deletion stays local. Cloud-only ChatGPT items and project files stay on their respective desktops. Pins and ordering have a separate opt-in.

An imported project starts with no folder attached unless you link it to an existing checkout. Select its local checkout on each computer when needed. When the sync result shows `desktop_refresh_on_quit`, fully quit the desktop app and reopen it. A local helper applies the queued sidebar changes after the app exits. If the computer shut down first, the next sync queues the layout again. You can also quit the desktop app and run `python3 sync.py sync` from the plugin directory (`py sync.py sync` on Windows). Custom sections appear under the signed-in ChatGPT account. A chat that arrives before its complete history is available gets its assignments on a subsequent pass.

## Link an existing project

On the desktop that already has a checkout, ask **“Link my local TARTARUS to the shared TARTARUS and use this folder.”** Give the folder path on that computer. For the cleanest first merge, sync the source, wait for the shared project to arrive, then link before applying the receiving sidebar preview. The same chat history can resume in different local checkouts on Windows and Linux. Links use explicit IDs. A rootless imported duplicate can be consolidated, and an independently configured project with its own files is preserved. The shared link record contains project IDs. Checkout paths stay in device configuration.

## Delivery and sharing

- Ask **“Name this sync device Windows PC”** on the receiver. On the sender, ask **“Has Windows PC received this chat?”** A receipt refers to the last completed chat revision that the receiving plugin verified. The next local sync reads receipts delivered by your folder provider. A new reply needs a new receipt. Name your devices **Home** and **Work** to see **Started on Home** or **Started on Work** on new chats. For older chats the plugin can report the first desktop that observed an existing chat. When its origin is unknown, the origin label stays blank.
- With **Codex Desktop Mods 0.7.0** applied, hover over a local sidebar chat title for its delivery status. A check mark means a peer acknowledged the last synced revision. A clock or exclamation mark flags a busy chat or a conflict.
- Ask **“Keep this chat on this device”** or **“Keep the CAPRICA project local only.”** The rule covers future transfers on this device and reports whether data was already saved to the shared folder. Earlier copies remain on peers and in the vault. You can enable sharing again later.

## Preview, undo, pins and order

The first incoming desktop sidebar merge shows the proposed changes before applying them. Ask **“Preview sidebar sync”**, then **“Apply that preview”**. Chat histories can arrive while sidebar changes await review. Later sync passes apply automatically. Ask **“Undo the last sidebar sync”** to roll back the latest received batch on this computer. Undo leaves the chats available and pauses sidebar and order sync. A newer local edit can prevent the rollback. Fully quit the desktop app when a change is queued for exit.

Ask **“Sync my pins and sidebar order”** on each participating desktop after enabling sidebar sync. This shares local chat pins and relative project, section and pinned-chat order. Other devices can keep their own layout.

The equivalent commands in the plugin directory are `python3 sync.py projects`, `link LOCAL_ID SHARED_ID FOLDER`, `name "Windows PC"`, `receipt CHAT_ID`, `local-only chat|project ID on|off`, `preview`, `apply-preview BATCH_ID`, `undo` and `order on|off`. Use `py sync.py` on Windows.

## Sync appearance and chat preferences

With Chat Sync 0.5.0 or later, configure the same private folder on both desktops. On the desktop whose settings you want to start with, ask **“Enable settings sync and sync my settings now.”** Wait for your folder service to deliver the settings file, then repeat on the other desktop. Existing shared settings apply on first join. A preference set only on the joining desktop is added to the folder. Fully quit and reopen Codex if an open window still shows its previous settings.

Supported appearance settings include light/dark mode, custom light and dark colours and font choices, code themes, UI/code/Page font sizes, pointer and font smoothing preferences, and reduced motion. Chat preferences include Enter to send, plain-text composition, follow-up handling, chat detail level, inline or detached reviews, context usage display, project chats in Recents, completion notifications and notification dots. A font must be installed on a computer to render there. Model selection, reasoning, permissions, credentials and device paths stay local.

Automatic chat sync includes these preferences when enabled. For a settings-only pass use `python3 sync.py sync-settings`; toggle the feature with `python3 sync.py settings on` or `settings off`. Settings sync starts off on each desktop. Edits merge per preference, including resets to defaults. Concurrent edits to one preference use its revision and the device ID to break ties.

## Sync sidebar colours

Apply **Codex Desktop Mods 0.7.0** and install **Chat Sync 0.4.0** or later on each computer. After configuring the same private folder, ask **Enable colour sync and sync my colours now** on each device. This exchanges colour preferences immediately, including existing saved swatches. From the CLI, use `python3 sync.py colours on` and `python3 sync.py sync-colours`. Use `colours off` to disable it. Colour sharing starts off on existing and new installations.

Chat Sync 0.9.0 sends custom section colours, overrides and memberships in a separate file for the signed-in account. Update Chat Sync and Desktop Mods on both computers for section colours. Existing section colours migrate when the desktop can identify their owner. If two accounts used the same section ID, choose its colour once in each account. Older copies already transferred by a folder provider may remain in its history.

The same sync pass carries individual, project and section colours, saved swatches, group overrides and resets. Matching IDs and explicitly linked local projects share settings across desktops. A chat can inherit its original project or section colour through its ID even when it appears only in Recents on the other desktop. Independently created groups with the same name keep separate IDs. The receiving mod refreshes within five seconds while visible. Concurrent edits to different swatches or chats coexist. Simultaneous edits to the same setting use a revision and device ID to select a consistent value.

## Storage and verification

The plugin creates `codex-chat-sync-v1` inside your chosen folder. Each desktop publishes its own head files. Snapshots refer to immutable checksummed blocks. If a cloud client delivers a head before its data, the receiver retries. Each local replacement uses a temporary file and the native writer lock. When an adopted history differs from the existing timeline, the plugin clears that chat’s derived timeline index so Codex can reconstruct it. A local journal allows an interrupted index refresh to retry. The shared folder retains previous snapshots and grows as you use it. Keep the provider’s desktop client running on each device.

The [cross-platform workflow](../../.github/workflows/chat-sync.yml) installs the plugin in disposable profiles and sends a synthetic chat from Linux to the Windows Store app server and back. It also sends a separate Windows chat to Linux and checks the original chat ID, saved title, both replies and busy-chat deferral. GitHub artifacts carry the test folder. The test does not use a live Nextcloud, OneDrive or Google Drive account.

To repeat the tests with disposable profiles, run `python3 tests/roundtrip.py` (Codex CLI and Node required) and `python3 tests/storage.py` (zstd required).
