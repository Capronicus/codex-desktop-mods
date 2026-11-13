---
name: sync-chats
description: Sync local Codex conversations across desktop profiles using a user-selected private folder, check status, or preserve a divergent chat as a branch.
---

For “Open Mod Controls”, call open_mod_controls. Use set_sync_paused when the user asks to pause or resume transfers. The panel shows last-attempt and receipt information and controls this computer’s visual settings. Fully reopen the patched desktop app to apply appearance switches.

For “which chats would this desktop share?”, use preview_chat_sharing on the configured desktop. Search by title or UUID, or page through the results. It works while paused. Describe “needs checking” entries as candidates whose source or recovery state sync still has to verify. In Mod Controls, **Preview outgoing chats** also lets the user change individual local-only rules.

In Mod Controls, **Sidebar changes** lets the user preview incoming changes, apply the displayed batch or undo the latest batch locally. Undo also works while transfers are paused. If the panel reports queued changes, fully quit and reopen the desktop to see the result.

For a stuck transfer, use check_sync_folder to check the current shared revisions. When asked to repair, use repair_sync_folder on a connected, unpaused desktop with a local copy. Report blocks restored and what still needs another desktop or a folder download.

For a status request call chat_sync_status. When setting up, use the exact existing private folder chosen by the user. The folder receives Codex transcripts including prompts, replies, tool input and output. Confirm the location and whether automatic exchange is wanted if either is unspecified. A Nextcloud, OneDrive, Google Drive or NAS folder works if files are available locally on each computer.

Call configure_chat_sync for setup, then sync_chats when the user wants to exchange chats. Report the imported, updated and deferred counts and any conflicts. A busy thread can be read again after Codex releases its writer lock. With automatic mode enabled, the plugin retries. For an independently edited chat, report the device IDs, using the device field in chat_sync_status to identify this desktop. To reunite the original, confirm which other device’s history the user wants for this chat, then use adopt_chat_history on the desktop that should adopt it. Its previous local history is preserved as a separate branch. If the user only wants a copy of one history, use keep_conflict_branch. Run sync_chats again so the branch reaches the other desktops. Call disable_chat_sync to disconnect this desktop.

For colour exchange, both desktops need the applied Desktop Mods 0.6.0 or later. When the user requests it, call set_colour_sync with enabled true or false on the configured device. When enabling, call sync_colours_now to exchange colour preferences immediately. Report the active preference and saved swatch counts. Check the colours flag on each desktop. Regular chat sync also exchanges opted-in colours. Report any deferred colour files or colour error separately.

For project and custom section sync, use set_sidebar_sync on a configured desktop when the user asks to enable or disable it there. Names and local chat membership go to the selected private vault. Both peers opt in separately. Report the sidebar field from chat_sync_status and the sidebar result from sync_chats, including pending chats or errors. When desktop_refresh_on_quit is true, ask the user to fully quit and reopen the app to see the queued layout. When sections_need_sign_in is true, projects can sync and sections await ChatGPT sign-in. Receiving projects have no folder attached until the user selects a local checkout.

Use list_sync_projects to identify both IDs before link_sync_project. Ask which existing local checkout to use if it is unknown. Linking is explicit. Equal project names alone do not choose a pair. Run sync on each participating device. The folder path belongs to the receiving device.

For “did my other PC receive this chat?”, call chat_delivery_status with the local thread UUID. A receipt identifies the last verified revision. “Saved” means the revision reached the local sync folder. The receiving desktop must run sync and the folder service must bring its receipt back. Use set_sync_device_label for a name and origin emoji. Pass an empty emoji when the user asks to clear it. Use name_sync_device for a user-chosen label such as Home or Work. Report the origin from chat_delivery_status when present, keeping “started” distinct from “first seen”. Desktop Mods 0.7.0 shows the same status on sidebar hover.

For “keep this chat/project on this device”, use set_local_only with kind, UUID and enabled. Report previously_shared and the effect on future passes. Earlier copies remain available wherever they were saved. Enable sharing again only when requested.

When sync_chats returns preview_required, show the counts and affected groups. Use preview_sidebar_sync for a fresh read-only preview. Apply a user's chosen batch via apply_sidebar_preview with its exact ID. A stale preview needs a fresh review. Use undo_sidebar_sync for a requested rollback of the most recent received sidebar batch on this device. It preserves newer edits, retains chat history and pauses sidebar and order sync locally. Report queued work on quit. Pins and relative project, section and pinned-chat order have their own opt-in via set_pin_order_sync on each participating desktop.

When the user asks to sync appearance and chat preferences, call set_settings_sync on the configured device, then sync_settings_now. Report the active and applied counts and any skipped settings or deferred peers. Enable first on the source desktop, then on the receiver after its folder client has downloaded the settings. Fully reopen Codex if the open window still shows old preferences. Model defaults and permissions are outside this option.

If a manual chat sync returns an `activation_offer`, show its destination and offer **Count this installation** or **Skip**. Counting sends this plugin’s name and a random receipt to that address. Call `report_activation` with the displayed destination only if the user chooses to count. Skip or silence ends the offer locally. For a later explicit request to count, read `activation_status` to identify the destination first.

For project or folder exclusions, use Mod Controls → Chat actions and sharing. Use `set_local_only` for this PC or `set_shared_exclusion` when the user chooses all connected PCs. Shared folder rules use a project-relative path. Existing copies remain. Right-click a local reply for file lookup or device handoff. The panel also compares two conflicting snapshots without changing either one.

In version 0.15.0, open **Mod Controls → Organize projects and sections** to create a subsection under a project or custom section (up to three levels including the root), change its parent or convert between a custom section and a real local project. Apply queued changes by fully quitting and reopening the app. A converted project remembers its local folder paths.

Use **Add to batch** in the organizer to preview a series of creations and moves before applying it on quit. Enable **Subsection tree** separately on both desktops to share nesting. File handoffs can include a relative path and fingerprint for comparison. Right-click a message or selection to bookmark it. Open bookmarks from the pet. Saved views in the sidebar filter local chats by project, origin, age and outstanding receipt. Bookmarks and saved views are local.
