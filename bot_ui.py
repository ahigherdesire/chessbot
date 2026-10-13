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
import premove

BG, PANEL, TEXT, MUTED = "#1e1f22", "#2b2d31", "#e8e8e8", "#9a9ca3"
WHITE_BAR, BLACK_BAR = "#f0f0f0", "#111214"
GOOD, OK, WARN, BAD = "#5fb760", "#c9b84a", "#d98c3f", "#d0544b"
TIMING = ["off", "bullet", "blitz", "rapid", "classical"]
PREMOVE = ["off", "on", "vs engine"]
LABEL_CHARS = 13   # setting names get the same width in every section, so the sections line up


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
        self._premove_settings(main)
        self._controls(main)
        self._game(main)
        self._log(main)
        self.apply()

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
        ttk.Label(parent, text=label, width=LABEL_CHARS).grid(row=r, column=0, sticky="w", pady=2)
        widget.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=2)
        if hint:
            ttk.Label(parent, text=hint, foreground=MUTED).grid(row=r, column=2, sticky="w", padx=(6, 0))

    def _settings(self, parent):
        ttk.Label(parent, text="SETTINGS", style="Head.TLabel").grid(row=0, column=0, sticky="w")
        f = ttk.Frame(parent)
        f.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        f.columnconfigure(1, weight=1)

        self.accuracy = tk.DoubleVar(value=100)
        self._row(f, 0, "Accuracy", self._slider(f, self.accuracy))

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
        self.human_moves = tk.DoubleVar(value=0)
        self._row(f, 5, "Human moves", ttk.Spinbox(f, from_=0, to=100, increment=5, textvariable=self.human_moves,
                                                  width=8), "% min human chance, 0 = off")
        self.side = tk.StringVar(value="auto")
        self.side_box = ttk.Combobox(f, values=["auto", "white", "black"], textvariable=self.side,
                                     state="readonly", width=10)
        self._row(f, 6, "Play as", self.side_box, "auto = side at the bottom")
        self.move_with = tk.StringVar(value="click")
        self._row(f, 7, "Move by", ttk.Combobox(f, values=["click", "drag"], textvariable=self.move_with,
                                               state="readonly", width=10))
        checks = ttk.Frame(f)
        checks.grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.dry_run = tk.BooleanVar(value=False)
        ttk.Checkbutton(checks, text="Dry run (hover, never click)", variable=self.dry_run).pack(side="left")
        self.on_top = tk.BooleanVar()
        ttk.Checkbutton(checks, text="Keep on top", variable=self.on_top,
                        command=self.toggle_top).pack(side="left", padx=(12, 0))

        for var in (self.accuracy, self.think, self.timing, self.max_think, self.rating, self.human_moves,
                    self.move_with, self.dry_run):
            var.trace_add("write", lambda *_: self.apply())

    def _premove_settings(self, parent):
        ttk.Label(parent, text="PREMOVES", style="Head.TLabel").grid(row=2, column=0, sticky="w")
        f = ttk.Frame(parent)
        f.grid(row=3, column=0, sticky="ew", pady=(2, 8))
        f.columnconfigure(1, weight=1)

        self.premove_mode = tk.StringVar(value="off")
        self._row(f, 0, "Premove", ttk.Combobox(f, values=PREMOVE, textvariable=self.premove_mode, state="readonly",
                                               width=10), "when their reply is 70%+ likely")
        self.premove_streak = tk.IntVar(value=2)
        streak_box = ttk.Spinbox(f, from_=1, to=10, increment=1, textvariable=self.premove_streak, width=8)
        self._row(f, 1, "Max in a row", streak_box, "then think a move over")
        # clock and low time share a row to keep the window short
        self.clock = tk.StringVar(value="")
        self.low_time = tk.DoubleVar(value=20)
        low = ttk.Frame(f)
        self._inline(low, "e.g. 3+2   low under",
                     ttk.Spinbox(low, from_=5, to=120, increment=5, textvariable=self.low_time, width=4), "s")
        self._row(f, 2, "Clock", ttk.Entry(f, textvariable=self.clock, width=10))
        low.grid(row=2, column=2, sticky="w", padx=(6, 0))
        self.premove_only = [streak_box]   # greyed out while premoves are off

        for var in (self.premove_mode, self.premove_streak, self.clock, self.low_time):
            var.trace_add("write", lambda *_: self.apply())

    def _inline(self, parent, before, widget, after):
        """Muted text around a small widget, in one line."""
        ttk.Label(parent, text=before, foreground=MUTED).pack(side="left")
        widget.pack(side="left", padx=4)
        ttk.Label(parent, text=after, foreground=MUTED).pack(side="left")

    def _slider(self, parent, var):
        """A 0-100% slider with its value beside it. Returns the frame holding both."""
        box = ttk.Frame(parent)
        label = ttk.Label(box, text=f"{var.get():.0f}%", width=5)
        scale = ttk.Scale(box, from_=0, to=100, variable=var, length=150,
                          command=lambda _: label.config(text=f"{var.get():.0f}%"))
        scale.pack(side="left")
        label.pack(side="left", padx=(6, 0))
        return box

    def _controls(self, parent):
        f = ttk.Frame(parent)
        f.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        f.columnconfigure((0, 1), weight=1)
        self.start_btn = ttk.Button(f, text="Start", style="Start.TButton", command=self.start)
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.stop_btn = ttk.Button(f, text="Stop", style="Stop.TButton", command=self.stop_bot, state="disabled")
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.status = ttk.Label(parent, text="Stopped", foreground=MUTED)
        self.status.grid(row=5, column=0, sticky="w", pady=(0, 8))

    def _game(self, parent):
        ttk.Label(parent, text="GAME", style="Head.TLabel").grid(row=6, column=0, sticky="w")
        f = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        f.grid(row=7, column=0, sticky="ew", pady=(2, 8))
        f.columnconfigure(1, weight=1)

        self.bar = tk.Canvas(f, width=26, height=150, bg=PANEL, highlightthickness=0)
        self.bar.grid(row=0, column=0, rowspan=6, sticky="ns", padx=(0, 12))

        self.eval_lbl = ttk.Label(f, text="0.0", style="Big.TLabel")
        self.eval_lbl.grid(row=0, column=1, sticky="w")
        self.clock_lbl = ttk.Label(f, text="", style="Muted.TLabel")   # same row as the eval, right side
        self.clock_lbl.grid(row=0, column=1, sticky="e")
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

        head = ttk.Frame(f, style="Panel.TFrame")
        head.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(head, text="Premove", style="Muted.TLabel").pack(side="left")
        self.pm_count = ttk.Label(head, text="", style="Muted.TLabel")
        self.pm_count.pack(side="right")
        self.pm_lbl = ttk.Label(f, text="off", style="Panel.TLabel", wraplength=300)
        self.pm_lbl.grid(row=7, column=0, columnspan=2, sticky="w")
        self.white_bottom = True
        self.draw_bar(0.5)
        self.reset_game_stats()

    def _log(self, parent):
        ttk.Label(parent, text="LOG", style="Head.TLabel").grid(row=8, column=0, sticky="w")
        self.log_box = tk.Text(parent, width=48, height=7, bg=PANEL, fg=MUTED, relief="flat", wrap="word",
                               font=("Consolas", 8), state="disabled", padx=6, pady=4)
        self.log_box.grid(row=9, column=0, sticky="ew", pady=(2, 0))

    # ---- settings -> bot ------------------------------------------------ #
    def apply(self):
        """Copy the controls into the args the bot reads every move."""
        a = self.args
        try:
            a.accuracy = float(self.accuracy.get())
            a.time = max(0.1, float(self.think.get()))
            a.max_think = max(1.0, float(self.max_think.get()))
            a.human_index = int(self.rating.get())
            a.human_moves = min(100.0, max(0.0, float(self.human_moves.get())))
            a.low_time = max(1.0, float(self.low_time.get()))
            a.premove_streak = max(1, int(self.premove_streak.get()))
        except (tk.TclError, ValueError):
            return                     # a half-typed number: keep the previous value
        a.human = None if self.timing.get() == "off" else self.timing.get()
        a.premove = self.premove_mode.get() != "off"
        a.opponent_engine = self.premove_mode.get() == "vs engine"
        for w in self.premove_only:
            w.state(["!disabled"] if a.premove else ["disabled"])
        if not self.running():
            self.pm_lbl.config(text="on" if a.premove else "off", foreground=TEXT if a.premove else MUTED)
        clock = self.clock.get().strip()
        if not clock or premove.Clock.valid(clock):
            a.clock = clock or None
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
        self.reset_game_stats()
        self.set_status("Looking for the board...", OK)
        self.log("Started. Keep this window off the board.")
        self.thread = threading.Thread(target=self.run_bot, daemon=True)
        self.thread.start()

    def run_bot(self):
        put = self.events.put
        try:
            bot = ap.AutoPlayer(self.args, log=lambda msg: put(("log", msg)),
                                on_move=lambda m: put(("move", m)), stop=self.stop,
                                on_premove=lambda event, text: put(("premove", (event, text))))
            self.bot = bot
            bot.run()
            put(("stopped", "Stopped"))
        except bc.pyautogui.FailSafeException:
            put(("stopped", "Stopped (mouse in a screen corner)"))
        except Exception as e:          # show it rather than dying silently
            put(("stopped", f"Error: {e}"))

    def running(self):
        return self.thread is not None and self.thread.is_alive()

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
                        self.reset_game_stats()
                        self.set_status(data.strip(), GOOD)
                    elif "thinking" in data:
                        self.set_status(data.strip().capitalize(), OK)
                    elif "opponent" in data:
                        self.set_status("Your move was seen - thinking", OK)
                elif kind == "move":
                    self.show_move(data)
                elif kind == "premove":
                    self.show_premove(*data)
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
        think = "   (premove)" if m.get("premove") else f"   ({m['think']:.1f}s)" if m["think"] else ""
        self.move_lbl.config(text=f"{m['number']}{dots} {m['san']}{think}")
        if m["human"] is not None:
            p = m["human"]
            color = GOOD if p > 0.5 else OK if p >= 0.10 else WARN if p >= 0.03 else BAD
            self.hi_bar.delete("all")
            self.hi_bar.create_rectangle(0, 0, max(2, 120 * p), 10, fill=color, width=0)
            self.hi_lbl.config(text=f"{p:.0%}  {m['human_label']}", foreground=color)
        self.our_moves += 1
        self.show_premove_count()
        if m.get("clock") is not None:
            low = m["clock"] < self.args.low_time
            left = f"{int(m['clock'] // 60)}:{int(m['clock'] % 60):02d}"
            self.clock_lbl.config(text=f"clock ~{left}" + ("  low on time" if low else ""),
                                  foreground=WARN if low else MUTED)
        self.set_status("Waiting for the opponent", GOOD)

    def reset_game_stats(self):
        self.our_moves = 0
        self.pm_stats = {"played": 0, "dropped": 0}
        self.show_premove_count()
        self.clock_lbl.config(text="")

    def show_premove(self, event, text):
        if event == "queued":
            self.pm_lbl.config(text=f"Queued: {text}", foreground=OK)
            self.set_status("Premove queued - waiting for the opponent", GOOD)
            return
        if event == "skipped":   # decided against it: show why, but it is not a premove
            self.pm_lbl.config(text=text, foreground=MUTED)
            return
        self.pm_stats[event] += 1
        self.pm_lbl.config(text=text[0].upper() + text[1:], foreground=GOOD if event == "played" else WARN)
        self.show_premove_count()

    def show_premove_count(self):
        played, dropped = self.pm_stats["played"], self.pm_stats["dropped"]
        share = f"  ·  {played / self.our_moves:.0%} of our moves" if self.our_moves else ""
        self.pm_count.config(text=f"{played} played  ·  {dropped} dropped{share}" if played + dropped else "")

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
