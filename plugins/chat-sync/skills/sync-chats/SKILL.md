---
name: sync-chats
description: Sync local Codex conversations across desktop profiles using a user-selected private folder, check status, or preserve a divergent chat as a branch.
---

For a status request call chat_sync_status. When setting up, use the exact existing private folder chosen by the user. The folder receives Codex transcripts including prompts, replies, tool input and output. Confirm the location and whether automatic exchange is wanted if either is unspecified. A Nextcloud, OneDrive, Google Drive or NAS folder works if files are available locally on each computer.

Call configure_chat_sync for setup, then sync_chats when the user wants to exchange chats. Report the imported, updated and deferred counts and any conflicts. A busy thread can be read again after Codex releases its writer lock. With automatic mode enabled, the plugin retries. For an independently edited chat, report the device IDs, using the device field in chat_sync_status to identify this desktop. To reunite the original, confirm which other device’s history the user wants for this chat, then use adopt_chat_history on the desktop that should adopt it. Its previous local history is preserved as a separate branch. If the user only wants a copy of one history, use keep_conflict_branch. Run sync_chats again so the branch reaches the other desktops. Call disable_chat_sync to disconnect this desktop.
