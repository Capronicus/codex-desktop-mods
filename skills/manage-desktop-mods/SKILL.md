---
name: manage-desktop-mods
description: Use when the user asks to check, apply, update or remove the Codex Desktop Mods Windows or Linux patch, or use its sidebar colours, timestamps and token counts.
---

Use the plugin's local tools on the Windows or Linux machine running ChatGPT. For setup, a status question or troubleshooting, call `desktop_mods_status`. Report the desktop build, menu launcher and any action the result calls for. Report a missing installation or incompatible build as such.

On a request to apply, repair or update the patch, call `apply_desktop_mods`. It bundles the original mod, uses its compatibility checks and works with an existing manual installation. After a successful change, ask the user to fully quit ChatGPT. On Windows, reopen **ChatGPT (Desktop Mods)** from Start. On Linux, reopen **ChatGPT** from the applications menu. The original Windows shortcut opens the Store app. Allow the user's running chat to finish.

On a request to remove the patch or return to stock, call `remove_desktop_mods`. If it reports deferred cleanup, the next menu launch uses the stock app. After reopening, the user can ask to finish removing the mods. Removing the manager itself is a separate Codex plugin action. To remove both, remove the patch first.

For colours, right-click a custom section heading, project or chat and select its colour action. Shift + right-click opens the picker directly. Use **Override all chat colours** in a section or project picker to apply the group colour to every member. Switching it off saves the current group colour to each member as its individual colour. Forced sections win, then forced projects, individual chats, projects and sections. **Save swatch** stores the custom colour for every picker. Use **Remove…** to remove a saved swatch. A thin line joins visible local parents and forks in the same sidebar list. To reset an override, choose **Use project colour**. The title dims with age and pinned chats have an outline. The mod shows local timestamps when available and reads recorded per-turn and cumulative token usage from the local Codex history. Missing usage stays blank.

On Windows, if the desktop-mods MCP server cannot start because only the Python launcher is on PATH, use `py -3 windows_mods.py status`, `install` or `uninstall` from the installed plugin root for the corresponding user request. If the pet opens the Chat Sync listing and `python3` resolves to the Store placeholder, run `py -3 setup_windows_python.py` from the plugin root for a requested launcher repair. Reload Chat Sync in Settings > Plugins and verify its Mod Controls view. The Windows package version reported by the Store can differ from Help → About.

With Chat Sync 0.4.0 configured, hover over a local sidebar chat for its origin device, when known, and the most recently synced revision’s delivery status. For exact peer receipts, use the Chat Sync plugin.

If a successful apply returns an `activation_offer`, show its destination and offer **Count this installation** or **Skip**. Counting sends this plugin’s name and a random receipt to that address. Call `report_activation` with the displayed destination only if the user chooses to count. Skip or silence ends the offer locally. For a later explicit request to count, read `activation_status` to identify the destination first.

With Desktop Mods and Chat Sync 0.12.0, right-click a local reply to open a corresponding file in a linked checkout or hand the reply to a named desktop. The pet menu opens reply handoffs. Right-click a local sidebar chat for its branches or a project for sharing controls. Run the desktop responsiveness check from Mod Controls → Chat actions and sharing.

In version 0.15.0, open **Mod Controls → Organize projects and sections** to create a subsection under a project or custom section (up to three levels including the root), change its parent or convert between a custom section and a real local project. Apply queued changes by fully quitting and reopening the app. A converted project remembers its local folder paths.

Select words in a message and right-click for **Animate selected text**. Select marked text for **Remove text animation**. These marks are local to this desktop and follow the reduced-motion preference.

Use **Add to batch** in the organizer to preview a series of creations and moves before applying it on quit. Enable **Subsection tree** separately on both desktops to share nesting. File handoffs can include a relative path and fingerprint for comparison. Right-click a message or selection to bookmark it. Open bookmarks from the pet. Saved views in the sidebar filter local chats by project, origin, age and outstanding receipt. Bookmarks and saved views are local.

When the mods cannot be applied after a desktop update, call `desktop_mods_compatibility`. It lists the renderer modules of the installed build that changed or are missing and, when every module is recognised, the first hook that no longer fits. Report those module IDs and roles. They tell the maintainer what to update.
