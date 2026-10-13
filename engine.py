"""Move search: Stockfish when available (very strong), otherwise a built-in
alpha-beta engine (iterative deepening, PVS, transposition table, null-move
pruning, late-move reductions, quiescence search, tapered evaluation)."""
import atexit
import glob
import math
import os
import random
import shutil
import threading
import time

import chess
import chess.engine

HERE = os.path.dirname(os.path.abspath(__file__))
MATE = 100000
INF = 10 ** 9


# --------------------------------------------------------------------------- #
# Stockfish
# --------------------------------------------------------------------------- #
def find_stockfish(explicit=None):
    cands = [explicit, os.environ.get("STOCKFISH_PATH"), shutil.which("stockfish")]
    cands += sorted(glob.glob(os.path.join(HERE, "engines", "**", "stockfish*"), recursive=True))
    local = os.environ.get("LOCALAPPDATA", "")
    cands += glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "Stockfish*", "**", "stockfish*.exe"), recursive=True)
    for c in cands:
        if c and os.path.isfile(c) and (c.endswith(".exe") or os.access(c, os.X_OK)):
            return c
    return None


_ENGINES = {}


def stockfish(path, threads=None, hash_mb=256):
    """One Stockfish process per path (and thread count), kept open between moves: no
    start-up cost per move, and its hash table remembers earlier positions of the game.
    threads=1 gets its own process, whose short searches give the same result every time."""
    key = (path, threads)
    eng = _ENGINES.get(key)
    if eng is not None:
        try:
            eng.ping()
            return eng
        except (chess.engine.EngineError, chess.engine.EngineTerminatedError):
            pass
    eng = chess.engine.SimpleEngine.popen_uci(path)
    eng.configure({"Threads": threads or max(1, (os.cpu_count() or 2) - 1), "Hash": hash_mb})
    _ENGINES[key] = eng
    return eng


def close_engines():
    for eng in _ENGINES.values():
        try:
            eng.quit()
        except Exception:
            pass
    _ENGINES.clear()


# Python waits for running threads *before* atexit handlers, and each engine owns
# a thread, so close the engines from the hook that runs ahead of that wait.
try:
    threading._register_atexit(close_engines)
except Exception:
    atexit.register(close_engines)


def _limit(seconds, depth):
    return chess.engine.Limit(depth=depth) if depth else chess.engine.Limit(time=seconds)


def stockfish_best(path, board, seconds, depth=None, threads=None, hash_mb=256):
    info = stockfish(path, threads, hash_mb).analyse(board, _limit(seconds, depth), multipv=1)[0]
    score = info["score"].pov(board.turn)
    return {
        "move": info["pv"][0],
        "pv": info["pv"],
        "score_cp": score.score(),
        "mate": score.mate(),
        "depth": info.get("depth"),
        "nodes": info.get("nodes"),
        "engine": "Stockfish",
    }


def stockfish_lines(path, board, seconds, depth=None, threads=None, hash_mb=256, n=None, fresh=False):
    """Score the top n legal moves (default: all of them) with MultiPV. Returns (candidates, info).
    fresh: clear the hash first, so a fixed-depth search on one thread always gives the same result."""
    n = n or board.legal_moves.count()
    infos = stockfish(path, threads, hash_mb).analyse(board, _limit(seconds, depth), multipv=n,
                                                      game=object() if fresh else None)
    cands = []
    for info in infos:
        if not info.get("pv"):
            continue
        score = info["score"].pov(board.turn)
        cands.append({"move": info["pv"][0], "pv": info["pv"], "score_cp": score.score(),
                      "mate": score.mate(), "value": score.score(mate_score=MATE)})
    meta = {"depth": max((i.get("depth") or 0) for i in infos),
            "nodes": max((i.get("nodes") or 0) for i in infos), "engine": "Stockfish"}
    return cands, meta


def top_moves(board, n=3, seconds=0.1, engine_path=None, use_stockfish=True):
    """A quick look at the n best moves, best first, each with a "value" in centipawns.
    Used to judge how hard a position is. Empty list without Stockfish."""
    path = find_stockfish(engine_path) if use_stockfish else None
    if not path or board.is_game_over():
        return []
    cands, _ = stockfish_lines(path, board, seconds, n=min(n, board.legal_moves.count()))
    return sorted(cands, key=lambda c: -c["value"])


def move_value(board, move=None, depth=10, seconds=0.1, engine_path=None, use_stockfish=True):
    """A quick look at how good `move` is (None: the best move): centipawns for the side
    to move (mates as +-MATE). Stockfish searches to `depth`; the built-in engine for `seconds`."""
    path = find_stockfish(engine_path) if use_stockfish else None
    if path:
        info = stockfish(path).analyse(board, chess.engine.Limit(depth=depth),
                                       root_moves=[move] if move else None)
        return info["score"].pov(board.turn).score(mate_score=MATE)
    if move is None:
        r = Searcher().search(board, seconds, depth)
        return r["score_cp"] if r["mate"] is None else MATE if r["mate"] > 0 else -MATE
    after = board.copy(stack=False)
    after.push(move)
    if after.is_game_over():
        return MATE if after.is_checkmate() else 0
    r = Searcher().search(after, seconds, depth)
    return -(r["score_cp"] if r["mate"] is None else MATE if r["mate"] > 0 else -MATE)


# --------------------------------------------------------------------------- #
# Built-in engine: evaluation
# --------------------------------------------------------------------------- #
MG_VALUE = [0, 82, 337, 365, 477, 1025, 0]
EG_VALUE = [0, 94, 281, 297, 512, 936, 0]
PHASE_W = [0, 0, 1, 1, 2, 4, 0]

# Piece-square tables, written as seen from White with rank 8 on top.
PST = {
    chess.PAWN: [
        0, 0, 0, 0, 0, 0, 0, 0,
        50, 50, 50, 50, 50, 50, 50, 50,
        10, 10, 20, 30, 30, 20, 10, 10,
        5, 5, 10, 25, 25, 10, 5, 5,
        0, 0, 0, 20, 20, 0, 0, 0,
        5, -5, -10, 0, 0, -10, -5, 5,
        5, 10, 10, -20, -20, 10, 10, 5,
        0, 0, 0, 0, 0, 0, 0, 0],
    chess.KNIGHT: [
        -50, -40, -30, -30, -30, -30, -40, -50,
        -40, -20, 0, 0, 0, 0, -20, -40,
        -30, 0, 10, 15, 15, 10, 0, -30,
        -30, 5, 15, 20, 20, 15, 5, -30,
        -30, 0, 15, 20, 20, 15, 0, -30,
        -30, 5, 10, 15, 15, 10, 5, -30,
        -40, -20, 0, 5, 5, 0, -20, -40,
        -50, -40, -30, -30, -30, -30, -40, -50],
    chess.BISHOP: [
        -20, -10, -10, -10, -10, -10, -10, -20,
        -10, 0, 0, 0, 0, 0, 0, -10,
        -10, 0, 5, 10, 10, 5, 0, -10,
        -10, 5, 5, 10, 10, 5, 5, -10,
        -10, 0, 10, 10, 10, 10, 0, -10,
        -10, 10, 10, 10, 10, 10, 10, -10,
        -10, 5, 0, 0, 0, 0, 5, -10,
        -20, -10, -10, -10, -10, -10, -10, -20],
    chess.ROOK: [
        0, 0, 0, 0, 0, 0, 0, 0,
        5, 10, 10, 10, 10, 10, 10, 5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        -5, 0, 0, 0, 0, 0, 0, -5,
        0, 0, 0, 5, 5, 0, 0, 0],
    chess.QUEEN: [
        -20, -10, -10, -5, -5, -10, -10, -20,
        -10, 0, 0, 0, 0, 0, 0, -10,
        -10, 0, 5, 5, 5, 5, 0, -10,
        -5, 0, 5, 5, 5, 5, 0, -5,
        0, 0, 5, 5, 5, 5, 0, -5,
        -10, 5, 5, 5, 5, 5, 0, -10,
        -10, 0, 5, 0, 0, 0, 0, -10,
        -20, -10, -10, -5, -5, -10, -10, -20],
    chess.KING: [
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -20, -30, -30, -40, -40, -30, -30, -20,
        -10, -20, -20, -20, -20, -20, -20, -10,
        20, 20, 0, 0, 0, 0, 20, 20,
        20, 30, 10, 0, 0, 10, 30, 20],
}
KING_EG = [
    -50, -40, -30, -20, -20, -30, -40, -50,
    -30, -20, -10, 0, 0, -10, -20, -30,
    -30, -10, 20, 30, 30, 20, -10, -30,
    -30, -10, 30, 40, 40, 30, -10, -30,
    -30, -10, 30, 40, 40, 30, -10, -30,
    -30, -10, 20, 30, 30, 20, -10, -30,
    -30, -30, 0, 0, 0, 0, -30, -30,
    -50, -30, -30, -30, -30, -30, -30, -50]
PAWN_EG_RANK = [0, 0, 5, 10, 20, 35, 60, 0]
PASSED_BONUS = [0, 5, 10, 20, 35, 60, 100, 0]

# MG[color][piece_type][square], EG likewise (piece value included)
MG = [[[0] * 64 for _ in range(7)] for _ in range(2)]
EG = [[[0] * 64 for _ in range(7)] for _ in range(2)]
for _pt in range(1, 7):
    for _sq in range(64):
        for _color in (chess.WHITE, chess.BLACK):
            idx = _sq ^ 56 if _color == chess.WHITE else _sq  # table is drawn rank 8 first
            rel_rank = chess.square_rank(_sq) if _color == chess.WHITE else 7 - chess.square_rank(_sq)
            mg = MG_VALUE[_pt] + PST[_pt][idx]
            if _pt == chess.KING:
                eg = KING_EG[idx]
            elif _pt == chess.PAWN:
                eg = EG_VALUE[_pt] + PAWN_EG_RANK[rel_rank]
            else:
                eg = EG_VALUE[_pt] + PST[_pt][idx] // 2
            MG[_color][_pt][_sq] = mg
            EG[_color][_pt][_sq] = eg

FILE_MASKS = [chess.BB_FILES[f] for f in range(8)]
ADJ_FILES = [(FILE_MASKS[f - 1] if f > 0 else 0) | (FILE_MASKS[f + 1] if f < 7 else 0) for f in range(8)]
PASSED_MASK = [[0] * 64 for _ in range(2)]
for _sq in range(64):
    f, r = chess.square_file(_sq), chess.square_rank(_sq)
    span = FILE_MASKS[f] | ADJ_FILES[f]
    ahead_w = 0
    for rr in range(r + 1, 8):
        ahead_w |= chess.BB_RANKS[rr]
    ahead_b = 0
    for rr in range(0, r):
        ahead_b |= chess.BB_RANKS[rr]
    PASSED_MASK[chess.WHITE][_sq] = span & ahead_w
    PASSED_MASK[chess.BLACK][_sq] = span & ahead_b


def evaluate(board):
    """Static evaluation in centipawns from the side to move's point of view."""
    mg = [0, 0]
    eg = [0, 0]
    phase = 0
    pawns = [board.pieces_mask(chess.PAWN, chess.BLACK), board.pieces_mask(chess.PAWN, chess.WHITE)]
    for color in (chess.WHITE, chess.BLACK):
        mgt, egt = MG[color], EG[color]
        m = e = 0
        for pt in range(1, 7):
            bb = board.pieces_mask(pt, color)
            if not bb:
                continue
            phase += PHASE_W[pt] * chess.popcount(bb)
            tm, te = mgt[pt], egt[pt]
            for sq in chess.scan_forward(bb):
                m += tm[sq]
                e += te[sq]
        own, opp = pawns[color], pawns[not color]
        # pawn structure
        for sq in chess.scan_forward(own):
            f = sq & 7
            if not own & ADJ_FILES[f]:
                m -= 10; e -= 15
            if chess.popcount(own & FILE_MASKS[f]) > 1:
                m -= 5; e -= 10
            if not opp & PASSED_MASK[color][sq]:
                rr = (sq >> 3) if color else 7 - (sq >> 3)
                m += PASSED_BONUS[rr] // 2; e += PASSED_BONUS[rr]
        # rooks on (semi-)open files
        for sq in chess.scan_forward(board.pieces_mask(chess.ROOK, color)):
            fm = FILE_MASKS[sq & 7]
            if not own & fm:
                bonus = 10 if opp & fm else 20
                m += bonus; e += bonus // 2
        if chess.popcount(board.pieces_mask(chess.BISHOP, color)) >= 2:
            m += 30; e += 45
        mg[color] = m
        eg[color] = e
    phase = min(phase, 24)
    w = ((mg[1] - mg[0]) * phase + (eg[1] - eg[0]) * (24 - phase)) // 24
    w += 10 if board.turn else -10  # tempo
    return w if board.turn else -w


# --------------------------------------------------------------------------- #
# Built-in engine: search
# --------------------------------------------------------------------------- #
EXACT, LOWER, UPPER = 0, 1, 2
ORDER_VAL = [0, 1, 3, 3, 5, 9, 20]


class Searcher:
    def __init__(self):
        self.tt = {}
        self.history = {}

    def _key(self, board):
        return board._transposition_key()

    def _has_pieces(self, board, color):
        return bool(board.occupied_co[color] & ~board.pawns & ~board.kings)

    def _order(self, board, moves, tt_move, ply):
        killers = self.killers[min(ply, 129)]
        def score(m):
            if m == tt_move:
                return 10 ** 7
            if board.is_capture(m):
                victim = board.piece_type_at(m.to_square) or chess.PAWN
                return 10 ** 6 + 10 * ORDER_VAL[victim] - ORDER_VAL[board.piece_type_at(m.from_square)]
            if m.promotion:
                return 9 * 10 ** 5 + m.promotion
            if m in killers:
                return 8 * 10 ** 5
            return self.history.get((board.turn, m.from_square, m.to_square), 0)
        return sorted(moves, key=score, reverse=True)

    def _tick(self):
        self.nodes += 1
        if not self.nodes & 1023 and time.time() > self.deadline:
            self.stopped = True

    def qsearch(self, board, alpha, beta, ply):
        self._tick()
        if self.stopped:
            return 0
        if board.is_check():
            moves = list(board.legal_moves)
            if not moves:
                return -MATE + ply
            stand = -INF
        else:
            stand = evaluate(board)
            if stand >= beta:
                return stand
            if stand > alpha:
                alpha = stand
            if ply > 60:
                return stand
            moves = [m for m in board.generate_legal_captures()] + \
                    [m for m in board.legal_moves if m.promotion == chess.QUEEN and not board.is_capture(m)]
        best = stand
        for m in self._order(board, moves, None, min(ply, 129)):
            if stand > -INF and not m.promotion:
                victim = board.piece_type_at(m.to_square) or chess.PAWN
                if stand + MG_VALUE[victim] + 200 < alpha:
                    continue  # delta pruning
            board.push(m)
            score = -self.qsearch(board, -beta, -alpha, ply + 1)
            board.pop()
            if self.stopped:
                return 0
            if score > best:
                best = score
                if score > alpha:
                    alpha = score
                    if alpha >= beta:
                        break
        return best

    def negamax(self, board, depth, alpha, beta, ply, can_null=True):
        self._tick()
        if self.stopped:
            return 0
        key = self._key(board)
        if ply > 0:
            if board.halfmove_clock >= 100 or self.path.get(key, 0) > 0:
                return 0
            # mate distance pruning
            alpha, beta = max(alpha, -MATE + ply), min(beta, MATE - ply - 1)
            if alpha >= beta:
                return alpha
        in_check = board.is_check()
        if in_check:
            depth += 1
        if depth <= 0:
            return self.qsearch(board, alpha, beta, ply)

        tt_move = None
        entry = self.tt.get(key)
        if entry:
            e_depth, flag, e_score, tt_move = entry
            if e_depth >= depth and ply > 0:
                if e_score > MATE - 1000:
                    e_score -= ply
                elif e_score < -MATE + 1000:
                    e_score += ply
                if flag == EXACT or (flag == LOWER and e_score >= beta) or (flag == UPPER and e_score <= alpha):
                    return e_score

        pv_node = beta - alpha > 1
        if not in_check and not pv_node:
            static = evaluate(board)
            if depth <= 3 and static - 120 * depth >= beta and abs(beta) < MATE - 1000:
                return static  # reverse futility pruning
            if can_null and depth >= 3 and static >= beta and self._has_pieces(board, board.turn):
                board.push(chess.Move.null())
                score = -self.negamax(board, depth - 3 - (depth > 6), -beta, -beta + 1, ply + 1, False)
                board.pop()
                if self.stopped:
                    return 0
                if score >= beta and score < MATE - 1000:
                    return beta

        moves = list(board.legal_moves)
        if not moves:
            return -MATE + ply if in_check else 0
        moves = self._order(board, moves, tt_move, ply)

        self.path[key] = self.path.get(key, 0) + 1
        best, best_move, orig_alpha = -INF, moves[0], alpha
        for i, m in enumerate(moves):
            quiet = not board.is_capture(m) and not m.promotion
            board.push(m)
            if i == 0:
                score = -self.negamax(board, depth - 1, -beta, -alpha, ply + 1)
            else:
                r = 0
                if depth >= 3 and i >= 3 and quiet and not in_check and not board.is_check():
                    r = 1 + (i >= 8) + (depth >= 6 and i >= 15)
                score = -self.negamax(board, depth - 1 - r, -alpha - 1, -alpha, ply + 1)
                if score > alpha and (r or score < beta):
                    score = -self.negamax(board, depth - 1, -beta, -alpha, ply + 1)
            board.pop()
            if self.stopped:
                break
            if score > best:
                best, best_move = score, m
                if score > alpha:
                    alpha = score
                    if alpha >= beta:
                        if quiet:
                            k = self.killers[min(ply, 129)]
                            if m != k[0]:
                                k[1], k[0] = k[0], m
                            hk = (board.turn, m.from_square, m.to_square)
                            self.history[hk] = self.history.get(hk, 0) + depth * depth
                        break
        self.path[key] -= 1
        if self.stopped:
            return 0

        flag = UPPER if best <= orig_alpha else LOWER if best >= beta else EXACT
        stored = best + ply if best > MATE - 1000 else best - ply if best < -MATE + 1000 else best
        self.tt[key] = (depth, flag, stored, best_move)
        return best

    def pv(self, board, first, max_len=12):
        line, b = [first], board.copy(stack=False)
        b.push(first)
        seen = {self._key(b)}
        while len(line) < max_len:
            e = self.tt.get(self._key(b))
            if not e or e[3] not in b.legal_moves:
                break
            b.push(e[3])
            line.append(e[3])
            k = self._key(b)
            if k in seen:
                break
            seen.add(k)
        return line

    def search(self, board, seconds=3.0, max_depth=64, on_iter=None):
        board = board.copy(stack=False)
        start = time.time()
        self.deadline = start + seconds
        self.nodes = 0
        self.stopped = False
        self.killers = [[None, None] for _ in range(130)]
        self.path = {}
        if len(self.tt) > 2_000_000:
            self.tt.clear()
        legal = list(board.legal_moves)
        if not legal:
            raise ValueError("no legal moves in this position")
        result = {"move": legal[0], "pv": [legal[0]], "score_cp": 0, "mate": None, "depth": 0}
        score = 0
        for depth in range(1, max_depth + 1):
            window = 40
            alpha, beta = (score - window, score + window) if depth >= 4 else (-INF, INF)
            while True:
                s = self.negamax(board, depth, alpha, beta, 0)
                if self.stopped:
                    break
                if s <= alpha:
                    alpha -= window * 4
                elif s >= beta:
                    beta += window * 4
                else:
                    break
                window *= 4
            if self.stopped:
                break
            score = s
            move = self.tt[self._key(board)][3]
            result = {"move": move, "pv": self.pv(board, move), "depth": depth, "nodes": self.nodes,
                      "score_cp": None if abs(s) > MATE - 1000 else s,
                      "mate": ((MATE - s + 1) // 2 if s > 0 else -((MATE + s) // 2)) if abs(s) > MATE - 1000 else None}
            if on_iter:
                on_iter(result)
            if abs(s) > MATE - 1000 or time.time() - start > seconds * 0.45 or len(legal) == 1:
                break
        result["nodes"] = self.nodes
        result["engine"] = "built-in"
        return result

    def score_root(self, board, seconds=3.0, max_depth=64, on_iter=None, margin=INF):
        """Give every legal move its own score (slower than search(), but needed to
        know how bad the non-best moves are). Moves more than `margin` below the best
        only get an upper bound, which is enough to know they won't be picked.
        Returns (candidates, info)."""
        board = board.copy(stack=False)
        start = time.time()
        self.deadline = start + seconds
        self.nodes = 0
        self.stopped = False
        self.killers = [[None, None] for _ in range(130)]
        if len(self.tt) > 2_000_000:
            self.tt.clear()
        legal = list(board.legal_moves)
        if not legal:
            raise ValueError("no legal moves in this position")
        root = self._key(board)
        scores, done = {}, 0
        order = legal
        for depth in range(1, max_depth + 1):
            new = {}
            self.path = {root: 1}
            for m in order:
                alpha = max(new.values()) - margin if new else -INF
                board.push(m)
                s = -self.negamax(board, depth - 1, -INF, -alpha, 1)
                board.pop()
                if self.stopped:
                    break
                new[m] = s
            if self.stopped:
                if not done:  # not even depth 1 finished: use what we have
                    scores.update(new)
                break
            scores, done = new, depth
            order = sorted(legal, key=scores.get, reverse=True)
            if on_iter:
                top = order[0]
                on_iter({"depth": depth, "pv": self.pv(board, top), **score_fields(scores[top])})
            if time.time() - start > seconds * 0.45 or len(legal) == 1:
                break
        cands = [{"move": m, "pv": self.pv(board, m), "value": s, **score_fields(s)}
                 for m, s in scores.items()]
        return cands, {"depth": done, "nodes": self.nodes, "engine": "built-in"}


def score_fields(s):
    if abs(s) > MATE - 1000:
        return {"score_cp": None, "mate": (MATE - s + 1) // 2 if s > 0 else -((MATE + s) // 2)}
    return {"score_cp": s, "mate": None}


# --------------------------------------------------------------------------- #
# Accuracy: sometimes play a worse move on purpose
# --------------------------------------------------------------------------- #
def temperature(accuracy):
    """How many centipawns of loss the bot shrugs off. 100 -> 0 (always best),
    90 -> ~7, 75 -> 30, 50 -> 150, 25 -> ~630, 0 -> ~2550 (close to random)."""
    return 10 * (2 ** ((100 - accuracy) / 12.5) - 1)


def pick_move(cands, accuracy, rng=random):
    """Pick a move: each one's chance is exp(-loss / temperature), where loss is
    how many centipawns worse than the best move it is."""
    best = max(cands, key=lambda c: c["value"])
    if accuracy >= 100:
        return best, best
    t = temperature(accuracy)
    weights = [math.exp(-min(best["value"] - c["value"], 3000) / t) for c in cands]
    return rng.choices(cands, weights)[0], best


def best_move(board, seconds=3.0, depth=None, engine_path=None, use_stockfish=True, on_iter=None,
              accuracy=100):
    path = find_stockfish(engine_path) if use_stockfish else None
    if accuracy >= 100:
        if path:
            return stockfish_best(path, board, seconds, depth)
        return Searcher().search(board, seconds, depth or 64, on_iter)
    if path:
        cands, meta = stockfish_lines(path, board, seconds, depth)
    else:
        # moves losing more than 8x the temperature have < 0.04% chance: no need to score them exactly
        margin = max(100, 8 * temperature(accuracy))
        cands, meta = Searcher().score_root(board, seconds, depth or 64, on_iter, margin)
    chosen, best = pick_move(cands, accuracy)
    return {**chosen, **meta, "best": best, "loss_cp": best["value"] - chosen["value"],
            "accuracy": accuracy, "cands": cands}
