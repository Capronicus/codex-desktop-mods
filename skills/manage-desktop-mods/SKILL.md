---
name: manage-desktop-mods
description: Use when the user asks to check, apply, update or remove the Codex Desktop Mods Windows or Linux patch, or use its sidebar colours, timestamps and token counts.
---

Use the plugin's local tools on the Windows or Linux machine running ChatGPT. For setup, a status question or troubleshooting, call `desktop_mods_status`. Report the desktop build, menu launcher and any action the result calls for. Report a missing installation or incompatible build as such.

On a request to apply, repair or update the patch, call `apply_desktop_mods`. It bundles the original mod, uses its compatibility checks and works with an existing manual installation. After a successful change, ask the user to fully quit ChatGPT. On Windows, reopen **ChatGPT (Desktop Mods)** from Start. On Linux, reopen **ChatGPT** from the applications menu. The original Windows shortcut opens the Store app. Allow the user's running chat to finish.

On a request to remove the patch or return to stock, call `remove_desktop_mods`. If it reports deferred cleanup, the next menu launch uses the stock app. After reopening, the user can ask to finish removing the mods. Removing the manager itself is a separate Codex plugin action. To remove both, remove the patch first.

For colours, right-click a custom section heading, project or chat and select its colour action. Shift + right-click opens the picker directly. Use **Override all chat colours** in a section or project picker to apply the group colour to every member. Switching it off saves the current group colour to each member as its individual colour. Forced sections win, then forced projects, individual chats, projects and sections. **Save swatch** stores the custom colour for every picker. Use **Remove…** to remove a saved swatch. A thin line joins visible local parents and forks in the same sidebar list. To reset an override, choose **Use project colour**. The title dims with age and pinned chats have an outline. The mod shows local timestamps when available and reads recorded per-turn and cumulative token usage from the local Codex history. Missing usage stays blank.

On Windows, if the desktop-mods MCP server cannot start because only the Python launcher is on PATH, use `py -3 windows_mods.py status`, `install` or `uninstall` from the installed plugin root for the corresponding user request. The Windows package version reported by the Store can differ from Help → About.

With Chat Sync 0.4.0 configured, hover over a local sidebar chat for its origin device, when known, and the most recently synced revision’s delivery status. For exact peer receipts, use the Chat Sync plugin.

If a successful apply returns an `activation_offer`, show its destination and offer **Count this installation** or **Skip**. Counting sends this plugin’s name and a random receipt to that address. Call `report_activation` with the displayed destination only if the user chooses to count. Skip or silence ends the offer locally. For a later explicit request to count, read `activation_status` to identify the destination first.
