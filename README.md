# Codex Desktop Mods

Unofficial mods for the Linux ChatGPT desktop app. They colour sidebar titles, add timestamps and token counts, and give wide chats more room with a dock for your selected pet.

Tested with Linux desktop builds `26.1027.51404`, `26.1029.61003`, `26.1103.31037`, `26.1103.32239` and `26.1103.60933` installed at `/usr/lib/chatgpt`. The mods install together.

## Sidebar colours

Right-click a project or chat and choose **Project colour…** or **Chat colour…**. Shift + right-click opens the picker directly. A preset applies immediately. For a custom colour, drag around the wheel to pick a hue, move toward the centre for less saturation, then set the **Brightness** bar and click **Apply custom colour**. With the wheel focused, Left and Right adjust hue and Up and Down adjust saturation. Hold Shift for larger steps.

![A project colour picker showing amber on the wheel and brightness at 81%](images/colour-picker.png)

- A project’s chats inherit its colour, including when they appear in Recents. Pick a colour for one chat to override it, or choose **Use project colour** to restore inheritance.
- The project title keeps its chosen colour. For the first day after an update, a chat title shows its exact project colour or its individual override. It gradually dims to 75% brightness by day 14, keeping the same hue.
- A thin outline marks pinned chats in light and dark themes.

## Timestamps and token use

Messages show their local date and time when the chat history provides a timestamp. On a completed Codex turn, **225 tokens this turn · 340 chat total** means the model used 225 input and output tokens across the calls for that turn, bringing the recorded conversation total to 340. Reopening the chat shows the counts through each reply. Regular ChatGPT conversations show timestamps when available.

The mod reads usage from the existing local Codex session history. Its desktop bridge returns numeric metadata for the requested turns. Missing usage stays blank.

![A test chat showing two reply timestamps and the running token total](images/chat-details.png)

## Wider chats and the pet dock

On wide windows the messages and composer expand together, leaving the Sources and Outputs card clear. Your selected pet sits in the right margin and runs in place as you scroll. Scrolling down makes it run right, scrolling up makes it run left, and it settles when scrolling stops. The dock gives way when the window gets narrow.

Click the pet to open its floating window, or summon it with the usual desktop pet command. Closing that window returns it to the chat. The dock uses the desktop’s selected artwork and respects reduced motion.

## Install

Use Python 3 on Linux. Allow approximately 1 GB of free space for the cached app copy. Download and extract the repository, then run this from the extracted folder as your normal user.

```sh
python3 sidebar-colours.py install
```

Fully quit ChatGPT, then reopen **ChatGPT** from your applications menu. The launcher runs the patched copy under your user cache. Your colours stay in the desktop profile. To upgrade from an earlier release, run the same install command from the new download once.

## Desktop updates

After the package manager updates ChatGPT, open **ChatGPT** from the applications menu as usual. On the first launch, the mods check the new package and rebuild the cached copy if the required app components are compatible. Later launches reuse that copy.

If an update changes an integration the mods depend on, the launcher opens stock ChatGPT and reports the reason. Your colours stay saved. The launcher checks again when the desktop package changes. To pick up support added in a newer mod release, download it and rerun the install command.

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

## Tests

Run the history reader and launcher tests from the repository folder. They use synthetic chat history and disposable desktop packages.

```sh
node tests/chat-details.cjs
python3 tests/updates.py
```

With the Linux desktop app and Google Chrome installed, run `node tests/sidebar-colours.cjs` to check the rendered colours in both themes and `node tests/chat-layout.cjs` to check chat width, panel clearance and scroll behaviour.
