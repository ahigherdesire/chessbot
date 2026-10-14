"""Maia: neural networks trained on millions of lichess games to predict the move a human
of a given rating plays (maia-1100 ... maia-1900, by the CSSLab at the University of
Toronto), run through the lc0 engine. `python get_maia.py` downloads both.

Only the network's first impression is used (one node, no search): the chance it gives
each move is the chance a player of that rating plays it, mistakes included.
"""
import glob
import os
import re
import shutil
import subprocess
import sys

import chess
import chess.engine

HERE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))   # the .exe's folder when built
RATINGS = list(range(1100, 2000, 100))
_ENGINES = {}
_LINE = re.compile(r"^(\S+)\s.*\(P:\s*([\d.]+)%\)")


def find_lc0():
    cands = [os.environ.get("LC0_PATH"), shutil.which("lc0"), os.path.join(HERE, "engines", "lc0", "lc0.exe")]
    cands += sorted(glob.glob(os.path.join(HERE, "engines", "**", "lc0*"), recursive=True))
    for c in cands:
        if c and os.path.isfile(c) and (c.endswith(".exe") or os.access(c, os.X_OK)):
            return c
    return None


def weights(rating):
    path = os.path.join(HERE, "engines", "maia", f"maia-{rating}.pb.gz")
    return path if os.path.isfile(path) else None


def available():
    """lc0 and every Maia network are there."""
    return find_lc0() is not None and all(weights(r) for r in RATINGS)


def _engine(rating):
    """One lc0 process per network, kept open (loading a network takes a moment)."""
    eng = _ENGINES.get(rating)
    if eng is not None:
        try:
            eng.ping()
            return eng
        except (chess.engine.EngineError, chess.engine.EngineTerminatedError):
            pass
    eng = chess.engine.SimpleEngine.popen_uci(find_lc0(), stderr=subprocess.DEVNULL,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # PolicyTemperature 1: the network's own chances (lc0 flattens them by default)
    eng.configure({"WeightsFile": weights(rating), "PolicyTemperature": "1.0", "VerboseMoveStats": True,
                   "Threads": 1, "MinibatchSize": 1})
    _ENGINES[rating] = eng
    return eng


def _policy(board, rating):
    out = {}
    with _engine(rating).analysis(board, chess.engine.Limit(nodes=1)) as lines:
        for info in lines:
            m = _LINE.match(info.get("string", ""))
            if m and m.group(1) != "node":
                try:
                    out[board.parse_uci(m.group(1))] = float(m.group(2)) / 100
                except ValueError:
                    pass
    total = sum(out.values()) or 1.0
    return {move: p / total for move, p in out.items()}


def policy(board, rating=1500):
    """{move: chance} that a human of `rating` plays each legal move. Between two networks
    (1550) the chances are blended; outside 1100-1900 the nearest network is used."""
    rating = min(max(rating, RATINGS[0]), RATINGS[-1])
    lo = RATINGS[0] + (rating - RATINGS[0]) // 100 * 100
    if lo == rating or lo == RATINGS[-1]:
        return _policy(board, lo)
    f = (rating - lo) / 100
    a, b = _policy(board, lo), _policy(board, lo + 100)
    return {m: (1 - f) * a.get(m, 0) + f * b.get(m, 0) for m in set(a) | set(b)}


def close():
    for eng in _ENGINES.values():
        try:
            eng.quit()
        except Exception:
            pass
    _ENGINES.clear()


# like engine.close_engines: close before Python waits for the engines' threads at exit
try:
    import threading
    threading._register_atexit(close)
except Exception:
    import atexit
    atexit.register(close)
