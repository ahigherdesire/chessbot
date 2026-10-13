"""Premoves: guess the opponent's reply and queue our answer before they move.

The bot decides each move whether to premove. It estimates how likely their most
likely reply is:
  * only legal move: certain,
  * book: a well-known opening position, from the share of players who answer that way,
  * opponent engine (--opponent-engine): how far the engine's best move is ahead of the rest,
  * otherwise: the human index, which favours clearly best moves and natural ones
    such as recaptures.
It premoves when that reply is at least SURE likely, or LOW_TIME_SURE when low on
time (--clock), since then a wrong guess costs less than thinking. It also never
premoves more than --premove-streak times in a row.

The site plays a queued premove after *any* reply where it is legal, not only the
predicted one, so each premove is checked against the opponent's other likely
replies and skipped if it would be a mistake after one of them.
"""
import math

import chess
import chess.engine

import engine
import human_index

SURE = 0.7             # premove when their reply is at least this likely...
LOW_TIME_SURE = 0.4    # ...or this likely when low on time
ENGINE_SPREAD = 20     # centipawns: an engine picks between moves this close almost at random
RISK = 80              # our premove may lose at most this much after another reply
LOW_TIME_RISK = 250    # in a time scramble a small slip beats losing on time
ALTERNATIVES = 8       # how many of the opponent's best replies we check the premove against
LOW_TIME_ALTERNATIVES = 4   # fewer when low on time: each check costs ~50ms
DECIDED = 1500         # centipawns beyond which the game is decided either way

# Rough share of players who choose each reply. Only lines where one reply clearly
# dominates are listed: after 1.e4 nobody can tell what black plays.
_BOOK = {
    # replies by black
    "e4 e5 Nf3": {"Nc6": 0.8},
    "e4 e5 Nf3 Nc6 Bb5": {"a6": 0.7, "Nf6": 0.2},
    "e4 e5 Nf3 Nc6 Bb5 a6 Ba4": {"Nf6": 0.85},
    "e4 e5 Nf3 Nc6 Bb5 a6 Bxc6": {"dxc6": 0.95},
    "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O": {"Be7": 0.7, "Nxe4": 0.2},
    "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1": {"b5": 0.95},
    "e4 e5 Nf3 Nc6 d4": {"exd4": 0.95},
    "e4 e5 Nf3 Nf6 Nxe5": {"d6": 0.95},
    "e4 e5 Nf3 Nf6 Nxe5 d6 Nf3": {"Nxe4": 0.95},
    "e4 e5 d4": {"exd4": 0.95},
    "e4 e5 d4 exd4 Qxd4": {"Nc6": 0.9},
    "e4 c5 d4": {"cxd4": 0.9},
    "e4 c5 Nf3 d6 d4": {"cxd4": 0.98},
    "e4 c5 Nf3 d6 d4 cxd4 Nxd4": {"Nf6": 0.9},
    "e4 c5 Nf3 Nc6 d4": {"cxd4": 0.98},
    "e4 c5 Nf3 e6 d4": {"cxd4": 0.98},
    "e4 e6 d4": {"d5": 0.9},
    "e4 e6 d4 d5 e5": {"c5": 0.9},
    "e4 e6 d4 d5 exd5": {"exd5": 0.98},
    "e4 c6 d4": {"d5": 0.95},
    "e4 c6 d4 d5 e5": {"Bf5": 0.85},
    "e4 c6 d4 d5 Nc3": {"dxe4": 0.95},
    "e4 c6 d4 d5 Nd2": {"dxe4": 0.95},
    "e4 c6 d4 d5 exd5": {"cxd5": 0.98},
    "e4 d5 exd5": {"Qxd5": 0.7, "Nf6": 0.3},
    "e4 Nf6 e5": {"Nd5": 0.97},
    "d4 d5 c4 e6 Nc3": {"Nf6": 0.8},
    "d4 d5 c4 e6 Nc3 Nf6 cxd5": {"exd5": 0.95},
    "d4 d5 c4 c6 Nf3": {"Nf6": 0.9},
    "d4 Nf6 c4 g6 Nc3": {"Bg7": 0.75, "d5": 0.25},
    "d4 Nf6 c4 g6 Nc3 Bg7 e4": {"d6": 0.95},
    "d4 Nf6 c4 g6 Nc3 d5 cxd5": {"Nxd5": 0.98},
    "d4 Nf6 c4 e6 Nc3": {"Bb4": 0.75, "d5": 0.2},
    # replies by white
    "e4 e5": {"Nf3": 0.8},
    "e4 c5": {"Nf3": 0.75},
    "e4 c5 Nf3 d6": {"d4": 0.85},
    "e4 c5 Nf3 Nc6": {"d4": 0.7, "Bb5": 0.25},
    "e4 c5 Nf3 e6": {"d4": 0.8},
    "e4 c5 Nf3 d6 d4 cxd4": {"Nxd4": 0.99},
    "e4 c5 Nf3 Nc6 d4 cxd4": {"Nxd4": 0.99},
    "e4 c5 Nf3 e6 d4 cxd4": {"Nxd4": 0.99},
    "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6": {"Nc3": 0.95},
    "e4 d5": {"exd5": 0.9},
    "e4 d5 exd5 Qxd5": {"Nc3": 0.85},
    "e4 Nf6": {"e5": 0.85},
    "e4 e6": {"d4": 0.9},
    "e4 c6": {"d4": 0.85},
    "e4 e6 d4 d5 e5 c5": {"c3": 0.9},
    "e4 e5 Nf3 Nc6 Bb5 a6": {"Ba4": 0.8, "Bxc6": 0.2},
    "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6": {"O-O": 0.9},
    "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7": {"Re1": 0.9},
    "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5": {"Bb3": 0.98},
    "e4 e5 Nf3 Nc6 d4 exd4": {"Nxd4": 0.8},
    "e4 e5 Nf3 Nf6": {"Nxe5": 0.8},
    "e4 e5 Nf3 Nf6 Nxe5 d6": {"Nf3": 0.95},
    "e4 e5 Nf3 d6": {"d4": 0.85},
    "e4 e5 Nf3 d6 d4 exd4": {"Nxd4": 0.9},
    "d4 d5": {"c4": 0.7},
    "d4 Nf6": {"c4": 0.7},
    "d4 Nf6 c4 g6": {"Nc3": 0.8},
    "d4 Nf6 c4 g6 Nc3 Bg7": {"e4": 0.85},
}


def _build_book():
    """Key the book by position, so it also works after transpositions or when the
    bot joined mid-game and has no move list."""
    book = {}
    for line, replies in _BOOK.items():
        b = chess.Board()
        for san in line.split():
            b.push_san(san)
        book[b.epd()] = {b.parse_san(san): share for san, share in replies.items()}
    return book


BOOK = _build_book()


def book_reply(board):
    """(most played reply, its share) for a book position, else None."""
    replies = BOOK.get(board.epd())
    if not replies:
        return None
    return max(replies.items(), key=lambda kv: kv[1])


def opponent_replies(board, n, seconds, engine_path=None, use_stockfish=True):
    """The opponent's n best replies, best first, each with a "value" (centipawns for them)."""
    top = engine.top_moves(board, n, seconds, engine_path, use_stockfish)
    if top or board.is_game_over():
        return top
    cands, _ = engine.Searcher().score_root(board, seconds * 3, 64, None, engine.INF)
    return sorted(cands, key=lambda c: -c["value"])[:n]


def predict(board, top, opponent_engine=False, rating=1500):
    """(reply, likelihood, why): the opponent's most likely reply and how likely it is.
    board: position after our move. top: opponent_replies() for it."""
    if not top:
        return None
    if board.legal_moves.count() == 1:
        return top[0]["move"], 1.0, "only move"
    if opponent_engine:
        # an engine plays what it rates best, book or not; ours only agrees when that move stands out
        best = top[0]["value"]
        weights = [math.exp(-min(best - c["value"], 1000) / ENGINE_SPREAD) for c in top]
        return top[0]["move"], weights[0] / sum(weights), "engine's choice"
    book = book_reply(board)
    if book:
        return book[0], book[1], "book"
    last_to = board.peek().to_square if board.move_stack else None
    move, p = human_index.human_odds(board, top, rating, last_to)[0]
    return move, p, "most natural reply"


def risky_after(board, move, reply, others, risk, depth=10, engine_path=None, use_stockfish=True):
    """The first of `others` after which `move` leaves us more than `risk` centipawns worse
    off than playing it after the expected `reply`, or None if it is fine (or illegal, so the
    site drops it) after all of them. Comparing with the expected line, not with the best
    answer, lets a premove through when their surprise move is itself a mistake."""
    def value(r):
        board.push(r)
        try:
            if move not in board.legal_moves:
                return None
            v = engine.move_value(board, move, depth, engine_path=engine_path, use_stockfish=use_stockfish)
            return max(-DECIDED, min(DECIDED, v))   # still crushing is as good as the mate we expected
        finally:
            board.pop()
    expected = value(reply)
    for other in others:
        v = value(other)
        if v is not None and v < expected - risk:
            return other
    return None


def plan(game, args, low_time=False):
    """Decide whether to premove in `game` (the position after our move, opponent to move).

    Returns (premove, reason). premove is {"reply", "move", "why", "result"} or None;
    result is the engine result for our answer from the position after their reply.
    reason says why there is no premove (None when there is nothing worth saying)."""
    if game.is_game_over():
        return None, None
    use_sf = not args.no_stockfish
    t = args.premove_time
    top = opponent_replies(game, min(ALTERNATIVES, game.legal_moves.count()), t, args.engine, use_sf)
    guess = predict(game, top, args.opponent_engine, args.human_index or 1500)
    if guess is None:
        return None, None
    reply, likely, why = guess
    need = LOW_TIME_SURE if low_time else SURE
    if likely < need:
        # rounded down, so 39.6% never reads as "40%, need 40%"
        return None, f"{game.san(reply)} is only {math.floor(likely * 100)}% likely ({why}), need {need:.0%}"

    after = game.copy()
    after.push(reply)
    if after.is_game_over():
        return None, None
    r = human_index.choose_move(after, args, t if low_time else 2 * t, reply.to_square)   # human mode too
    move = r["move"]
    if move.promotion and not (args.auto_queen and move.promotion == chess.QUEEN):
        return None, "a promotion cannot be premoved"   # the picker only opens after the move

    others = [c["move"] for c in top if c["move"] != reply]
    if low_time:
        others = others[:LOW_TIME_ALTERNATIVES]
    bad = risky_after(game, move, reply, others, LOW_TIME_RISK if low_time else RISK, 8 if low_time else 10,
                      args.engine, use_sf)
    if bad is not None:
        return None, f"{after.san(move)} would be a mistake after {game.san(bad)}"
    why = f"{why}, {likely:.0%} likely" + (", low on time" if low_time else "")
    return {"reply": reply, "move": move, "why": why, "result": r}, None


def shown(game, move):
    """Placement with our premove drawn on the board before the opponent has moved
    (chess.com shows queued premoves that way)."""
    b = game.copy(stack=False)
    b.push(chess.Move.null())
    b.push(move)
    return b.board_fen()


class Clock:
    """Our time left, estimated from how long each of our moves takes. The clock on
    screen is not read, so start the bot before the game to keep it accurate."""

    def __init__(self, spec):
        """spec: minutes+increment in seconds, like "3+2" or "1" (no increment)."""
        base, _, inc = spec.partition("+")
        self.spec = spec
        self.base = float(base) * 60
        self.inc = float(inc or 0)
        self.left = self.base

    def reset(self):
        self.left = self.base

    def spend(self, seconds, increment=True):
        self.left = max(0.0, self.left - seconds) + (self.inc if increment else 0)

    @staticmethod
    def valid(spec):
        try:
            Clock(spec)
            return True
        except ValueError:
            return False
