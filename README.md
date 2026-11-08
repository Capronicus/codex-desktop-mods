# Codex Desktop Mods

Two unofficial mods for the Linux ChatGPT desktop app. One colours the project and chat titles in the sidebar. The other adds timestamps and token counts to conversations.

**Supported build: 26.1027.51404** with the app installed at `/usr/lib/chatgpt`. Both mods install together. The installer checks the app version and asset hashes before it creates a per-user launcher.

## Sidebar colours

Right-click a project or chat and choose **Project colour…** or **Chat colour…**. Shift + right-click opens the picker directly. Choose a preset or use a custom colour.

- A project’s chats inherit its colour, including when they appear in Recents. Pick a colour for one chat to override it, or choose **Use project colour** to restore inheritance.
- The project title keeps its chosen colour. A chat title is brightest for the first day after an update and gradually darkens over two weeks.
- A thin outline marks pinned chats in light and dark themes.

## Timestamps and token use

Messages show their local date and time when the chat history provides a timestamp. On a completed Codex turn, **225 tokens this turn · 340 chat total** means the model used 225 input and output tokens across the calls for that turn, bringing the recorded conversation total to 340. Reopening the chat shows the counts through each reply. Regular ChatGPT conversations show timestamps when available.

The mod reads usage from the existing local Codex session history. Its desktop bridge returns numeric metadata for the requested turns. Missing usage stays blank.

![A test chat showing two reply timestamps and the running token total](images/chat-details.png)

## Install

Use Python 3 on Linux. Allow approximately 1 GB of free space for the cached app copy. Download and extract the repository, then run this from the extracted folder as your normal user.

```sh
python3 sidebar-colours.py install
```

Fully quit ChatGPT, then reopen **ChatGPT** from your applications menu. The launcher runs the patched copy under your user cache. The system package stays available. If a system update changes the installed app, the launcher opens the system version until a compatible mod is available.

## Remove or recover

Fully quit ChatGPT. From the extracted folder, run the following command and reopen ChatGPT from the applications menu.

```sh
python3 sidebar-colours.py uninstall
```

If you already removed the download, use the installed script.

```sh
python3 "${XDG_DATA_HOME:-$HOME/.local/share}/codex-sidebar-colours/sidebar-colours.py" uninstall
```

To start the stock app directly after quitting the modded instance, run `/usr/bin/chatgpt`.

## Test the history reader

With Node.js installed, run the test from the repository folder. It uses synthetic chat history in a temporary directory.

```sh
node tests/chat-details.cjs
```
