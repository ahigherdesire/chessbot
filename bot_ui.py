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
PLAY_RATINGS = ["800", "1000", "1200", "1400", "1500", "1600", "1800", "2000", "2200", "2500"]
LABEL_CHARS = 13   # setting names get the same width in every section, so the sections line up
GRAPH_W, GRAPH_H = 300, 130
PAD_L, PAD_R, PAD_T, PAD_B = 26, 6, 6, 6   # room around the plot for the axis labels
GRAPH_MOVES = 20   # the graphs' x axis spans at least this many of our moves
HOVER_HINT = "Point at a graph to see that move"
PREMOVE_NOTE = ("On. Premoves are guesses and can fail: the site may drop or ignore one, "
                "or play it after a reply the bot did not check (see README).")


def win_chance(value):
    """Win chance in % for the side with this value (centipawns, mates as +-engine.MATE)."""
    v = max(-1000, min(1000, value))
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * v)) - 1)


def move_accuracy(best, played):
    """Lichess's accuracy for one move: 100 for the best move, less the more win chance it gives away."""
    lost = max(0.0, win_chance(best) - win_chance(played))
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * lost) - 3.1669))


def accuracy_kind(acc):
    """Lichess's names: giving away 5 / 10 / 15 points of win chance is an inaccuracy / mistake / blunder."""
    return ("good", GOOD) if acc >= 80 else ("inaccuracy", OK) if acc >= 64 else \
        ("mistake", WARN) if acc >= 50 else ("blunder", BAD)


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
        self.human_rating = 1500

        root.title("Chess Bot")
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self._style()

        self.main = ttk.Frame(root, padding=10)
        self.main.grid(sticky="nsew")
        self._settings(self.main)
        self._premove_settings(self.main)
        self._controls(self.main)
        self._game(self.main)
        self._side(self.main)
        self.reset_game_stats()
        self.apply()

        self.on_top.set(True)
        self.toggle_top()
        self.layout(top=40)
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

        # engine: Stockfish (with accuracy / human moves); human: Maia plays like a player of that rating
        self.play_as = tk.StringVar(value="engine")
        self.play_rating = tk.StringVar(value="1500")
        like = ttk.Frame(f)
        self.rating_box = ttk.Combobox(like, values=PLAY_RATINGS, textvariable=self.play_rating, width=6)
        self._inline(like, "human rated", self.rating_box, "")
        self._row(f, 0, "Play as", ttk.Combobox(f, values=["engine", "human"], textvariable=self.play_as,
                                               state="readonly", width=10))
        like.grid(row=0, column=2, sticky="w", padx=(6, 0))
        self.accuracy = tk.DoubleVar(value=100)
        acc_box = self._slider(f, self.accuracy)
        self._row(f, 1, "Accuracy", acc_box)
        self.think = tk.DoubleVar(value=1.0)
        self._row(f, 2, "Think time", ttk.Spinbox(f, from_=0.1, to=10, increment=0.1, textvariable=self.think,
                                                 width=8), "seconds of engine search")
        self.timing = tk.StringVar(value="off")
        self._row(f, 3, "Human timing", ttk.Combobox(f, values=TIMING, textvariable=self.timing, state="readonly",
                                                    width=10), "fast on easy moves, slow on hard")
        self.max_think = tk.DoubleVar(value=30)
        self._row(f, 4, "Max think", ttk.Spinbox(f, from_=1, to=120, increment=1, textvariable=self.max_think,
                                                width=8), "seconds, caps human timing")
        self.rating = tk.IntVar(value=1500)
        self._row(f, 5, "Human index", ttk.Spinbox(f, from_=600, to=2800, increment=100, textvariable=self.rating,
                                                  width=8), "rating to compare with")
        self.human_moves = tk.DoubleVar(value=0)
        hm_box = ttk.Spinbox(f, from_=0, to=100, increment=5, textvariable=self.human_moves, width=8)
        self._row(f, 6, "Human moves", hm_box, "% min human chance, 0 = off")
        self.acc_label = acc_box.label
        self.engine_only = [acc_box.scale, hm_box]   # playing as a human picks its own moves: these do nothing then
        # side and click/drag share a row to keep the window short
        self.side = tk.StringVar(value="auto")
        self.side_box = ttk.Combobox(f, values=["auto", "white", "black"], textvariable=self.side,
                                     state="readonly", width=10)
        self.move_with = tk.StringVar(value="click")
        by = ttk.Frame(f)
        self._inline(by, "move by", ttk.Combobox(by, values=["click", "drag"], textvariable=self.move_with,
                                                 state="readonly", width=6), "")
        self._row(f, 7, "Side", self.side_box)
        by.grid(row=7, column=2, sticky="w", padx=(6, 0))
        checks = ttk.Frame(f)
        checks.grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.dry_run = tk.BooleanVar(value=False)
        ttk.Checkbutton(checks, text="Dry run", variable=self.dry_run).pack(side="left")
        self.natural_mouse = tk.BooleanVar(value=True)
        ttk.Checkbutton(checks, text="Natural mouse", variable=self.natural_mouse).pack(side="left", padx=(12, 0))
        self.on_top = tk.BooleanVar()
        ttk.Checkbutton(checks, text="Keep on top", variable=self.on_top,
                        command=self.toggle_top).pack(side="left", padx=(12, 0))
        self.show_graphs = tk.BooleanVar(value=True)
        ttk.Checkbutton(checks, text="Graphs", variable=self.show_graphs,
                        command=self.layout).pack(side="left", padx=(12, 0))

        for var in (self.play_as, self.play_rating, self.accuracy, self.think, self.timing, self.max_think,
                    self.rating, self.human_moves, self.move_with, self.dry_run, self.natural_mouse):
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
        box.scale, box.label = scale, label
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
        self.pm_lbl = ttk.Label(f, text="off", style="Panel.TLabel", wraplength=480)
        self.pm_lbl.grid(row=7, column=0, columnspan=2, sticky="w")
        self.white_bottom = True
        self.draw_bar(0.5)

    def _side(self, parent):
        """Right-hand column: the eval and accuracy graphs, then the log. Placed by layout()."""
        f = self.graph_col = ttk.Frame(parent)
        f.columnconfigure(0, weight=1)
        f.rowconfigure((1, 3), weight=2)       # a taller window grows the graphs and the log
        f.rowconfigure(7, weight=1)
        ttk.Label(f, text="EVAL", style="Head.TLabel").grid(row=0, column=0, sticky="w")
        self.eval_graph = self._graph(f, 1)
        head = ttk.Frame(f)
        head.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(head, text="ACCURACY", style="Head.TLabel").pack(side="left")
        self.acc_avg = ttk.Label(head, text="", style="Head.TLabel")
        self.acc_avg.pack(side="right")
        self.acc_graph = self._graph(f, 3)
        self.graph_info = ttk.Label(f, text=HOVER_HINT, foreground=MUTED)
        self.graph_info.grid(row=4, column=0, sticky="w", pady=(4, 0))
        # the log belongs to the main frame: layout() puts it in this column or under the game
        self.log_head = ttk.Label(parent, text="LOG", style="Head.TLabel")
        self.log_box = tk.Text(parent, width=48, height=7, bg=PANEL, fg=MUTED, relief="flat", wrap="word",
                               font=("Consolas", 8), state="disabled", padx=6, pady=4)

    def _graph(self, parent, row):
        c = tk.Canvas(parent, width=GRAPH_W, height=GRAPH_H, bg=PANEL, highlightthickness=0)
        c.grid(row=row, column=0, sticky="nsew", pady=(2, 0))
        c.bind("<Configure>", lambda _: self.draw_graphs())
        c.bind("<Motion>", self.hover)
        c.bind("<Leave>", self.unhover)
        return c

    def layout(self, top=None):
        """Graphs on: a second column with the graphs and the log. Off: one narrow column,
        the log under the game. Either way the window fits its contents again, at the
        right edge of the screen; the graph column (or the log) takes any resizing."""
        m, graphs = self.main, self.show_graphs.get()
        if graphs:
            self.graph_col.grid(row=0, column=1, rowspan=9, sticky="nsew", padx=(16, 0))
            self.log_head.grid(in_=self.graph_col, row=6, column=0, sticky="w", pady=(8, 0))
            self.log_box.grid(in_=self.graph_col, row=7, column=0, sticky="nsew", pady=(2, 0))
        else:
            self.graph_col.grid_remove()
            self.log_head.grid(in_=m, row=8, column=0, sticky="w")
            self.log_box.grid(in_=m, row=9, column=0, sticky="nsew", pady=(2, 0))
        m.columnconfigure(0, weight=0 if graphs else 1)
        m.columnconfigure(1, weight=1 if graphs else 0)
        m.rowconfigure(8, weight=1 if graphs else 0)   # empty on the left: the game panel stays put
        m.rowconfigure(9, weight=0 if graphs else 1)
        root = self.root
        root.minsize(1, 1)
        root.geometry("")                      # drop any size the user dragged to
        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        # the graph column may shrink to about half; nothing else may be cut off
        root.minsize(w - (self.graph_col.winfo_reqwidth() // 2 if graphs else 0), h)
        y = top if top is not None else root.winfo_y()
        root.geometry(f"{w}x{h}+{root.winfo_screenwidth() - w - 20}+{y}")

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
        human = self.play_as.get() == "human"
        like = self.play_rating.get().strip()
        if like.isdigit() and 400 <= int(like) <= 3000:
            self.human_rating = int(like)  # anything else is half-typed: keep the previous rating
        a.play_like = self.human_rating if human else None
        for w in self.engine_only:
            w.state(["disabled"] if human else ["!disabled"])
        self.rating_box.state(["!disabled"] if human else ["disabled"])
        self.acc_label.config(foreground=MUTED if human else TEXT)
        a.mouse = "natural" if self.natural_mouse.get() else "instant"
        a.human = None if self.timing.get() == "off" else self.timing.get()
        a.premove = self.premove_mode.get() != "off"
        a.opponent_engine = self.premove_mode.get() == "vs engine"
        for w in self.premove_only:
            w.state(["!disabled"] if a.premove else ["disabled"])
        if not self.running():
            self.pm_lbl.config(text=PREMOVE_NOTE if a.premove else "off", foreground=WARN if a.premove else MUTED)
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
                        self.draw_graphs()
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
        point = {"number": m["number"], "label": f"{m['number']}{dots} {m['san']}", "share": share,
                 "eval": eval_text(m["eval_cp"], m["mate"]), "premove": m.get("premove"),
                 "acc": None if m.get("value") is None else move_accuracy(m["best_value"], m["value"])}
        if self.history and m["number"] < self.history[-1]["number"]:
            self.history.clear()                   # a new game started
        if self.history and m["number"] == self.history[-1]["number"]:
            self.history[-1] = point               # the same move clicked again
        else:
            self.history.append(point)
            self.our_moves += 1
        self.draw_graphs()
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
        self.history = []              # one point per move we played: eval after it, its accuracy
        self.draw_graphs()

    def show_premove(self, event, text):
        if event == "queued":
            self.pm_lbl.config(text=f"Queued (may not land): {text}", foreground=OK)
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

    # ---- graphs --------------------------------------------------------- #
    @staticmethod
    def size(c):
        """A canvas's size; the requested one until it is on screen."""
        w, h = c.winfo_width(), c.winfo_height()
        return (w, h) if w > 1 else (int(c["width"]), int(c["height"]))

    def gx(self, i):
        """x of point i on both graphs: 0 = the start position, k = after our k-th move."""
        w = self.size(self.eval_graph)[0]
        return PAD_L + (w - PAD_L - PAD_R) * i / max(len(self.history), GRAPH_MOVES)

    def draw_graphs(self):
        self.draw_eval_graph()
        self.draw_accuracy_graph()

    def draw_eval_graph(self):
        """White's win chance after each of our moves, white below the line when we are white
        (like the eval bar)."""
        c = self.eval_graph
        w, h = self.size(c)
        top, bot, right = PAD_T, h - PAD_B, w - PAD_R
        c.delete("all")

        def y(share):
            return bot - (bot - top) * (share if self.white_bottom else 1 - share)

        c.create_rectangle(PAD_L, top, right, bot, fill=BLACK_BAR, width=0)
        pts = [(self.gx(0), y(0.5))] + [(self.gx(i), y(p["share"])) for i, p in enumerate(self.history, 1)]
        if len(pts) > 1:
            base = bot if self.white_bottom else top
            c.create_polygon([(pts[0][0], base)] + pts + [(pts[-1][0], base)], fill=WHITE_BAR, outline="")
        for share, text in ((0.75, "+3"), (0.5, "0"), (0.25, "-3")):   # 75% to win is about +3
            c.create_line(PAD_L, y(share), right, y(share), fill="#6b6d73", dash=(2, 3))
            c.create_text(PAD_L - 5, y(share), text=text, anchor="e", fill=MUTED, font=("Segoe UI", 7))
        for (x, py), p in zip(pts[1:], self.history):
            if p["premove"]:
                c.create_oval(x - 2, py - 2, x + 2, py + 2, fill=OK, width=0)

    def draw_accuracy_graph(self):
        """A bar per move (coloured like lichess: good, inaccuracy, mistake, blunder) and the
        running average as a line."""
        c = self.acc_graph
        w, h = self.size(c)
        top, bot, right = PAD_T, h - PAD_B, w - PAD_R
        c.delete("all")

        def y(acc):
            return bot - (bot - top) * acc / 100

        c.create_rectangle(PAD_L, top, right, bot, fill=BG, width=0)
        for acc in (100, 50, 0):
            c.create_line(PAD_L, y(acc), right, y(acc), fill="#45474d", dash=(2, 3))
            c.create_text(PAD_L - 5, y(acc), text=str(acc), anchor="e", fill=MUTED, font=("Segoe UI", 7))
        half = max(1.0, (self.gx(1) - self.gx(0)) * 0.3)
        rated, line = [], []
        for i, p in enumerate(self.history, 1):
            if p["acc"] is None:
                continue
            x = self.gx(i)
            c.create_rectangle(x - half, y(p["acc"]), x + half, bot, fill=accuracy_kind(p["acc"])[1], width=0)
            rated.append(p["acc"])
            line.append((x, y(sum(rated) / len(rated))))
        if len(line) > 1:
            c.create_line(line, fill=TEXT, width=1.5)
        self.acc_avg.config(text=f"game average {sum(rated) / len(rated):.0f}%" if rated else "")

    def hover(self, event):
        """Show the move under the mouse, with a line across both graphs."""
        step = self.gx(1) - self.gx(0)
        i = max(0, min(len(self.history), round((event.x - PAD_L) / step)))
        for c in (self.eval_graph, self.acc_graph):
            c.delete("cursor")
            c.create_line(self.gx(i), PAD_T, self.gx(i), self.size(c)[1] - PAD_B, fill=MUTED, tags="cursor")
        if i == 0:
            self.graph_info.config(text="Start position   0.0", foreground=TEXT)
            return
        p = self.history[i - 1]
        text = f"{p['label']}   {p['eval']}"
        color = TEXT
        if p["acc"] is not None:
            kind, color = accuracy_kind(p["acc"])
            text += f"   accuracy {p['acc']:.0f}% ({kind})"
        self.graph_info.config(text=text + ("   premove" if p["premove"] else ""), foreground=color)

    def unhover(self, _event):
        for c in (self.eval_graph, self.acc_graph):
            c.delete("cursor")
        self.graph_info.config(text=HOVER_HINT, foreground=MUTED)

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
