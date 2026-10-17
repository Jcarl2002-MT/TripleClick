"""Prompt Launcher: triple right-click anywhere to open your saved prompts.

Run:    python launcher.py
Build:  pyinstaller --onefile --noconsole --name launcher launcher.py
"""
import json
import os
import queue
import subprocess
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.request
from tkinter import messagebox, simpledialog, ttk

from pynput import keyboard, mouse

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# ---------- platform: remember which window has focus, and move focus to a window ----------

if IS_WIN:
    import ctypes
    try:  # crisp text, and cursor coordinates that match window coordinates
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    user32 = ctypes.windll.user32

    def focused_window():
        return user32.GetForegroundWindow()

    def focus_window(hwnd):
        # Windows won't let a background app take focus; borrow it from the foreground app
        fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        me = ctypes.windll.kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(me, fg_thread, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(me, fg_thread, False)

elif IS_MAC:
    def focused_window():
        return None

    def focus_window(window):
        if window is None:  # hand focus back to the app you were in by hiding this one
            subprocess.run(["osascript", "-e", 'tell application "System Events" to set visible of '
                            "first process whose frontmost is true to false"])

else:  # Linux (X11). Tk won't give keyboard focus to borderless windows, so ask X directly.
    from Xlib import X, display as xdisplay
    xd = xdisplay.Display()

    def focused_window():
        return xd.get_input_focus().focus

    def focus_window(window):
        if isinstance(window, int):
            window = xd.create_resource_object("window", window)
        xd.set_input_focus(window, X.RevertToParent, X.CurrentTime)
        xd.sync()

APP_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
PROMPTS_FILE = os.path.join(APP_DIR, "prompts.json")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
LAUNCHER_FILE = os.path.abspath(__file__)
UPDATE_URL = "https://raw.githubusercontent.com/Jcarl2002-MT/TripleClick/main/launcher.py"

DEFAULT_SETTINGS = {
    "theme": "dark",      # "dark" or "light"
    "click_window": 1.0,  # seconds allowed for the three right-clicks
    "paste_delay": 0.15,  # seconds to let your app regain focus before pasting
}

DEFAULT_PROMPTS = {
    "Osprey": [
        {"name": "Loan Agreement Review", "text": (
            "Review the attached loan agreement and summarize only the major, most important terms. "
            "Focus on loan amount, interest rate, payments, maturity, guarantees, covenants, default "
            "provisions and lender protections. Flag anything unusual or risky.")},
        {"name": "Guarantor Summary", "text": (
            "Summarize each guarantor in the attached documents: who they are, what they guarantee, "
            "any limits or caps on the guarantee, and their apparent financial strength.")},
        {"name": "Deal Summary", "text": (
            "Write a one-page summary of this deal: parties, purpose, structure, key terms, "
            "collateral, risks and open questions.")},
        {"name": "Red Flag Check", "text": (
            "Read the attached document and list every red flag, inconsistency or missing item "
            "a careful lender would want to know about, most serious first.")},
    ],
    "Emails": [
        {"name": "Polite Follow-Up", "text": (
            "Write a short, friendly follow-up email checking in on my previous message. "
            "Keep it under 80 words.")},
    ],
    "Claude": [
        {"name": "Explain Simply", "text": "Explain this to me simply, as if I'm new to the topic. Use a short example."},
    ],
    "Personal": [],
}

THEMES = {
    "dark": dict(bg="#1e2430", panel="#2a3140", field="#151a23", hover="#2f3a4d", select="#34518a",
                 fg="#e6e9ef", muted="#8a93a3", border="#3a4252", accent="#2f6fed",
                 accent_hover="#4a82f0", folder="#f2c14e"),
    "light": dict(bg="#ffffff", panel="#eef0f4", field="#f6f7f9", hover="#e8eefc", select="#d4e1fb",
                  fg="#1d2330", muted="#6b7280", border="#d5d9e0", accent="#2f6fed",
                  accent_hover="#245ad0", folder="#e0a82e"),
}
FONT = "Segoe UI" if IS_WIN else "Helvetica Neue" if IS_MAC else "DejaVu Sans"
WIDTH = 320            # popup width in pixels
MAX_LIST_HEIGHT = 340  # the list scrolls beyond this


# ---------- storage ----------

def load_json(path, default):
    if not os.path.exists(path):
        save_json(path, default)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def index_of(items, obj):
    return next(i for i, x in enumerate(items) if x is obj)


def search(prompts, query):
    """Every (folder, prompt) whose name or text contains query; name matches first."""
    q = query.lower()
    hits = [(f, p) for f, ps in prompts.items() for p in ps
            if q in p["name"].lower() or q in p["text"].lower()]
    return sorted(hits, key=lambda hit: q not in hit[1]["name"].lower())


def style_ttk(root, t):
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=t["bg"], foreground=t["fg"], fieldbackground=t["field"],
                bordercolor=t["border"], lightcolor=t["bg"], darkcolor=t["bg"],
                troughcolor=t["panel"], arrowcolor=t["fg"], font=(FONT, 10))
    row_height = int(tkfont.Font(root, font=(FONT, 10)).metrics("linespace") * 1.7)
    s.configure("Treeview", background=t["bg"], fieldbackground=t["bg"], rowheight=row_height)
    s.map("Treeview", background=[("selected", t["select"])], foreground=[("selected", t["fg"])])
    s.configure("TButton", background=t["panel"], padding=(10, 4))
    s.map("TButton", background=[("active", t["hover"])])
    s.configure("Muted.TLabel", foreground=t["muted"])
    s.map("TCombobox", fieldbackground=[("readonly", t["field"])], foreground=[("readonly", t["fg"])],
          selectbackground=[("readonly", t["field"])], selectforeground=[("readonly", t["fg"])])
    root.option_add("*TCombobox*Listbox.background", t["field"])
    root.option_add("*TCombobox*Listbox.foreground", t["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", t["select"])


# ---------- app ----------

class App:
    def __init__(self):
        self.settings = {**DEFAULT_SETTINGS, **load_json(SETTINGS_FILE, DEFAULT_SETTINGS)}
        self.prompts = load_json(PROMPTS_FILE, DEFAULT_PROMPTS)  # {folder: [{"name", "text"}]}
        self.root = tk.Tk()
        self.root.withdraw()
        style_ttk(self.root, self.t)
        self.popup = self.manager = None
        self.target_window = None  # the window you were in, so Paste Now can go back to it
        self.anchor = (0, 0)
        self.pos = None
        self.drag_offset = (0, 0)
        self.inside_click = 0.0  # when the popup last got a click
        self.enter_action = None
        self.events = queue.Queue()  # mouse thread -> Tk thread
        self.right_clicks = []
        self.kb = keyboard.Controller()
        mouse.Listener(on_click=self.on_click).start()  # daemon thread; ends with the app

    @property
    def t(self):
        return THEMES[self.settings["theme"]]

    def run(self):
        self.root.after(30, self.poll)
        self.root.mainloop()

    def quit(self):
        if self.manager:
            self.manager.flush()
        self.root.destroy()

    def update(self, parent):
        """Download the latest launcher.py from GitHub, replace this file, and restart."""
        if getattr(sys, "frozen", False):
            messagebox.showinfo("Update", "This is the .exe version. To update it, build a new "
                                "launcher.exe from the latest launcher.py on GitHub.", parent=parent)
            return
        try:
            with urllib.request.urlopen(UPDATE_URL, timeout=15) as response:
                new = response.read()
            compile(new, LAUNCHER_FILE, "exec")  # make sure it's real Python, not an error page
            with open(LAUNCHER_FILE, "rb") as f:
                current = f.read()
        except Exception as e:
            messagebox.showerror("Update", f"Couldn't check for updates:\n{e}", parent=parent)
            return
        if new.replace(b"\r\n", b"\n") == current.replace(b"\r\n", b"\n"):
            messagebox.showinfo("Update", "You already have the latest version.", parent=parent)
            return
        if not messagebox.askyesno("Update", "A new version is available.\n\nInstall it and restart "
                                   "the launcher? Your prompts are not affected.", parent=parent):
            return
        try:
            tmp = LAUNCHER_FILE + ".tmp"
            with open(tmp, "wb") as f:
                f.write(new)
            os.replace(tmp, LAUNCHER_FILE)
        except OSError as e:
            messagebox.showerror("Update", f"Couldn't install the update:\n{e}", parent=parent)
            return
        if self.manager:
            self.manager.flush()
        subprocess.Popen([sys.executable, LAUNCHER_FILE], cwd=APP_DIR)
        self.quit()

    def save_prompts(self):
        save_json(PROMPTS_FILE, self.prompts)

    # ----- mouse listener (runs on pynput's thread; only talks to Tk through the queue) -----

    def on_click(self, x, y, button, pressed):
        if pressed:
            self.events.put(("click", x, y))
        elif button == mouse.Button.right:
            now = time.monotonic()
            window = self.settings["click_window"]
            self.right_clicks = [t for t in self.right_clicks if now - t < window] + [now]
            if len(self.right_clicks) >= 3:
                self.right_clicks = []
                self.events.put(("triple", x, y))

    def poll(self):
        while not self.events.empty():
            kind, x, y = self.events.get()
            if kind == "triple":
                self.hide()
                self.kb.tap(keyboard.Key.esc)  # close the context menu the right-clicks opened
                self.root.after(80, self.open_popup)
            elif (self.popup and not self.contains(self.popup, x, y)
                  and time.monotonic() - self.inside_click > 0.5):  # popup may have resized under the click
                self.hide()  # clicked somewhere else
        self.root.after(30, self.poll)

    @staticmethod
    def contains(w, x, y):
        x0, y0 = w.winfo_rootx(), w.winfo_rooty()
        return x0 <= x < x0 + w.winfo_width() and y0 <= y < y0 + w.winfo_height()

    # ----- popup -----

    def open_popup(self):
        self.target_window = focused_window()
        self.anchor = self.root.winfo_pointerxy()
        self.pos = None  # set on first render, then by dragging
        self.show_popup()

    def show_popup(self):
        t = self.t
        p = self.popup = tk.Toplevel(self.root, bg=t["border"])
        p.withdraw()
        p.overrideredirect(True)
        p.attributes("-topmost", True)
        frame = tk.Frame(p, bg=t["bg"])
        frame.pack(fill="both", expand=True, padx=1, pady=1)  # 1px border

        grip = tk.Frame(frame, bg=t["bg"], height=12, cursor="fleur")
        grip.pack(fill="x")
        handle = tk.Frame(grip, bg=t["border"], width=36, height=4, cursor="fleur")
        handle.place(relx=0.5, rely=0.6, anchor="center")
        bar = tk.Frame(frame, bg=t["bg"], cursor="fleur")
        bar.pack(fill="x", padx=8, pady=(0, 4))
        for w in (grip, handle, bar):  # drag the popup by its top edge
            w.bind("<ButtonPress-1>", self.start_drag)
            w.bind("<B1-Motion>", self.drag)
        box = tk.Frame(bar, bg=t["field"], highlightthickness=1, cursor="xterm",
                       highlightbackground=t["border"], highlightcolor=t["accent"])
        box.pack(side="left", fill="x", expand=True)
        tk.Label(box, text="🔍", bg=t["field"], fg=t["muted"], font=(FONT, 9)).pack(side="left", padx=(6, 0))
        self.query = tk.StringVar()
        self.entry = tk.Entry(box, textvariable=self.query, bg=t["field"], fg=t["fg"], relief="flat",
                              insertbackground=t["fg"], highlightthickness=0, font=(FONT, 10))
        self.entry.pack(side="left", fill="x", expand=True, padx=4, pady=4)
        self.hint = tk.Label(box, text="Search prompts…", bg=t["field"], fg=t["muted"], font=(FONT, 10))
        self.hint.place(in_=self.entry, x=0, rely=0.5, anchor="w")
        self.hint.bind("<Button-1>", lambda e: self.entry.focus_set())
        theme = tk.Label(bar, text="☀" if self.settings["theme"] == "dark" else "☾", bg=t["bg"],
                         fg=t["muted"], font=(FONT, 12), cursor="hand2", padx=6)
        theme.pack(side="right")
        theme.bind("<Button-1>", lambda e: self.toggle_theme())

        self.head = tk.Frame(frame, bg=t["bg"])
        self.head.pack(fill="x")
        self.canvas = tk.Canvas(frame, bg=t["bg"], highlightthickness=0, width=WIDTH, yscrollincrement=24)
        self.canvas.pack(fill="x")
        self.list = tk.Frame(self.canvas, bg=t["bg"])
        self.canvas.create_window(0, 0, window=self.list, anchor="nw", width=WIDTH)
        self.footer = tk.Frame(frame, bg=t["bg"])
        self.footer.pack(fill="x", pady=(0, 6))

        self.query.trace_add("write", lambda *_: self.on_search())
        self.entry.bind("<Return>", lambda e: self.enter_action and self.enter_action())
        p.bind("<Escape>", lambda e: self.hide())
        p.bind("<ButtonPress>", lambda e: setattr(self, "inside_click", time.monotonic()))
        p.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        p.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        p.bind("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))

        self.show_home()
        p.deiconify()
        p.lift()
        p.update_idletasks()  # actually map the window so it can take focus
        if not IS_MAC:
            focus_window(int(p.wm_frame(), 16))
        self.entry.focus_force()

    def start_drag(self, e):
        self.drag_offset = (e.x_root - self.popup.winfo_rootx(), e.y_root - self.popup.winfo_rooty())

    def drag(self, e):
        self.pos = (e.x_root - self.drag_offset[0], e.y_root - self.drag_offset[1])
        self.popup.geometry("+%d+%d" % self.pos)

    def hide(self):
        if self.popup:
            self.popup.destroy()
            self.popup = None

    def hide_soon(self):
        """Close the popup shortly, unless it has been replaced by a new one by then."""
        popup = self.popup
        self.root.after(450, lambda: self.popup is popup and self.hide())

    def toggle_theme(self):
        self.settings["theme"] = "light" if self.settings["theme"] == "dark" else "dark"
        save_json(SETTINGS_FILE, self.settings)
        style_ttk(self.root, self.t)
        if self.manager:
            self.manager.apply_theme()
        self.hide()
        self.show_popup()

    def clear(self):
        for part in (self.head, self.list, self.footer):
            for w in part.winfo_children():
                w.destroy()
            part.config(height=1)  # an emptied frame otherwise keeps its old size
        self.enter_action = None

    def fit(self):
        """Size the list (scrolling if long) and keep the popup on screen."""
        self.list.update_idletasks()
        h = self.list.winfo_reqheight()
        self.canvas.config(height=min(h, MAX_LIST_HEIGHT), scrollregion=(0, 0, WIDTH, h))
        self.canvas.yview_moveto(0)
        p = self.popup
        p.update_idletasks()
        w, h = p.winfo_reqwidth(), p.winfo_reqheight()
        sw, sh = p.winfo_screenwidth(), p.winfo_screenheight()
        if self.pos is None:  # first time: beside the cursor, flipped left if it would run off screen
            x, y = self.anchor[0] + 2, self.anchor[1] + 2
            if x < sw < x + w:
                x -= w + 4
            self.pos = (x, y)
        x, y = self.pos
        if y < sh < y + h:
            y = max(0, sh - h - 48)  # stay above the taskbar
        p.geometry(f"+{x}+{y}")

    def row(self, parent, icon, text, command, sub=None, arrow=False, icon_color=None, button=None, more=None):
        """A clickable menu row. `button` = (text, command) adds a small button on the right and
        `more` adds a ⋯ after it. Returns (text label, button)."""
        t = self.t
        f = tk.Frame(parent, bg=t["bg"], cursor="hand2", padx=8, pady=4)
        f.pack(fill="x", padx=4)
        tk.Label(f, text=icon, bg=t["bg"], fg=icon_color or t["muted"], font=(FONT, 11), width=2).pack(side="left")
        if arrow:
            tk.Label(f, text="›", bg=t["bg"], fg=t["muted"], font=(FONT, 11)).pack(side="right")
        dots = None
        if more:
            dots = tk.Label(f, text="⋯", bg=t["bg"], fg=t["muted"], font=(FONT, 12, "bold"), padx=6)
            dots.pack(side="right")
        btn = None
        if button:
            btn = tk.Label(f, text=button[0], bg=t["accent"], fg="#ffffff", font=(FONT, 9), padx=8)
            btn.pack(side="right", padx=(6, 0))
        label = tk.Label(f, text=text, bg=t["bg"], fg=t["fg"], font=(FONT, 10), anchor="w")
        label.pack(fill="x", padx=(6, 0))
        if sub:
            tk.Label(f, text=sub, bg=t["bg"], fg=t["muted"], font=(FONT, 8), anchor="w").pack(fill="x", padx=(6, 0))
        parts = [w for w in (f, *f.winfo_children()) if w is not btn]

        def paint(color):
            for w in parts:
                w.config(bg=color)
        for w in parts:
            w.bind("<Enter>", lambda e: paint(t["hover"]))
            w.bind("<Leave>", lambda e: paint(t["bg"]))
            w.bind("<Button-1>", lambda e: command())
        if dots:
            dots.bind("<Button-1>", lambda e: more())
            dots.bind("<Enter>", lambda e: (paint(t["hover"]), dots.config(fg=t["fg"])))
            dots.bind("<Leave>", lambda e: (paint(t["bg"]), dots.config(fg=t["muted"])))
        if btn:
            btn.bind("<Button-1>", lambda e: button[1]())
            btn.bind("<Enter>", lambda e: (paint(t["hover"]), btn.config(bg=t["accent_hover"])))
            btn.bind("<Leave>", lambda e: (paint(t["bg"]), btn.config(bg=t["accent"])))
        return label, btn

    def prompt_row(self, folder, prompt, back, sub=None):
        """Copy button (or clicking the row) copies the prompt; ⋯ leads to edit and delete.
        Returns the copy action."""
        def copy():
            self.copy(prompt["text"], btn)
        _, btn = self.row(self.list, "📄", prompt["name"], copy, sub=sub, button=("Copy", copy),
                          more=lambda: self.show_options(folder, prompt, back))
        return copy

    def button(self, parent, text, command, primary=False):
        t = self.t
        bg = t["accent"] if primary else t["panel"]
        b = tk.Label(parent, text=text, bg=bg, fg="#ffffff" if primary else t["fg"],
                     font=(FONT, 10), padx=10, pady=5, cursor="hand2")
        b.bind("<Button-1>", lambda e: command())
        b.bind("<Enter>", lambda e: b.config(bg=t["accent_hover"] if primary else t["hover"]))
        b.bind("<Leave>", lambda e: b.config(bg=bg))
        return b

    def link(self, parent, text, command):
        t = self.t
        lk = tk.Label(parent, text=text, bg=t["bg"], fg=t["muted"], font=(FONT, 9), cursor="hand2", pady=2)
        lk.bind("<Button-1>", lambda e: command())
        lk.bind("<Enter>", lambda e: lk.config(fg=t["fg"]))
        lk.bind("<Leave>", lambda e: lk.config(fg=t["muted"]))
        return lk

    def separator(self, parent):
        tk.Frame(parent, bg=self.t["border"], height=1).pack(fill="x", padx=8, pady=4)

    def note(self, text):
        tk.Label(self.list, text=text, bg=self.t["bg"], fg=self.t["muted"],
                 font=(FONT, 10), pady=10).pack(fill="x")

    # ----- popup views -----

    def show_home(self):
        self.clear()
        for folder in self.prompts:
            self.row(self.list, "📁", folder, lambda f=folder: self.show_folder(f),
                     arrow=True, icon_color=self.t["folder"])
        if not self.prompts:
            self.note("No folders yet")
        self.separator(self.footer)
        self.row(self.footer, "＋", "Add New Prompt", lambda: self.open_manager(lambda m: m.add_prompt()))
        self.row(self.footer, "⚙", "Manage Prompts", self.open_manager)
        self.row(self.footer, "✕", "Close", self.hide)
        self.fit()

    def show_folder(self, folder):
        self.clear()
        self.row(self.head, "←", folder, self.show_home)
        self.separator(self.head)
        for p in self.prompts.get(folder, []):
            self.prompt_row(folder, p, back=lambda: self.show_folder(folder))
        if not self.prompts.get(folder):
            self.note("No prompts in this folder yet")
        self.separator(self.footer)
        self.row(self.footer, "＋", "Add Prompt to this Folder",
                 lambda: self.open_manager(lambda m: m.add_prompt(folder)))
        self.fit()

    def on_search(self):
        query = self.query.get().strip()
        if self.query.get():
            self.hint.place_forget()
        else:
            self.hint.place(in_=self.entry, x=0, rely=0.5, anchor="w")
        self.show_results(query) if query else self.show_home()

    def show_results(self, query):
        self.clear()
        results = search(self.prompts, query)
        copies = [self.prompt_row(f, p, back=lambda: self.show_results(query), sub=f) for f, p in results]
        if results:  # Enter copies the top result
            self.enter_action = copies[0]
        else:
            self.note("No prompts found")
        self.fit()

    def show_options(self, folder, prompt, back):
        t = self.t
        self.clear()
        self.row(self.head, "←", folder, back)
        self.separator(self.head)
        tk.Label(self.list, text=prompt["name"], bg=t["bg"], fg=t["fg"], font=(FONT, 11, "bold"),
                 anchor="w", justify="left", wraplength=WIDTH - 28).pack(fill="x", padx=14, pady=(2, 4))
        text = prompt["text"].strip() or "(empty prompt)"
        tk.Label(self.list, text=text[:400] + ("…" if len(text) > 400 else ""), bg=t["bg"], fg=t["muted"],
                 font=(FONT, 9), anchor="w", justify="left",
                 wraplength=WIDTH - 28).pack(fill="x", padx=14, pady=(0, 4))
        buttons = tk.Frame(self.footer, bg=t["bg"])
        buttons.pack(fill="x", padx=12, pady=(6, 2))
        buttons.columnconfigure((0, 1), weight=1, uniform="b")
        copy = self.button(buttons, "Copy", lambda: self.copy(prompt["text"], copy), primary=True)
        copy.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.button(buttons, "Paste Now", lambda: self.paste(prompt["text"]), primary=True).grid(
            row=0, column=1, sticky="ew", padx=(4, 0))
        self.separator(self.footer)
        links = tk.Frame(self.footer, bg=t["bg"])
        links.pack(fill="x", padx=14)
        self.link(links, "✎  Edit", lambda: self.open_manager(lambda m: m.refresh((folder, prompt)))).pack(side="left")
        delete = self.link(links, "🗑  Delete", lambda: confirm_delete())
        delete.pack(side="right")

        def confirm_delete():
            if delete.cget("text") == "🗑  Delete":  # first click asks, second click deletes
                delete.config(text="Click again to delete", fg="#e5534b")
                delete.bind("<Leave>", lambda e: None)
            else:
                self.delete_prompt(folder, prompt)
                back()
        self.fit()

    def delete_prompt(self, folder, prompt):
        items = self.prompts[folder]
        items.pop(index_of(items, prompt))
        self.save_prompts()
        if self.manager:
            self.manager.refresh()

    # ----- clipboard / paste -----

    def copy(self, text, label=None):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        if label:
            label.config(text="✓ Copied")
            self.hide_soon()

    def paste(self, text):
        self.copy(text)
        self.hide()
        focus_window(self.target_window)  # back to the window you were typing in
        self.root.after(int(self.settings["paste_delay"] * 1000), self.send_paste)

    def send_paste(self):
        with self.kb.pressed(keyboard.Key.cmd if IS_MAC else keyboard.Key.ctrl):
            self.kb.tap("v")

    # ----- manager -----

    def open_manager(self, then=None):
        self.hide()
        if not self.manager:
            self.manager = Manager(self)
        win = self.manager.win
        win.deiconify()
        win.lift()
        win.focus_force()
        if then:
            then(self.manager)


class Manager:
    """The full organizer: folders, prompts, drag to move, edit text. Changes save automatically."""

    def __init__(self, app):
        self.app = app
        self.items = {}         # tree item id -> (folder, prompt or None)
        self.current = None     # (item id, prompt) shown in the editor
        self.shown = None       # tree item id currently loaded in the editor
        self.loading = False
        self.drag = None
        self.save_job = None

        w = self.win = tk.Toplevel(app.root)
        w.title("Manage Prompts")
        w.geometry("860x540")
        w.minsize(620, 380)
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.columnconfigure(1, weight=1)
        w.rowconfigure(0, weight=1)

        left = ttk.Frame(w, padding=(10, 10, 4, 10))
        left.grid(row=0, column=0, sticky="ns")
        left.rowconfigure(1, weight=1)
        ttk.Label(left, text="My Prompts", font=(FONT, 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.column("#0", width=260)
        self.tree.grid(row=1, column=0, sticky="ns")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.config(yscrollcommand=scroll.set)
        tools = ttk.Frame(left)
        tools.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        for i, (label, cmd) in enumerate([("+ Folder", self.add_folder), ("+ Prompt", self.add_prompt),
                                          ("Rename", self.rename), ("Duplicate", self.duplicate),
                                          ("Delete", self.delete)]):
            ttk.Button(tools, text=label, command=cmd).grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)
        tools.columnconfigure((0, 1), weight=1)

        right = ttk.Frame(w, padding=(8, 10, 10, 10))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(5, weight=1)
        ttk.Label(right, text="Name").grid(row=0, column=0, sticky="w")
        self.name = tk.StringVar()
        self.name_entry = ttk.Entry(right, textvariable=self.name, font=(FONT, 10))
        self.name_entry.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        ttk.Label(right, text="Folder").grid(row=2, column=0, sticky="w")
        self.folder = tk.StringVar()
        self.folder_box = ttk.Combobox(right, textvariable=self.folder, state="readonly")
        self.folder_box.grid(row=3, column=0, sticky="ew", pady=(2, 8))
        ttk.Label(right, text="Prompt").grid(row=4, column=0, sticky="w")
        self.text = tk.Text(right, wrap="word", undo=True, relief="flat", padx=8, pady=6,
                            highlightthickness=1, font=(FONT, 10))
        self.text.grid(row=5, column=0, sticky="nsew", pady=(2, 8))
        bottom = ttk.Frame(right)
        bottom.grid(row=6, column=0, sticky="ew")
        ttk.Button(bottom, text="Quit Launcher", command=app.quit).pack(side="right")
        ttk.Button(bottom, text="Check for Updates", command=lambda: app.update(self.win)).pack(side="right", padx=6)
        ttk.Label(bottom, text="Changes save automatically",
                  style="Muted.TLabel").pack(side="left")

        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<ButtonPress-1>", lambda e: setattr(self, "drag", self.tree.identify_row(e.y)))
        self.tree.bind("<ButtonRelease-1>", self.on_drop)
        self.tree.bind("<Delete>", lambda e: self.delete())
        self.tree.bind("<F2>", lambda e: self.rename())
        self.name.trace_add("write", lambda *_: self.on_edit())
        self.text.bind("<<Modified>>", self.on_text_modified)
        self.folder_box.bind("<<ComboboxSelected>>", self.on_folder_change)

        self.apply_theme()
        self.refresh()

    def apply_theme(self):
        t = self.app.t
        self.win.config(bg=t["bg"])
        self.text.config(bg=t["field"], fg=t["fg"], insertbackground=t["fg"], selectbackground=t["select"],
                         selectforeground=t["fg"], highlightbackground=t["border"], highlightcolor=t["accent"])

    def close(self):
        self.flush()
        self.win.destroy()
        self.app.manager = None

    # ----- tree -----

    def refresh(self, select=None):
        """Rebuild the tree from app.prompts. select = (folder, prompt or None)."""
        opened = ({self.items[i][0] for i in self.tree.get_children() if self.tree.item(i, "open")}
                  if self.items else set(self.app.prompts))
        self.tree.delete(*self.tree.get_children())
        self.items, target = {}, None
        for folder, prompts in self.app.prompts.items():
            fid = self.tree.insert("", "end", text=f"📁  {folder}", open=folder in opened)
            self.items[fid] = (folder, None)
            if select and select[0] == folder and select[1] is None:
                target = fid
            for p in prompts:
                pid = self.tree.insert(fid, "end", text=f"📄  {p['name']}")
                self.items[pid] = (folder, p)
                if select and select[1] is p:
                    target = pid
        self.folder_box.config(values=list(self.app.prompts))
        if target:
            self.tree.see(target)
            self.tree.selection_set(target)
            self.tree.focus(target)
        self.on_select()

    def selected(self):
        sel = self.tree.selection()
        return self.items[sel[0]] if sel else (None, None)

    def on_select(self, _=None):
        sel = self.tree.selection()
        if (sel[0] if sel else None) == self.shown:
            return
        self.shown = sel[0] if sel else None
        folder, prompt = self.selected()
        self.current = (sel[0], prompt) if prompt else None
        self.loading = True
        self.name.set(prompt["name"] if prompt else "")
        self.folder.set(folder or "")
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        if prompt:
            self.text.insert("1.0", prompt["text"])
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.text.config(state="normal" if prompt else "disabled")
        self.name_entry.config(state="normal" if prompt else "disabled")
        self.folder_box.config(state="readonly" if prompt else "disabled")
        self.loading = False

    def on_text_modified(self, _):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self.on_edit()

    def on_edit(self):
        if self.loading or not self.current:
            return
        iid, prompt = self.current
        prompt["name"] = self.name.get()
        prompt["text"] = self.text.get("1.0", "end-1c")
        self.tree.item(iid, text=f"📄  {prompt['name']}")
        if self.save_job:
            self.win.after_cancel(self.save_job)
        self.save_job = self.win.after(400, self.flush)

    def flush(self):
        if self.save_job:
            self.win.after_cancel(self.save_job)
            self.save_job = None
        self.app.save_prompts()

    def on_folder_change(self, _=None):
        folder, prompt = self.selected()
        if prompt and self.folder.get() != folder:
            self.move(prompt, folder, self.folder.get())

    def move(self, prompt, src, dst, before=None):
        self.app.prompts[src].pop(index_of(self.app.prompts[src], prompt))
        items = self.app.prompts[dst]
        items.insert(len(items) if before is None else index_of(items, before), prompt)
        self.app.save_prompts()
        self.refresh((dst, prompt))

    def on_drop(self, e):
        src, dst = self.drag, self.tree.identify_row(e.y)
        self.drag = None
        if not src or not dst or src == dst:
            return
        src_folder, prompt = self.items[src]
        dst_folder, before = self.items[dst]
        if prompt:  # prompt dropped on a folder (goes to the end) or on a prompt (goes before it)
            self.move(prompt, src_folder, dst_folder, before)
        elif before is None:  # folder dropped on a folder: reorder
            names = [n for n in self.app.prompts if n != src_folder]
            names.insert(names.index(dst_folder), src_folder)
            self.app.prompts = {n: self.app.prompts[n] for n in names}
            self.app.save_prompts()
            self.refresh((src_folder, None))

    # ----- toolbar actions -----

    def ask_folder_name(self, title, initial=""):
        name = simpledialog.askstring(title, "Folder name:", initialvalue=initial, parent=self.win)
        name = (name or "").strip()
        if name and name != initial and name in self.app.prompts:
            messagebox.showerror("Folder exists", f'There is already a folder called "{name}".', parent=self.win)
            return None
        return name or None

    def add_folder(self):
        name = self.ask_folder_name("New Folder")
        if name:
            self.app.prompts[name] = []
            self.app.save_prompts()
            self.refresh((name, None))

    def add_prompt(self, folder=None):
        folder = folder or self.selected()[0] or next(iter(self.app.prompts), None) or "General"
        prompt = {"name": "New Prompt", "text": ""}
        self.app.prompts.setdefault(folder, []).append(prompt)
        self.app.save_prompts()
        self.refresh((folder, prompt))
        self.name_entry.focus_set()
        self.name_entry.select_range(0, "end")

    def rename(self):
        folder, prompt = self.selected()
        if prompt:
            self.name_entry.focus_set()
            self.name_entry.select_range(0, "end")
        elif folder:
            name = self.ask_folder_name("Rename Folder", folder)
            if name and name != folder:
                self.app.prompts = {(name if k == folder else k): v for k, v in self.app.prompts.items()}
                self.app.save_prompts()
                self.refresh((name, None))

    def duplicate(self):
        folder, prompt = self.selected()
        if not prompt:
            return
        copy = dict(prompt, name=prompt["name"] + " (copy)")
        items = self.app.prompts[folder]
        items.insert(index_of(items, prompt) + 1, copy)
        self.app.save_prompts()
        self.refresh((folder, copy))

    def delete(self):
        folder, prompt = self.selected()
        if prompt:
            if messagebox.askyesno("Delete Prompt", f'Delete "{prompt["name"]}"?', parent=self.win):
                self.app.prompts[folder].pop(index_of(self.app.prompts[folder], prompt))
        elif folder:
            count = len(self.app.prompts[folder])
            extra = f" and its {count} prompt{'s' if count != 1 else ''}" if count else ""
            if messagebox.askyesno("Delete Folder", f'Delete folder "{folder}"{extra}?', parent=self.win):
                del self.app.prompts[folder]
        else:
            return
        self.app.save_prompts()
        self.refresh((folder, None) if prompt else None)


if __name__ == "__main__":
    App().run()
