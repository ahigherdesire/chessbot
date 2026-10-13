"""Human index: an estimate of how likely a human of a given rating is to play each move.

It is a model, not data: a move's chance falls with how much worse it is than the
best move (stronger players give away less) and with its rank among the engine's
choices (people mostly pick from the first few). It is nudged by what humans find
natural (captures, recaptures, checks, castling, moving an attacked piece) or tend
to overlook (backward retreats, long quiet moves, early king walks).
"""
import math

import chess

import engine

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


def human_odds(board, cands, rating=1500, last_to=None):
    """[(move, chance)] for every scored move, most human first. Chances sum to 1."""
    if not cands:
        return []
    cands = sorted(cands, key=lambda c: -c["value"])
    best = cands[0]["value"]
    t = temperature(rating)
    # humans mostly pick among the first few candidates, even when the rest are nearly as good
    logits = [-min(best - c["value"], 1000) / t - RANK_WEIGHT * math.log(i + 1)
              + naturalness(board, c["move"], last_to) for i, c in enumerate(cands)]
    top = max(logits)
    weights = [math.exp(x - top) for x in logits]
    total = sum(weights)
    return sorted(((c["move"], w / total) for c, w in zip(cands, weights)), key=lambda x: -x[1])


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


def report(board, move, rating=1500, last_to=None, seconds=0.3, engine_path=None, use_stockfish=True, top=3,
           cands=None, engine_best=None):
    """(chance of `move`, its label, the `top` most human moves as [(san, chance)]).

    cands: scores for every move if you already have them (skips a search).
    engine_best: the best move from a deeper search; the quick scores are noisy
    between near-equal moves, so that move is treated as the best one."""
    cands = cands or score_all(board, seconds, engine_path, use_stockfish)
    if engine_best is not None:
        top_value = max(c["value"] for c in cands)
        cands = [dict(c, value=top_value + 1) if c["move"] == engine_best else c for c in cands]
    odds = human_odds(board, cands, rating, last_to)
    chance = next((p for m, p in odds if m == move), 0.0)
    best = max(c["value"] for c in cands)
    loss = best - next((c["value"] for c in cands if c["move"] == move), best)
    return chance, label(chance, loss), [(board.san(m), p) for m, p in odds[:top]]
