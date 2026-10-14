"""Human index: an estimate of how likely a human of a given rating is to play each move.

It is a model, not data. Moves are judged the way a player of that rating sees
them: by a search only as deep as they calculate (search_depth), so a move that is
only good because of a deep line looks bad and gets a low chance. A move's chance
then falls with how much worse it looks than the best-looking move (stronger
players give away less) and with its rank (people mostly pick from the first few).
It is nudged by what humans find natural (captures, recaptures, checks, castling,
moving an attacked piece) or tend to overlook (backward retreats, long quiet moves,
early king walks).
"""
import math
import random

import chess

import engine
import maia

RANK_WEIGHT = 1.2


def temperature(rating):
    """Centipawns a player of this rating shrugs off: 1000 -> 200, 1500 -> 100, 2000 -> 50, 2500 -> 25."""
    return 200 * 2 ** (-(rating - 1000) / 500)


def score_all(board, seconds=0.3, engine_path=None, use_stockfish=True):
    """Engine score ("value", centipawns for the side to move) for every legal move."""
    path = engine.find_stockfish(engine_path) if use_stockfish else None
    if path:
        return engine.stockfish_lines(path, board, seconds)[0]
    return engine.Searcher().score_root(board, seconds, 64, None, engine.INF)[0]


def search_depth(rating):
    """Plies a player of this rating effectively looks ahead: 1000 -> 2, 1500 -> 4, 2000 -> 6, 2500 -> 8."""
    return max(1, min(12, round(2 + (rating - 1000) / 250)))


def looks(board, rating=1500, engine_path=None, use_stockfish=True):
    """How good every legal move looks to a player of `rating`: scores from a search
    only search_depth(rating) plies deep."""
    depth = search_depth(rating)
    path = engine.find_stockfish(engine_path) if use_stockfish else None
    if path:
        # one thread and a clear hash: otherwise a search this short gives a different answer each time
        return engine.stockfish_lines(path, board, None, depth=depth, threads=1, hash_mb=16, fresh=True)[0]
    return engine.Searcher().score_root(board, 5.0, depth, None, engine.INF)[0]


def glance(board):
    """How every legal move looks at a glance, without a thought for the reply: the
    position right after it, by the built-in static evaluation (centipawns for the side
    to move). A piece left hanging, or walking into a fork, looks fine this way."""
    out = []
    for move in board.legal_moves:
        board.push(move)
        value = engine.MATE if board.is_checkmate() else -engine.evaluate(board)
        board.pop()
        out.append({"move": move, "value": value})
    return out


def sharpness(board):
    """0-1: how much is going on (captures and checks for both sides), where lapses are likelier."""
    def forcing(b):
        return sum(1 for m in b.legal_moves if b.is_capture(m) or b.gives_check(m))

    ours = forcing(board)
    theirs = 0
    if not board.is_check():          # their threats: what they could do if it were their move
        board.push(chess.Move.null())
        theirs = forcing(board)
        board.pop()
    return min(1.0, (ours + theirs) / 12)


def chances(board, rating=1500, last_to=None, engine_path=None, use_stockfish=True):
    """{move: chance} that a player of `rating` picks each move. Chances sum to 1."""
    return dict(human_odds(board, looks(board, rating, engine_path, use_stockfish), rating, last_to))


def naturalness(board, move, last_to=None):
    """Log-odds bonus for how natural a move looks to a human (0 = neutral)."""
    piece = board.piece_at(move.from_square)
    bonus = 0.0
    if board.is_castling(move):
        return 0.6
    if board.is_capture(move):
        bonus += 0.5
        if move.to_square == last_to:
            bonus += 1.0                                   # recapturing is the first thing anyone looks at
    if board.gives_check(move):
        bonus += 0.5
    if move.promotion:
        bonus += 0.5 if move.promotion == chess.QUEEN else -2.0
    if piece.piece_type != chess.KING and board.is_attacked_by(not board.turn, move.from_square):
        bonus += 0.4                                       # saving an attacked piece
    if piece.piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        forward = chess.square_rank(move.to_square) - chess.square_rank(move.from_square)
        if (forward < 0) == (board.turn == chess.WHITE) and forward != 0 and not board.is_capture(move):
            bonus -= 0.5                                   # retreats are easy to miss
    if not board.is_capture(move) and chess.square_distance(move.from_square, move.to_square) >= 4:
        bonus -= 0.3                                       # long quiet moves too
    if piece.piece_type == chess.KING and board.pieces(chess.QUEEN, not board.turn):
        bonus -= 0.5                                       # king walks while queens are on
    return bonus


def human_odds(board, cands, rating=1500, last_to=None, spread=1.0):
    """[(move, chance)] for every scored move, most human first. Chances sum to 1.
    cands: scored moves; looks() gives a human's view, engine scores a perfect player's.
    spread: how loosely the player picks between moves (2 = shrugs off twice as much)."""
    if not cands:
        return []
    cands = sorted(cands, key=lambda c: -c["value"])
    best = cands[0]["value"]
    t = temperature(rating) * spread
    # humans mostly pick among the first few candidates, even when the rest are nearly as good
    logits = [-min(best - c["value"], 1000) / t - RANK_WEIGHT * math.log(i + 1)
              + naturalness(board, c["move"], last_to) for i, c in enumerate(cands)]
    top = max(logits)
    weights = [math.exp(x - top) for x in logits]
    total = sum(weights)
    return sorted(((c["move"], w / total) for c, w in zip(cands, weights)), key=lambda x: -x[1])


def human_move(board, rating=1500, min_chance=0.1, seconds=1.0, last_to=None, accuracy=100,
               engine_path=None, use_stockfish=True):
    """Human mode: the engine's choice among only the moves a player of `rating` would
    pick at least `min_chance` of the time, so it never plays a move only an engine
    finds. When no move is that human, the most human move is played. Returns an
    engine.best_move()-style result, plus "human" (the chance of the chosen move) and
    "allowed" (how many moves passed)."""
    cands = score_all(board, seconds, engine_path, use_stockfish)
    odds = chances(board, rating, last_to, engine_path, use_stockfish)
    allowed = [c for c in cands if odds.get(c["move"], 0) >= min_chance] \
        or [max(cands, key=lambda c: odds.get(c["move"], 0))]
    chosen, _ = engine.pick_move(allowed, accuracy)   # --accuracy still applies among them
    best = max(cands, key=lambda c: c["value"])
    return {**chosen, "best": best, "loss_cp": best["value"] - chosen["value"], "cands": cands,
            "human": odds.get(chosen["move"], 0), "allowed": len(allowed), "accuracy": accuracy}


# Playing as a human of a given rating ("play like"): Maia's chances for each move, from
# networks trained on real games (maia.py). Maia covers 1100-1900; the settings below
# stretch it to other ratings, faster games and time trouble.
# Tuned so self-play matches graded real lichess games (accuracy, mistake and blunder rates).
BELOW_MAIA = 750        # under 1100, flatten Maia-1100's chances: 1 + (1100 - rating) / this
FLATTEN = {"rapid": 1.0, "blitz": 1.09, "bullet": 1.12}   # faster games: more errors
LOW_TIME_FLATTEN = 1.25
ABOVE_MAIA_T0 = 1000    # over 1900, the engine vetoes mistakes such a player would see through:
ABOVE_MAIA_HALF = 140   # a move loses exp(-loss / T), T = T0 halved every HALF points over 1900


def pace(clock=None, timing=None):
    """bullet, blitz or rapid: from the time control ("3+2") if known, else the human timing pace."""
    if clock:
        base = float(clock.split("+")[0]) * 60
        return "bullet" if base < 180 else "blitz" if base < 480 else "rapid"
    return {"bullet": "bullet", "rapid": "rapid", "classical": "rapid"}.get(timing, "blitz")


def maia_odds(board, rating, cands, tc="blitz", low_time=False):
    """{move: chance} for a human of `rating`: Maia's chances, flattened (more mistakes)
    below 1100, in faster games and in time trouble, and filtered by the engine's scores
    (`cands`) above 1900."""
    p = maia.policy(board, rating)
    flatten = 1.0
    if rating < maia.RATINGS[0]:
        flatten *= 1 + (maia.RATINGS[0] - rating) / BELOW_MAIA
    flatten *= FLATTEN.get(tc, 1.0)
    if low_time:
        flatten *= LOW_TIME_FLATTEN
    logit = {m: math.log(max(v, 1e-6)) / flatten for m, v in p.items()}
    if rating > maia.RATINGS[-1]:
        t = ABOVE_MAIA_T0 * 2 ** (-(rating - maia.RATINGS[-1]) / ABOVE_MAIA_HALF)
        value = {c["move"]: c["value"] for c in cands}
        best = max(value.values())
        for m in logit:
            logit[m] -= min(best - value.get(m, best - 1000), 1000) / t
    top = max(logit.values())
    w = {m: math.exp(x - top) for m, x in logit.items()}
    total = sum(w.values())
    return {m: x / total for m, x in w.items()}


def lapse_odds(board, rating, last_to=None, low_time=False, engine_path=None, use_stockfish=True):
    """Without Maia: a rougher model. Usually the player thinks as deep as their rating
    allows (looks()); now and then they lapse and go by a glance (glance()), blind to
    the reply, which is how players hang pieces. Lapses are likelier at lower ratings,
    in sharp positions and in time trouble."""
    lapse = 0.12 * 2 ** (-(rating - 1000) / 700)
    q = min(0.9, lapse * (0.5 + sharpness(board)) * (2 if low_time else 1))
    look = dict(human_odds(board, looks(board, rating, engine_path, use_stockfish), rating, last_to, 2.5))
    blind = dict(human_odds(board, glance(board), rating, last_to, 2.5))
    return {m: (1 - q) * look.get(m, 0) + q * blind.get(m, 0) for m in set(look) | set(blind)}


def play_like(board, rating=1500, seconds=1.0, last_to=None, tc="blitz", low_time=False,
              engine_path=None, use_stockfish=True, rng=random):
    """Play like a human of `rating`, mistakes and all: a move drawn at random with the
    chance such a player plays it (maia_odds(), or lapse_odds() without Maia). The
    engine's full search scores the moves, for the eval and accuracy shown (and, above
    1900, to veto mistakes). Returns an engine.best_move()-style result plus "human"
    (the chance of the chosen move) and "source" (maia or model)."""
    cands = score_all(board, seconds, engine_path, use_stockfish)
    if maia.available():
        odds, source = maia_odds(board, rating, cands, tc, low_time), "maia"
    else:
        odds, source = lapse_odds(board, rating, last_to, low_time, engine_path, use_stockfish), "model"
    moves = list(odds)
    move = rng.choices(moves, [odds[m] for m in moves])[0]
    chosen = next((c for c in cands if c["move"] == move), None)
    if chosen is None:   # the built-in engine ran out of time before scoring it
        chosen = {"move": move, "pv": [move], **engine.score_fields(min(c["value"] for c in cands)),
                  "value": min(c["value"] for c in cands)}
    best = max(cands, key=lambda c: c["value"])
    return {**chosen, "best": best, "loss_cp": best["value"] - chosen["value"], "cands": cands,
            "human": odds[move], "play_like": rating, "pace": tc, "source": source}


def choose_move(board, args, seconds, last_to=None, low_time=False):
    """The move to play under the bot's settings (an argparse namespace like auto_play's):
    "play like" a rating when args.play_like is set, else human mode when args.human_moves
    is set, otherwise the engine with --accuracy. low_time: we are short of time."""
    use_sf = not args.no_stockfish
    if getattr(args, "play_like", None):
        tc = pace(getattr(args, "clock", None), getattr(args, "human", None))
        return play_like(board, args.play_like, seconds, last_to, tc, low_time, args.engine, use_sf)
    if getattr(args, "human_moves", 0):
        return human_move(board, args.human_index or 1500, args.human_moves / 100, seconds, last_to,
                          args.accuracy, args.engine, use_sf)
    return engine.best_move(board, seconds=seconds, engine_path=args.engine, use_stockfish=use_sf,
                            accuracy=args.accuracy)


def label(chance, loss_cp=0):
    """Words for a chance. loss_cp: how much worse than the best move it is."""
    if chance > 0.50:
        return "very human"
    if chance >= 0.10:
        return "natural"
    if chance >= 0.03:
        return "unusual"
    # rare and strong = a move only an engine finds; rare and bad = a mistake few would make
    return "engine-like" if loss_cp <= 30 else "rare mistake"


def report(board, move, rating=1500, last_to=None, engine_path=None, use_stockfish=True, top=3, cands=None):
    """(chance of `move`, its label, the `top` most human moves as [(san, chance)]).

    cands: deep engine scores for every move, if you already have them. They only
    decide the label of a rare move (engine-like or a mistake); without them the
    human's own view is used."""
    seen = looks(board, rating, engine_path, use_stockfish)
    odds = human_odds(board, seen, rating, last_to)
    chance = next((p for m, p in odds if m == move), 0.0)
    cands = cands or seen
    best = max(c["value"] for c in cands)
    loss = best - next((c["value"] for c in cands if c["move"] == move), best)
    return chance, label(chance, loss), [(board.san(m), p) for m, p in odds[:top]]
