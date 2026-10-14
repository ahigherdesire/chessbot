"""Play a game on screen: watch the board, and when it is our turn, think and click the move.

Every click goes through board_coords.BoardMap, so it lands on the square centres
that `python board_coords.py map` prints (monitor offset included).

  python auto_play.py                      # play the side at the bottom of the board
  python auto_play.py --time 1 --accuracy 85
  python auto_play.py --once               # wait for our turn, play one move, exit
  python auto_play.py --once --turn-now    # play one move right now
  python auto_play.py --dry-run            # think and hover the squares, never click
  python auto_play.py --human blitz        # take human-like time: fast on easy moves, slow on hard ones
  python auto_play.py --premove --clock 3+2   # premove predictable replies, and everything when low on time

Move the mouse into a screen corner (or press Ctrl+C) to stop.
"""
import argparse
import random
import sys
import threading
import time

import chess

import board_coords as bc
import board_reader as br
import engine
import human_index
import maia
import mouse
import premove

START = chess.Board().board_fen()
PROMO_ORDER = [chess.QUEEN, chess.KNIGHT, chess.ROOK, chess.BISHOP]  # chess.com and lichess pickers


def play_move(bm, move, drag=False, delay=0.15, jitter=3, auto_queen=False, natural=False, speed=1.0):
    """Click `move` on the board described by BoardMap `bm`. natural: move the mouse like
    a hand (mouse.py), at `speed` (> 1 quicker)."""
    frm, to = chess.square_name(move.from_square), chess.square_name(move.to_square)
    bm.move(frm, to, drag=drag, delay=delay, jitter=jitter, natural=natural, speed=speed)
    if move.promotion and not (auto_queen and move.promotion == chess.QUEEN):
        # the picker opens on the promotion square and runs Q, N, R, B towards the board centre
        time.sleep(0.3)
        x, y = bm.center(to)
        k = PROMO_ORDER.index(move.promotion)
        y += round(k * bm.size) if bm[to].top < bm.ys[4] else -round(k * bm.size)
        if natural:
            mouse.glide(bc.pyautogui, x, y, bm.size, speed)
            mouse.press(bc.pyautogui, speed)
        else:
            bc.pyautogui.moveTo(x, y, duration=0.1, _pause=False)
            bc.pyautogui.click(x, y)


PACES = {"bullet": 0.25, "blitz": 0.5, "rapid": 1.0, "classical": 2.0}
PREMOVE_GRACE = 1.5     # seconds to wait for the site to play our premove once their reply is on screen
LOW_TIME_THINK = 0.3    # engine seconds per move when low on time
MOUSE_SPEED = {"bullet": 2.2, "blitz": 1.5, "rapid": 1.15}   # natural mouse: quicker in faster games


def think_time(board, top, opp_move=None, prev_eval=None, moves=0, pace=1.0, rng=random, human_top=None):
    """How long a human would take here, in seconds, and why.

    board: position with us to move. top: engine.top_moves() for it, best first.
    opp_move: the opponent's last move. prev_eval: our eval (centipawns) after our
    previous move. moves: how many moves we have already played this game.
    human_top: when playing as a human, Maia's chance for the most popular move
    (how obvious the position is to a person, rather than to an engine)."""
    n = board.legal_moves.count()
    if n == 1:
        return rng.uniform(0.3, 0.8), "only move"
    if moves < 5:
        return pace * rng.uniform(0.4, 1.8), "opening"
    if human_top is not None and human_top >= 0.7:
        return pace * rng.uniform(0.5, 1.5), "obvious to a human"
    if not top:   # no Stockfish: judge from the position alone
        d = 0.5 * min(n, 40) / 40 + 0.5 * min(_forcing(board), 6) / 6
        return max(0.3, pace * (1 + 10 * d * d) * rng.lognormvariate(0, 0.3)), "thinking"

    best = top[0]
    gap = best["value"] - top[1]["value"] if len(top) > 1 else 1000
    if best["mate"] is not None and 0 < best["mate"] <= 2:
        return pace * rng.uniform(0.8, 2.5), "sees the mate"
    if opp_move:
        def recaptures(c):
            return board.is_capture(c["move"]) and c["move"].to_square == opp_move.to_square
        others = [c for c in top if not recaptures(c)]
        if recaptures(best) and (not others or best["value"] - others[0]["value"] >= 150):
            return pace * rng.uniform(0.5, 1.5), "recapture"
    if gap >= 300:
        return pace * rng.uniform(0.8, 2.5), "obvious move"

    # several moves almost as good is a hard choice - unless they all still win easily
    closeness = 0 if len(top) > 1 and top[1]["value"] > 400 else 1 - min(gap, 200) / 200
    if human_top is not None and human_top < 0.4:
        closeness = max(closeness, (0.4 - human_top) / 0.3)   # people are split between several moves
    complexity = min(n, 40) / 40
    tactics = min(_forcing(board), 6) / 6                # captures and checks to calculate
    swing = 0 if prev_eval is None else min(abs(best["value"] - prev_eval), 300) / 300  # a surprise
    d = 0.4 * closeness + 0.15 * complexity + 0.3 * tactics + 0.15 * swing
    t = pace * (1 + 14 * d * d) * rng.lognormvariate(0, 0.3)
    if abs(best["value"]) > 600:
        t *= 0.6                                         # the game is decided: play quicker
    return max(0.3, t), "easy" if d < 0.3 else "thinking" if d < 0.6 else "long think"


def _value(r):
    """An engine result's score in centipawns for the side to move (mates as +-MATE)."""
    if r.get("mate") is not None:
        return engine.MATE if r["mate"] > 0 else -engine.MATE
    return r.get("score_cp")


def _forcing(board):
    return sum(1 for m in board.legal_moves if board.is_capture(m) or board.gives_check(m))


def park(bm, natural=False, speed=1.0):
    """Move the mouse just right of the board so it does not cover any pieces."""
    x, y = bm.xs[8] + round(bm.size * 0.4), bm.ys[4]
    if natural:   # somewhere off the board's right edge, not the same pixel every time
        x, y = x + round(random.uniform(-0.2, 0.6) * bm.size), y + round(random.gauss(0, bm.size))
        mouse.glide(bc.pyautogui, x, y, bm.size, speed)
    else:
        bc.pyautogui.moveTo(x, y, duration=0.1, _pause=False)


class AutoPlayer:
    def __init__(self, args, log=print, on_move=None, stop=None, on_premove=None):
        """log: where status text goes. on_move: called with a dict after each move we play.
        stop: a threading.Event that ends run() (and any wait) when set.
        on_premove: called with (event, text) when a premove is "queued", "played" or "dropped"."""
        self.args = args
        self.log = log
        self.on_move = on_move
        self.on_premove = on_premove or (lambda event, text: None)
        self.stop = stop or threading.Event()
        self.templates = br.load_templates(args.templates)
        self.grid = None              # board position on the screenshot, found once and reused
        self.white_bottom = None if args.side is None else args.side == "white"
        self.warned = None
        self.no_board = False
        self.clock = None             # our estimated time left, when --clock is set
        self.new_game()

    def new_game(self):
        self.rights = chess.BB_CORNERS  # castling rights still possible; only ever shrink
        self.played_from = None        # placement we last moved from
        self.expected = None           # placement right after our move
        self.game = None               # full game state right after our move (castling, en passant)
        self.game_before = None        # the board we last moved from, for re-clicking
        self.played_at = 0.0
        self.moves = 0                 # moves we have played this game
        self.opp_move = None           # the opponent's last move, when we know it
        self.last_eval = None          # our eval after our last move (centipawns)
        self.premove = None            # the premove queued on the site, waiting for their reply
        self.premove_streak = 0        # premoves played in a row since we last thought for real
        if self.clock:
            self.clock.reset()

    # ---- vision --------------------------------------------------------- #
    def read(self):
        """Screenshot and read the board. Returns (info, BoardMap) or None."""
        img, origin = bc.grab(self.args.monitor)
        try:
            if self.grid is None:
                self.grid = br.find_board(img)
            info = br.read_board(img, self.templates, self.white_bottom, grid=self.grid)
            board = info["board"]
            if (self.args.side is None and self.white_bottom is not None
                    and len(board.piece_map()) == 32 and board.board_fen() != START):
                # a new game with the colours swapped shows the start position upside down
                flipped = br.read_board(img, self.templates, not self.white_bottom, grid=self.grid)
                if flipped["board"].board_fen() == START:
                    self.white_bottom = not self.white_bottom
                    self.log(f"New game - now playing {'white' if self.white_bottom else 'black'}.")
                    self.new_game()
                    info = flipped
        except br.BoardNotFound:
            return None
        if self.white_bottom is None:
            self.white_bottom = info["white_bottom"]
            side = "white" if self.white_bottom else "black"
            self.log(f"Board found at {self.grid['x'] + origin[0]:.0f},{self.grid['y'] + origin[1]:.0f} "
                  f"({self.grid['size']:.0f}px squares). Playing {side}.")
        return info, bc.BoardMap(self.grid, self.white_bottom, origin)

    def read_stable(self):
        """Read until two screenshots in a row agree (lets piece animations finish)."""
        prev = None
        for _ in range(20):
            got = self.read()
            if got is None:
                self.grid = None   # board moved or vanished: search again next time
                if not self.no_board:
                    self.log("No chess board visible on screen - waiting for one.")
                    self.no_board = True
                return None
            self.no_board = False
            if prev is not None and got[0]["board"].board_fen() == prev:
                return got
            prev = got[0]["board"].board_fen()
            time.sleep(self.args.poll)
        return None

    # ---- turn logic ----------------------------------------------------- #
    def decide(self, info):
        """Return the board to move from if it is our turn, else None."""
        board = info["board"]
        me = chess.WHITE if self.white_bottom else chess.BLACK
        placement = board.board_fen()
        if placement == self.expected:
            return None                        # still waiting for the opponent
        if placement == self.played_from:
            # our move did not register: try again after a pause
            return self.game_before if time.time() - self.played_at > self.args.retry else None
        if self.game is not None:
            # it only counts as their move if one legal move gets from our position to the screen
            for reply in self.game.legal_moves:
                self.game.push(reply)
                if self.game.board_fen() == placement:
                    after = self.game.copy()
                    self.game.pop()
                    self.log(f"   opponent: {self.game.san(reply)}")
                    self.opp_move = reply
                    return after
                self.game.pop()
            if info["turn_source"] != "last-move highlight" or board.turn != me:
                if self.warned != placement:
                    self.log("   board changed, but not by one legal move - waiting (misread or animation?)")
                    self.warned = placement
                return None
            self.log("   lost track of the game, following the screen again")
        elif info["turn_source"] == "last-move highlight":
            if board.turn != me:
                return None
        elif not (placement == START and me == chess.WHITE):
            return None
        # read straight off the screen: castling rights can only shrink during a game
        board.turn = me
        self.rights &= board.castling_rights
        board.castling_rights = self.rights
        return board

    def step(self):
        """Look at the board once; play a move if it is our turn. Returns True if a move was played."""
        self.sync_clock()
        t_look = time.time()
        got = self.read_stable()
        if got is None:
            return False
        info, bm = got
        placement = info["board"].board_fen()
        if placement == START and self.played_from != START:
            self.new_game()
            if not self.white_bottom:
                self.game, self.expected = chess.Board(), START   # playing black: wait for white's first move
        if self.premove:
            state = self.check_premove(placement, bm)
            if state == "queued":
                return False
            if state == "played":
                return True
            if state == "gone":
                self.on_premove("dropped", f"{self.premove['san']} dropped: they played something else")
                self.premove = None
        board = self.decide(info)
        if board is None:
            return False

        me = board.turn
        if not board.is_valid() or board.is_game_over():
            if self.warned != placement:
                why = f"game over ({board.result()})" if board.is_valid() else f"position looks wrong: {board.fen()}"
                self.log(f"Waiting - {why}")
                self.warned = placement
            return False

        t0 = time.time()
        retry = placement == self.played_from
        wait, seconds = 0.0, self.args.time
        # playing as a human always takes human time: the pace from the clock if timing is off
        timing = self.args.human or (human_index.pace(self.args.clock) if self.args.play_like else None)
        if retry:
            self.log("   move did not register, clicking it again")
        elif self.low_on_time():
            seconds = min(seconds, LOW_TIME_THINK)
            if timing:   # rushed, but not like a metronome
                wait = random.uniform(0.05, 0.4)
                self.log(f"   thinking {wait:.1f}s (time trouble)")
        elif timing:
            top = engine.top_moves(board, 3, 0.1, self.args.engine, not self.args.no_stockfish)
            human_top = None
            if self.args.play_like and maia.available():
                human_top = max(maia.policy(board, self.args.play_like).values())
            wait, why = think_time(board, top, self.opp_move, self.last_eval, self.moves, PACES[timing],
                                   human_top=human_top)
            wait = min(wait, self.args.max_think)
            seconds = min(seconds, max(0.1, wait * 0.8))   # quick moves must not wait on the engine
            self.log(f"   thinking {wait:.1f}s ({why})")
        r = human_index.choose_move(board, self.args, seconds, self.opp_move.to_square if self.opp_move else None,
                                    low_time=self.low_on_time())
        if wait > 0:
            if self.stop.wait(max(0.0, t0 + wait - time.time())):
                return False
            if wait > 1.5:
                # a long think: make sure nothing happened meanwhile (resign, flag, takeback)
                again = self.read()
                if again is None or again[0]["board"].board_fen() != placement:
                    self.log("   position changed while thinking - not moving")
                    return False
        move = r["move"]
        if not retry:
            self.moves += 1
        self.log(f"{self.moves}{'.' if me == chess.WHITE else '...'} {board.san(move):<7} "
              f"{chess.square_name(move.from_square)}{bm.center(chess.square_name(move.from_square))} -> "
              f"{chess.square_name(move.to_square)}{bm.center(chess.square_name(move.to_square))}")
        if "allowed" in r:
            self.log(f"   human mode: {self.human_mode_note(board, r)}")
        elif "play_like" in r:
            note = f"   as a {r['play_like']} human ({r['pace']}, {r['source']}): {r['human']:.0%} of them play this"
            if r["best"]["move"] != move:
                gap = "the difference is a mate" if r["loss_cp"] > engine.MATE // 2 \
                    else f"{r['loss_cp'] / 100:.1f} pawns better"
                note += f", best was {board.san(r['best']['move'])} ({gap})"
            self.log(note)

        if self.args.dry_run:
            bm.hover(chess.square_name(move.from_square), duration=0.2, **self.mouse_style())
            time.sleep(0.3)
            bm.hover(chess.square_name(move.to_square), duration=0.2, **self.mouse_style())
        else:
            play_move(bm, move, drag=self.args.drag, delay=self.args.delay,
                      jitter=self.args.jitter, auto_queen=self.args.auto_queen, **self.mouse_style())
        if self.clock:  # our clock stopped when the move landed, before the mouse moves away
            if retry:   # the clock kept running since the click that did not register
                self.clock.spend(time.time() - self.played_at, increment=False)
            else:       # it started when their move landed, about one screenshot before we saw it
                self.clock.spend(time.time() - t_look + self.args.poll)
        park(bm, **self.mouse_style())
        self.after_move(board, r, wait)
        self.played_from = placement
        if not retry:
            self.premove_streak = 0
            self.queue_premove(bm)
        return True

    def after_move(self, board, r, think, premoved=False):
        """Report our move r["move"] from `board` and remember the game after it."""
        move, me = r["move"], board.turn
        chance = lab = None
        if "human" in r:               # human mode already worked it out
            chance, lab = r["human"], human_index.label(r["human"], r["loss_cp"])
        elif self.args.human_index is not None:
            last_to = self.opp_move.to_square if self.opp_move else None
            chance, lab, _ = human_index.report(board, move, self.args.human_index, last_to,
                                                engine_path=self.args.engine, use_stockfish=not self.args.no_stockfish,
                                                cands=r.get("cands"))
        if chance is not None:
            self.log(f"   human index: {chance:.0%} ({lab})")
        if self.on_move:
            # eval from white's point of view, for an eval bar
            sign = 1 if me == chess.WHITE else -1
            self.on_move({
                "number": self.moves, "san": board.san(move), "color": "white" if me == chess.WHITE else "black",
                "eval_cp": None if r.get("score_cp") is None else sign * r["score_cp"],
                "mate": None if r.get("mate") is None else sign * r["mate"],
                # ours and the best move's value, for the move's accuracy (no "best": r is the engine's top move)
                "value": _value(r), "best_value": r["best"]["value"] if "best" in r else _value(r),
                "human": chance, "human_label": lab, "think": think, "premove": premoved,
                "clock": self.clock.left if self.clock else None,
            })
        self.last_eval = _value(r)
        self.premove = None
        self.game_before = board
        self.game = board.copy()
        self.game.push(move)
        self.expected, self.played_at = self.game.board_fen(), time.time()

    def human_mode_note(self, board, r):
        """What human mode did this move, for the log."""
        x = self.args.human_moves
        if r["human"] < x / 100:
            return f"no move is {x:g}%+ human, played the most human one"
        note = f"{r['allowed']} move{'s' if r['allowed'] > 1 else ''} {x:g}%+ human"
        if r["best"]["move"] != r["move"]:
            note += f", skipped the engine's {board.san(r['best']['move'])}"
        return note

    # ---- premoves ------------------------------------------------------- #
    def sync_clock(self):
        """Follow the --clock setting (the control panel can change it mid-game)."""
        spec = self.args.clock or None
        if spec != (self.clock.spec if self.clock else None):
            self.clock = premove.Clock(spec) if spec else None

    def low_on_time(self):
        return self.clock is not None and self.clock.left < self.args.low_time

    def mouse_style(self):
        """How to move the mouse now: like a hand (--mouse natural), quicker in faster
        games and when low on time; or straight and fast (--mouse instant)."""
        if self.args.mouse != "natural":
            return {"natural": False}
        speed = MOUSE_SPEED[human_index.pace(self.args.clock, self.args.human)]
        return {"natural": True, "speed": speed * (1.5 if self.low_on_time() else 1.0)}

    def queue_premove(self, bm):
        """Right after our move: if their reply is predictable, click our answer to it now."""
        if not self.args.premove or self.game is None:
            return
        low = self.low_on_time()
        if self.premove_streak >= self.args.premove_streak:
            self.on_premove("skipped", f"No premove: {self.premove_streak} in a row, thinking this one over")
            return
        pm, why_not = premove.plan(self.game, self.args, low)
        if pm is None:
            if why_not:
                self.log(f"   no premove: {why_not}")
                self.on_premove("skipped", f"No premove: {why_not}")
            return
        if self.stop.is_set():
            return
        # planning took a moment: if they have already moved, the click would not be a premove
        again = self.read()
        if again is None or again[0]["board"].board_fen() != self.expected:
            return
        after = self.game.copy()
        after.push(pm["reply"])
        san, reply = after.san(pm["move"]), self.game.san(pm["reply"])
        self.log(f"   premove {san} if {reply} ({pm['why']})")
        frm, to = chess.square_name(pm["move"].from_square), chess.square_name(pm["move"].to_square)
        if self.args.dry_run:
            bm.hover(frm, duration=0.2, **self.mouse_style())
            time.sleep(0.3)
            bm.hover(to, duration=0.2, **self.mouse_style())
            park(bm, **self.mouse_style())
            return                     # nothing was queued on the site
        play_move(bm, pm["move"], drag=self.args.drag, delay=self.args.delay,
                  jitter=self.args.jitter, auto_queen=self.args.auto_queen, **self.mouse_style())
        park(bm, **self.mouse_style())
        self.premove = {**pm, "san": san, "shown": premove.shown(self.game, pm["move"]), "since": None}
        self.on_premove("queued", f"{san} if {reply} ({pm['why']})")

    def check_premove(self, placement, bm):
        """Follow the queued premove. Returns "queued" (still waiting for their reply),
        "played" (the site played it after their reply), "gone" (their reply made it
        illegal, so the site dropped it: our turn as usual) or "unknown"."""
        pm = self.premove
        if placement in (self.expected, pm["shown"]):
            return "queued"
        for reply in self.game.legal_moves:
            after = self.game.copy()
            after.push(reply)
            if after.board_fen() == placement:
                if pm["move"] not in after.legal_moves:
                    return "gone"
                # their move is on screen but ours is not yet: give the site a moment
                pm["since"] = pm["since"] or time.time()
                return "queued" if time.time() - pm["since"] < PREMOVE_GRACE else "gone"
            if pm["move"] in after.legal_moves:
                after.push(pm["move"])
                if after.board_fen() == placement:
                    after.pop()
                    self.premove_played(after, reply, bm)
                    return "played"
        return "unknown"

    def premove_played(self, board, reply, bm):
        """The site played our premove after their `reply`; `board` is the position after that reply."""
        pm = self.premove
        move = pm["move"]
        self.log(f"   opponent: {self.game.san(reply)}")
        self.opp_move = reply
        if reply == pm["reply"]:
            r, note = pm["result"], "as predicted"
        else:
            # a surprise, but the premove was checked to be fine after their likely replies
            look = dict(engine_path=self.args.engine, use_stockfish=not self.args.no_stockfish)
            value = engine.move_value(board, move, **look)
            best = max(value, engine.move_value(board, None, **look))   # for the accuracy graph
            r, note = {"move": move, **engine.score_fields(value), "best": {"value": best}}, "after another reply"
        self.moves += 1
        self.log(f"{self.moves}{'.' if board.turn == chess.WHITE else '...'} {board.san(move):<7} premove, {note}")
        if self.clock:
            self.clock.spend(0.1)      # sites charge a premove about 0.1s
        self.premove_streak += 1
        self.on_premove("played", f"{board.san(move)} played, {note}")
        self.after_move(board, r, 0.0, premoved=True)
        self.played_from = board.board_fen()
        self.queue_premove(bm)         # keep premoving while their replies stay predictable

    def run(self):
        self.log("Watching the board. Move the mouse into a screen corner to stop.")
        while not self.stop.is_set():
            if self.step():
                if self.args.once:
                    return
                self.stop.wait(0.4)   # give the site time to show our move
            else:
                self.stop.wait(self.args.poll)


def _force_turn(bot, info):
    board = info["board"]
    board.turn = chess.WHITE if bot.white_bottom else chess.BLACK
    return board


def build_parser():
    p = argparse.ArgumentParser(description="Auto-play the board on screen")
    p.add_argument("--side", choices=["white", "black"], help="colour at the bottom = the side we play (default: auto)")
    p.add_argument("--monitor", type=int, default=0, help="monitor to watch (0 = all, 1 = primary)")
    p.add_argument("--time", type=float, default=1.0, help="seconds to think per move (default 1)")
    p.add_argument("--accuracy", type=float, default=100, help="0-100, lower plays worse on purpose")
    p.add_argument("--engine", help="path to a UCI engine such as stockfish.exe")
    p.add_argument("--no-stockfish", action="store_true", help="always use the built-in engine")
    p.add_argument("--drag", action="store_true", help="drag pieces instead of click-click")
    p.add_argument("--mouse", choices=["natural", "instant"], default="natural",
                   help="natural (default): move the mouse like a hand, curved and uneven with small "
                        "corrections; instant: straight and fast")
    p.add_argument("--delay", type=float, default=0.15, help="seconds between the two clicks")
    p.add_argument("--jitter", type=int, default=3, help="random pixels off the square centre")
    p.add_argument("--auto-queen", action="store_true", help="the site promotes to a queen by itself")
    p.add_argument("--poll", type=float, default=0.25, help="seconds between screenshots")
    p.add_argument("--retry", type=float, default=3.0, help="re-click a move that did not register after this long")
    p.add_argument("--once", action="store_true", help="play a single move and exit")
    p.add_argument("--turn-now", action="store_true", help="with --once: it is our move, whatever the screen suggests")
    p.add_argument("--dry-run", action="store_true", help="hover the squares instead of clicking")
    p.add_argument("--human", nargs="?", const="rapid", choices=list(PACES),
                   help="take human-like time: quick on easy moves, longer on hard ones "
                        "(pace: bullet, blitz, rapid (default), classical)")
    p.add_argument("--human-index", nargs="?", type=int, const=1500, metavar="RATING",
                   help="after each move, print the chance a human of this rating (default 1500) plays it")
    p.add_argument("--human-moves", type=float, default=0, metavar="PERCENT",
                   help="human mode: only play moves a human of the --human-index rating (default 1500) "
                        "picks at least this often, e.g. 10 (default 0 = off)")
    p.add_argument("--play-like", type=int, metavar="RATING",
                   help="play like a human of this rating, e.g. 1200: pick moves with the chances the human "
                        "index gives that player (overrides --accuracy and --human-moves)")
    p.add_argument("--max-think", type=float, default=30.0, help="longest human-like think in seconds")
    p.add_argument("--premove", action="store_true",
                   help="premove when their reply is at least 70%% likely (only move, book opening, clear "
                        "best or natural move such as a recapture), or 40%% likely when low on time (--clock)")
    p.add_argument("--opponent-engine", action="store_true",
                   help="the opponent is an engine: judge its reply by how far its best move stands out")
    p.add_argument("--clock", metavar="MIN+INC",
                   help="time control such as 3+2, to track our time left: when low, think fast and premove more")
    p.add_argument("--low-time", type=float, default=20.0, help="seconds left that count as low on time (default 20)")
    p.add_argument("--premove-streak", type=int, default=2, metavar="N",
                   help="most premoves in a row before thinking over a move again (default 2)")
    p.add_argument("--premove-time", type=float, default=0.1,
                   help="engine seconds for predicting their reply (default 0.1)")
    p.add_argument("--templates", default=br.DEFAULT_TEMPLATES)
    return p


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = build_parser()
    args = p.parse_args()
    if bc.pyautogui is None:
        sys.exit("pip install pyautogui")

    if args.turn_now and not args.once:
        p.error("--turn-now only works with --once")
    if args.clock and not premove.Clock.valid(args.clock):
        p.error("--clock takes minutes+increment, like 3+2 or 1")
    bot = AutoPlayer(args)
    if args.turn_now:
        bot.decide = lambda info: _force_turn(bot, info)
    try:
        bot.run()
    except (KeyboardInterrupt, bc.pyautogui.FailSafeException):
        print("\nStopped.")


if __name__ == "__main__":
    main()
