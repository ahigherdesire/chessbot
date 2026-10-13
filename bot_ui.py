"""Small control panel for the auto-player: settings, start/stop, eval bar, human index.

  python bot_ui.py

Settings take effect on the next move, even while the bot is playing.
Keep this window off the chess board: the bot reads the board from the screen.
"""
import math
import queue
import threading
import tkinter as tk
from tkinter import ttk

import auto_play as ap
import board_coords as bc
import engine

BG, PANEL, TEXT, MUTED = "#1e1f22", "#2b2d31", "#e8e8e8", "#9a9ca3"
WHITE_BAR, BLACK_BAR = "#f0f0f0", "#111214"
GOOD, OK, WARN, BAD = "#5fb760", "#c9b84a", "#d98c3f", "#d0544b"
TIMING = ["off", "bullet", "blitz", "rapid", "classical"]


def white_share(cp, mate):
    """Fraction of the bar that is white (lichess win-chance curve)."""
    if mate is not None:
        return 1.0 if mate > 0 else 0.0
    if cp is None:
        return 0.5
    return 0.5 + 0.5 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def eval_text(cp, mate):
    if mate is not None:
        return f"M{mate}" if mate > 0 else f"-M{-mate}"
    if cp is None:
        return "0.0"
    return f"{cp / 100:+.1f}"


class BotUI:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.thread = None
        self.stop = threading.Event()
        self.args = ap.build_parser().parse_args([])

        root.title("Chess Bot")
        root.configure(bg=BG)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._style()

        main = ttk.Frame(root, padding=10)
        main.grid(sticky="nsew")
        self._settings(main)
        self._controls(main)
        self._game(main)
        self._log(main)

        self.on_top.set(True)
        self.toggle_top()
        root.update_idletasks()
        root.geometry(f"+{root.winfo_screenwidth() - root.winfo_width() - 20}+40")  # top-right corner
        self.root.after(100, self.pump)

    # ---- layout --------------------------------------------------------- #
    def _style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=TEXT, fieldbackground=PANEL, font=("Segoe UI", 9))
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=TEXT)
        s.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        s.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        s.configure("Big.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 15, "bold"))
        s.configure("Head.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 8, "bold"))
        s.configure("TCheckbutton", background=BG, foreground=TEXT)
        s.map("TCheckbutton", background=[("active", BG)])
        s.configure("TButton", background=PANEL, foreground=TEXT, borderwidth=0, padding=6)
        s.map("TButton", background=[("active", "#3a3c42"), ("disabled", PANEL)], foreground=[("disabled", MUTED)])
        s.configure("Start.TButton", background="#3d7a46")
        s.map("Start.TButton", background=[("active", "#4a8f54"), ("disabled", PANEL)])
        s.configure("Stop.TButton", background="#8a3a35")
        s.map("Stop.TButton", background=[("active", "#a3443e"), ("disabled", PANEL)])
        s.configure("TCombobox", arrowcolor=TEXT, foreground=TEXT, background=PANEL)
        s.map("TCombobox", fieldbackground=[("readonly", PANEL), ("disabled", BG)],
              foreground=[("disabled", MUTED), ("readonly", TEXT)],
              selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#3d7a46")
        s.configure("TSpinbox", arrowcolor=TEXT, foreground=TEXT, background=PANEL)
        s.configure("Horizontal.TScale", background=BG, troughcolor=PANEL)

    def _row(self, parent, r, label, widget, hint=""):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=2)
        widget.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=2)
        if hint:
            ttk.Label(parent, text=hint, foreground=MUTED).grid(row=r, column=2, sticky="w", padx=(6, 0))

    def _settings(self, parent):
        ttk.Label(parent, text="SETTINGS", style="Head.TLabel").grid(row=0, column=0, sticky="w")
        f = ttk.Frame(parent)
        f.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        f.columnconfigure(1, weight=1)

        self.accuracy = tk.DoubleVar(value=100)
        acc_box = ttk.Frame(f)
        ttk.Scale(acc_box, from_=0, to=100, variable=self.accuracy, length=150,
                  command=lambda _: self.acc_label.config(text=f"{self.accuracy.get():.0f}%")).pack(side="left")
        self.acc_label = ttk.Label(acc_box, text="100%", width=5)
        self.acc_label.pack(side="left", padx=(6, 0))
        self._row(f, 0, "Accuracy", acc_box)

        self.think = tk.DoubleVar(value=1.0)
        self._row(f, 1, "Think time", ttk.Spinbox(f, from_=0.1, to=10, increment=0.1, textvariable=self.think,
                                                 width=8), "seconds of engine search")
        self.timing = tk.StringVar(value="off")
        self._row(f, 2, "Human timing", ttk.Combobox(f, values=TIMING, textvariable=self.timing, state="readonly",
                                                    width=10), "fast on easy moves, slow on hard")
        self.max_think = tk.DoubleVar(value=30)
        self._row(f, 3, "Max think", ttk.Spinbox(f, from_=1, to=120, increment=1, textvariable=self.max_think,
                                                width=8), "seconds, caps human timing")
        self.rating = tk.IntVar(value=1500)
        self._row(f, 4, "Human index", ttk.Spinbox(f, from_=600, to=2800, increment=100, textvariable=self.rating,
                                                  width=8), "rating to compare with")
        self.side = tk.StringVar(value="auto")
        self.side_box = ttk.Combobox(f, values=["auto", "white", "black"], textvariable=self.side,
                                     state="readonly", width=10)
        self._row(f, 5, "Play as", self.side_box, "auto = side at the bottom")
        self.move_with = tk.StringVar(value="click")
        self._row(f, 6, "Move by", ttk.Combobox(f, values=["click", "drag"], textvariable=self.move_with,
                                               state="readonly", width=10))

        checks = ttk.Frame(f)
        checks.grid(row=7, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.dry_run = tk.BooleanVar(value=False)
        ttk.Checkbutton(checks, text="Dry run (hover, never click)", variable=self.dry_run).pack(side="left")
        self.on_top = tk.BooleanVar()
        ttk.Checkbutton(checks, text="Keep on top", variable=self.on_top,
                        command=self.toggle_top).pack(side="left", padx=(12, 0))

        for var in (self.accuracy, self.think, self.timing, self.max_think, self.rating, self.move_with, self.dry_run):
            var.trace_add("write", lambda *_: self.apply())

    def _controls(self, parent):
        f = ttk.Frame(parent)
        f.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        f.columnconfigure((0, 1), weight=1)
        self.start_btn = ttk.Button(f, text="Start", style="Start.TButton", command=self.start)
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.stop_btn = ttk.Button(f, text="Stop", style="Stop.TButton", command=self.stop_bot, state="disabled")
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.status = ttk.Label(parent, text="Stopped", foreground=MUTED)
        self.status.grid(row=3, column=0, sticky="w", pady=(0, 8))

    def _game(self, parent):
        ttk.Label(parent, text="GAME", style="Head.TLabel").grid(row=4, column=0, sticky="w")
        f = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        f.grid(row=5, column=0, sticky="ew", pady=(2, 8))
        f.columnconfigure(1, weight=1)

        self.bar = tk.Canvas(f, width=26, height=150, bg=PANEL, highlightthickness=0)
        self.bar.grid(row=0, column=0, rowspan=6, sticky="ns", padx=(0, 12))

        self.eval_lbl = ttk.Label(f, text="0.0", style="Big.TLabel")
        self.eval_lbl.grid(row=0, column=1, sticky="w")
        self.win_lbl = ttk.Label(f, text="White 50%  ·  Black 50%", style="Muted.TLabel")
        self.win_lbl.grid(row=1, column=1, sticky="w")

        ttk.Label(f, text="Last move", style="Muted.TLabel").grid(row=2, column=1, sticky="w", pady=(10, 0))
        self.move_lbl = ttk.Label(f, text="-", style="Panel.TLabel", font=("Segoe UI", 11, "bold"))
        self.move_lbl.grid(row=3, column=1, sticky="w")

        ttk.Label(f, text="Human index", style="Muted.TLabel").grid(row=4, column=1, sticky="w", pady=(10, 0))
        hi = ttk.Frame(f, style="Panel.TFrame")
        hi.grid(row=5, column=1, sticky="ew")
        self.hi_bar = tk.Canvas(hi, width=120, height=10, bg=BG, highlightthickness=0)
        self.hi_bar.pack(side="left")
        self.hi_lbl = ttk.Label(hi, text="-", style="Panel.TLabel")
        self.hi_lbl.pack(side="left", padx=(8, 0))
        self.white_bottom = True
        self.draw_bar(0.5)

    def _log(self, parent):
        ttk.Label(parent, text="LOG", style="Head.TLabel").grid(row=6, column=0, sticky="w")
        self.log_box = tk.Text(parent, width=48, height=9, bg=PANEL, fg=MUTED, relief="flat", wrap="word",
                               font=("Consolas", 8), state="disabled", padx=6, pady=4)
        self.log_box.grid(row=7, column=0, sticky="ew", pady=(2, 0))

    # ---- settings -> bot ------------------------------------------------ #
    def apply(self):
        """Copy the controls into the args the bot reads every move."""
        a = self.args
        try:
            a.accuracy = float(self.accuracy.get())
            a.time = max(0.1, float(self.think.get()))
            a.max_think = max(1.0, float(self.max_think.get()))
            a.human_index = int(self.rating.get())
        except (tk.TclError, ValueError):
            return                     # a half-typed number: keep the previous value
        a.human = None if self.timing.get() == "off" else self.timing.get()
        a.drag = self.move_with.get() == "drag"
        a.dry_run = self.dry_run.get()
        a.side = None if self.side.get() == "auto" else self.side.get()

    # ---- running -------------------------------------------------------- #
    def start(self):
        if bc.pyautogui is None:
            self.log("pyautogui is missing: pip install pyautogui")
            return
        self.apply()
        self.stop = threading.Event()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.side_box.config(state="disabled")
        self.set_status("Looking for the board...", OK)
        self.log("Started. Keep this window off the board.")
        self.thread = threading.Thread(target=self.run_bot, daemon=True)
        self.thread.start()

    def run_bot(self):
        put = self.events.put
        try:
            bot = ap.AutoPlayer(self.args, log=lambda msg: put(("log", msg)),
                                on_move=lambda m: put(("move", m)), stop=self.stop)
            self.bot = bot
            bot.run()
            put(("stopped", "Stopped"))
        except bc.pyautogui.FailSafeException:
            put(("stopped", "Stopped (mouse in a screen corner)"))
        except Exception as e:          # show it rather than dying silently
            put(("stopped", f"Error: {e}"))

    def stop_bot(self):
        self.stop.set()
        self.set_status("Stopping...", OK)

    def pump(self):
        """Handle messages from the bot thread (tkinter must only be touched here)."""
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "log":
                    self.log(data.strip())
                    if "Playing" in data:
                        self.set_status(data.strip().split(". ")[-1], GOOD)
                        self.white_bottom = "white" in data.split("Playing")[-1]
                    elif data.startswith("New game"):
                        self.white_bottom = "white" in data
                        self.set_status(data.strip(), GOOD)
                    elif "thinking" in data:
                        self.set_status(data.strip().capitalize(), OK)
                    elif "opponent" in data:
                        self.set_status("Your move was seen - thinking", OK)
                elif kind == "move":
                    self.show_move(data)
                elif kind == "stopped":
                    self.log(data)
                    self.set_status(data, BAD if data.startswith("Error") else MUTED)
                    self.start_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    self.side_box.config(state="readonly")
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    # ---- display -------------------------------------------------------- #
    def show_move(self, m):
        share = white_share(m["eval_cp"], m["mate"])
        self.draw_bar(share)
        self.eval_lbl.config(text=eval_text(m["eval_cp"], m["mate"]))
        self.win_lbl.config(text=f"White {share:.0%}  ·  Black {1 - share:.0%}")
        dots = "." if m["color"] == "white" else "..."
        think = f"   ({m['think']:.1f}s)" if m["think"] else ""
        self.move_lbl.config(text=f"{m['number']}{dots} {m['san']}{think}")
        if m["human"] is not None:
            p = m["human"]
            color = GOOD if p > 0.5 else OK if p >= 0.10 else WARN if p >= 0.03 else BAD
            self.hi_bar.delete("all")
            self.hi_bar.create_rectangle(0, 0, max(2, 120 * p), 10, fill=color, width=0)
            self.hi_lbl.config(text=f"{p:.0%}  {m['human_label']}", foreground=color)
        self.set_status("Waiting for the opponent", GOOD)

    def draw_bar(self, share):
        """Vertical eval bar; our colour at the bottom, like on chess.com."""
        c, w, h = self.bar, 26, 150
        c.delete("all")
        white_h = h * share
        if self.white_bottom:
            c.create_rectangle(0, 0, w, h - white_h, fill=BLACK_BAR, width=0)
            c.create_rectangle(0, h - white_h, w, h, fill=WHITE_BAR, width=0)
        else:
            c.create_rectangle(0, 0, w, white_h, fill=WHITE_BAR, width=0)
            c.create_rectangle(0, white_h, w, h, fill=BLACK_BAR, width=0)
        c.create_line(0, h / 2, w, h / 2, fill="#888888")
        c.create_rectangle(0, 0, w - 1, h - 1, outline="#55575e")

    def set_status(self, text, color=TEXT):
        self.status.config(text=text, foreground=color)

    def log(self, text):
        self.log_box.config(state="normal")
        self.log_box.insert("end", text + "\n")
        if int(self.log_box.index("end-1c").split(".")[0]) > 300:
            self.log_box.delete("1.0", "50.0")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def toggle_top(self):
        self.root.attributes("-topmost", self.on_top.get())

    def close(self):
        self.stop.set()
        engine.close_engines()
        self.root.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # crisp text, and pixel coordinates that match mss
    except Exception:
        pass
    root = tk.Tk()
    BotUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
