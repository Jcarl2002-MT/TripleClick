# Prompt Launcher

A small saved-prompt launcher that runs in the background. **Right-click 3 times quickly anywhere** and a
compact menu of your prompts opens right at your cursor. Pick one and it's copied or pasted straight into
whatever you were typing in (ChatGPT, Claude, email, Word, a browser…). A normal single right-click still works
as usual.

Everything is one Python file (`launcher.py`) plus a local `prompts.json`. No account, database or internet.

## Run it

```
pip install -r requirements.txt
python launcher.py        # on Windows, use  pythonw launcher.py  to run without a console window
```

The first run creates `prompts.json` (with a few example prompts) and `settings.json` next to `launcher.py`.

## Use it

| Do this | What happens |
| --- | --- |
| Right-click 3× within a second | Popup opens beside the cursor |
| Click a folder, then **Copy** next to a prompt | Copies it to your clipboard and closes the popup (clicking the name works too) |
| Click **⋯** next to a prompt | Shows the prompt's text with **Copy** and **Paste Now**, plus small **Edit** and **Delete** links |
| Drag the top edge (the little bar) | Moves the popup |
| Just start typing | Searches every prompt's name and text; `Enter` copies the top result |
| `Esc` or click elsewhere | Closes the popup |
| ☀ / ☾ | Switches between dark and light mode |

- **Paste Now** copies the prompt, switches back to the window you were in, and presses Ctrl+V for you.
- **Delete** asks you to click a second time to confirm.
- **Manage Prompts** opens the full organizer: add, rename, duplicate and delete folders and prompts, edit
  prompt text, and drag prompts between folders (drag a folder onto another to reorder). Changes save
  automatically. **Quit Launcher** is at the bottom right of this window.

## Make a `launcher.exe` (Windows)

```
pip install pyinstaller
pyinstaller --onefile --noconsole --name launcher launcher.py
```

Put `dist/launcher.exe` in its own folder, e.g. `prompt_launcher/`. Your `prompts.json` and `settings.json`
are kept next to it. To start it with Windows, press `Win+R`, type `shell:startup`, and drop a shortcut to
`launcher.exe` in the folder that opens.

## Your files

`prompts.json` is plain JSON: folder name → list of prompts. You can edit it by hand or back it up by copying it.

```json
{
  "Osprey": [
    {"name": "Loan Agreement Review", "text": "Review the attached loan agreement and summarize..."}
  ],
  "Emails": []
}
```

`settings.json`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `theme` | `"dark"` | `"dark"` or `"light"` |
| `click_window` | `1.0` | Seconds allowed for the three right-clicks |
| `paste_delay` | `0.15` | Seconds to wait for your app to regain focus before pasting. Raise it if Paste Now misses. |

## Platforms

Built for **Windows**. It also runs on **Linux with X11** (not Wayland). **macOS** may work but is untested:
it needs Accessibility and Input Monitoring permission for your terminal or Python.

## Ideas for later

Favorites, recently used prompts, nested folders, import/export, and `{{variables}}` that ask for values
before pasting.
