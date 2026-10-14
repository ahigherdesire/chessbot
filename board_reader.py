"""Read a chess position from a screenshot.

Pipeline:
  1. find the board: try pairs of dominant colours, keep the pair that forms an
     8x8 checker pattern, then fit the exact grid (works even if the board is
     slightly cropped at the image edges);
  2. cut out the 64 squares, separate each piece from its square background;
  3. decide piece colour from brightness and piece type by template matching;
  4. infer orientation, side to move (from last-move highlights), castling rights.
"""
import json
import os
import sys

import chess
import cv2
import numpy as np

FEAT = 64  # every square is normalised to FEAT x FEAT pixels
SYMBOLS = "PNBRQKpnbrqk"
HERE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))   # the .exe's folder when built
DEFAULT_TEMPLATES = os.path.join(HERE, "templates", "default")


class BoardNotFound(Exception):
    pass


# --------------------------------------------------------------------------- #
# Board detection
# --------------------------------------------------------------------------- #
def _dominant_colors(img, k=8):
    scale = min(1.0, 600.0 / max(img.shape[:2]))
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else img
    pix = small.reshape(-1, 3).astype(np.int32)
    q = pix >> 3
    keys = (q[:, 0] << 10) | (q[:, 1] << 5) | q[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    colors = []
    for i in np.argsort(-counts)[:60]:
        if counts[i] < 0.01 * len(pix):
            break
        c = pix[keys == vals[i]].mean(0)
        if all(np.abs(c - e).sum() > 30 for e in colors):
            colors.append(c)
        if len(colors) == k:
            break
    return colors


def _fit_axis(signal, lo, hi, s_min, s_max):
    """Find offset o and spacing s so the 9 lines o + k*s sit on the strongest edges."""
    sig = np.convolve(signal.astype(np.float64), [1, 2, 1], "same")
    sig = np.append(sig, 0.0)  # index n = "outside the image", scores 0
    n = len(sig) - 1
    offsets = np.arange(lo, hi, 0.5)
    ks = np.arange(9)
    best = (-1.0, None, None)
    for s in np.arange(s_min, s_max, 0.25):
        pos = np.round(offsets[:, None] + s * ks[None, :]).astype(int)
        pos[(pos < 0) | (pos >= n)] = n
        scores = sig[pos].sum(1)
        i = int(np.argmax(scores))
        if scores[i] > best[0]:
            best = (scores[i], offsets[i], s)
    return best[1], best[2]


def _try_pair(img, d_light, d_dark, tol=36):
    cls = np.full(img.shape[:2], -1, np.int8)
    cls[d_light < tol] = 0
    cls[(d_dark < tol) & (d_dark < d_light)] = 1
    # dilate so squares separated by an anti-aliased seam still connect
    mask = cv2.dilate((cls >= 0).astype(np.uint8), np.ones((5, 5), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n < 2:
        return None
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, area = stats[big]
    if min(w, h) < 80 or not (0.75 < w / h < 1.33):
        return None
    region = cls[y:y + h, x:x + w]
    # light<->dark transitions (allow one anti-aliased pixel in between)
    a, b = region[:, :-2], region[:, 2:]
    xflip = ((a >= 0) & (b >= 0) & (a != b)).sum(0)
    a, b = region[:-2, :], region[2:, :]
    yflip = ((a >= 0) & (b >= 0) & (a != b)).sum(1)
    xsig = np.zeros(img.shape[1]); xsig[x + 1:x + w - 1] = xflip
    ysig = np.zeros(img.shape[0]); ysig[y + 1:y + h - 1] = yflip
    s_est = max(w, h) / 8.0
    ox, sx = _fit_axis(xsig, x - 0.3 * s_est, x + 0.1 * s_est, s_est * 0.95, s_est * 1.08)
    oy, sy = _fit_axis(ysig, y - 0.3 * s_est, y + 0.1 * s_est, s_est * 0.95, s_est * 1.08)
    s = (sx + sy) / 2.0
    # checker score: top-left visual square is always light
    good = total = 0
    for r in range(8):
        for c in range(8):
            cx, cy = int(ox + (c + 0.08) * s), int(oy + (r + 0.08) * s)
            if 0 <= cx < img.shape[1] and 0 <= cy < img.shape[0]:
                total += 1
                good += cls[cy, cx] == (r + c) % 2
    if total < 48:
        return None
    return good / total, (ox, oy, s)


def find_board(img):
    """Return dict with grid origin/size and the light/dark square colours."""
    imgi = img.astype(np.int32)
    colors = _dominant_colors(img)
    dist = [np.abs(imgi - c).sum(2) for c in colors]  # distance map per colour, computed once
    best = None
    for i in range(len(colors)):
        for j in range(i + 1, len(colors)):
            c1, c2 = colors[i], colors[j]
            if np.abs(c1 - c2).sum() < 40:
                continue
            li, di = (i, j) if c1.sum() > c2.sum() else (j, i)
            light, dark = colors[li], colors[di]
            res = _try_pair(imgi, dist[li], dist[di])
            if res and (best is None or res[0] > best[0]):
                best = (res[0], res[1], light, dark)
    if best is None or best[0] < 0.7:
        raise BoardNotFound("could not find an 8x8 board in the image")
    score, (ox, oy, s), light, dark = best
    return {"x": ox, "y": oy, "size": s, "light": light, "dark": dark, "score": score}


def square_patches(img, grid):
    """Yield (row, col, FEAT x FEAT BGR patch) in visual order (row 0 = top)."""
    s = grid["size"]
    pad = int(s) + 2
    padded = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    for r in range(8):
        for c in range(8):
            x0 = int(round(grid["x"] + c * s)) + pad
            y0 = int(round(grid["y"] + r * s)) + pad
            crop = padded[y0:y0 + int(round(s)), x0:x0 + int(round(s))]
            yield r, c, cv2.resize(crop, (FEAT, FEAT), interpolation=cv2.INTER_AREA)


# --------------------------------------------------------------------------- #
# Square analysis
# --------------------------------------------------------------------------- #
def analyse_patch(patch):
    """Return (background colour, silhouette mask, feature image, white_fraction)."""
    p = patch.astype(np.int32)
    ring = np.concatenate([p[:3].reshape(-1, 3), p[-3:].reshape(-1, 3),
                           p[:, :3].reshape(-1, 3), p[:, -3:].reshape(-1, 3)])
    bg = np.median(ring, axis=0)
    raw = (np.abs(p - bg).sum(2) > 60).astype(np.uint8)
    b = 4
    raw[:b] = raw[-b:] = 0
    raw[:, :b] = raw[:, -b:] = 0
    raw = cv2.morphologyEx(raw, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    # keep only blobs centred in the middle of the square (drops coordinate labels)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(raw, connectivity=8)
    keep = np.zeros_like(raw)
    for k in range(1, n):
        cx, cy = cents[k]
        if stats[k, cv2.CC_STAT_AREA] >= 25 and 0.2 * FEAT < cx < 0.8 * FEAT and 0.2 * FEAT < cy < 0.85 * FEAT:
            keep[labels == k] = 1
    contours, _ = cv2.findContours(keep, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    sil = np.zeros_like(keep)
    cv2.drawContours(sil, contours, -1, 1, thickness=cv2.FILLED)
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    feat = np.where(sil > 0, 0.4 + 0.6 * gray, 0.0).astype(np.float32)
    area = sil.sum()
    white_frac = float((gray[sil > 0] > 0.7).mean()) if area else 0.0
    return bg, sil, feat, white_frac


def _match(feat, tmpl):
    m = 4
    res = cv2.matchTemplate(feat, tmpl[m:-m, m:-m], cv2.TM_CCOEFF_NORMED)
    return float(res.max())


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
def load_templates(path=DEFAULT_TEMPLATES):
    meta_path = os.path.join(path, "meta.json")
    if not os.path.exists(meta_path):
        raise FileNotFoundError(f"no templates in {path} - run the 'calibrate' command first")
    with open(meta_path) as f:
        meta = json.load(f)
    tmpls = {}
    for sym in SYMBOLS:
        fn = os.path.join(path, ("w" if sym.isupper() else "b") + sym.upper() + ".png")
        if os.path.exists(fn):
            tmpls[sym] = cv2.imread(fn, cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
    return tmpls, meta


def calibrate(img, fen, out_dir=DEFAULT_TEMPLATES, white_bottom=True):
    """Learn piece templates from a screenshot whose position (FEN) is known."""
    grid = find_board(img)
    board = chess.Board(fen.split()[0] + " w - - 0 1")
    feats = {s: [] for s in SYMBOLS}
    fracs = {"w": [], "b": [], "empty": []}
    areas = {"piece": [], "empty": []}
    for r, c, patch in square_patches(img, grid):
        sq = _visual_to_square(r, c, white_bottom)
        piece = board.piece_at(sq)
        _, sil, feat, wf = analyse_patch(patch)
        if piece is None:
            areas["empty"].append(sil.mean())
            continue
        areas["piece"].append(sil.mean())
        feats[piece.symbol()].append(feat)
        fracs["w" if piece.color else "b"].append(wf)
    os.makedirs(out_dir, exist_ok=True)
    learned = []
    for sym, lst in feats.items():
        if lst:
            t = np.mean(lst, axis=0)
            cv2.imwrite(os.path.join(out_dir, ("w" if sym.isupper() else "b") + sym.upper() + ".png"),
                        np.clip(t * 255, 0, 255).astype(np.uint8))
            learned.append(sym)
    meta = {
        "white_threshold": (min(fracs["w"]) + max(fracs["b"])) / 2 if fracs["w"] and fracs["b"] else 0.3,
        "empty_threshold": (max(areas["empty"] or [0]) + min(areas["piece"])) / 2,
    }
    old = os.path.join(out_dir, "meta.json")
    with open(old, "w") as f:
        json.dump(meta, f, indent=2)
    return learned, meta


# --------------------------------------------------------------------------- #
# Reading a position
# --------------------------------------------------------------------------- #
def _visual_to_square(r, c, white_bottom):
    return chess.square(c, 7 - r) if white_bottom else chess.square(7 - c, r)


def read_board(img, templates=None, white_bottom=None, turn=None, grid=None):
    """Recognise the position in `img` (BGR). Returns a dict with the board and diagnostics.
    grid: a board already found by find_board (skips the search, much faster)."""
    tmpls, meta = templates or load_templates()
    grid = grid or find_board(img)
    visual = [[None] * 8 for _ in range(8)]
    conf = [[1.0] * 8 for _ in range(8)]
    highlighted = []
    for r, c, patch in square_patches(img, grid):
        bg, sil, feat, wf = analyse_patch(patch)
        expected = grid["light"] if (r + c) % 2 == 0 else grid["dark"]
        if np.abs(bg - expected).sum() > 45:
            highlighted.append((r, c, _yellowness(bg)))
        if sil.mean() < meta["empty_threshold"]:
            continue
        is_white = wf > meta["white_threshold"]
        cands = [s for s in tmpls if s.isupper() == is_white] or list(tmpls)
        scores = {s: _match(feat, tmpls[s]) for s in cands}
        best = max(scores, key=scores.get)
        visual[r][c] = best
        conf[r][c] = scores[best]

    if white_bottom is None:
        white_bottom = _guess_orientation(visual)

    board = chess.Board(None)
    for r in range(8):
        for c in range(8):
            if visual[r][c]:
                board.set_piece_at(_visual_to_square(r, c, white_bottom), chess.Piece.from_symbol(visual[r][c]))

    # last-move highlight -> who moved last -> side to move (+ en passant)
    last_move = None
    # keep only the yellow last-move tint (drops e.g. the red "king in check" square)
    top = max((y for _, _, y in highlighted), default=0)
    highlighted = [(r, c) for r, c, y in highlighted if y >= 0.7 * top]
    occ = [(r, c) for r, c in highlighted if visual[r][c]]
    emp = [(r, c) for r, c in highlighted if not visual[r][c]]
    if len(occ) == 1 and len(emp) == 1:
        last_move = (_visual_to_square(*emp[0], white_bottom), _visual_to_square(*occ[0], white_bottom))
    turn_source = "argument"
    if turn is None:
        if last_move:
            mover = board.piece_at(last_move[1])
            turn = not mover.color
            turn_source = "last-move highlight"
        else:
            turn = chess.WHITE if white_bottom else chess.BLACK
            turn_source = "assumed: side at the bottom"
    board.turn = turn

    if last_move:
        frm, to = last_move
        p = board.piece_at(to)
        if p.piece_type == chess.PAWN and chess.square_file(frm) == chess.square_file(to) \
                and abs(chess.square_rank(frm) - chess.square_rank(to)) == 2:
            board.ep_square = (frm + to) // 2

    board.castling_rights = _infer_castling(board)
    if not board.is_valid() and turn_source != "argument":
        board.turn = not board.turn  # e.g. side not to move is in check
        if board.is_valid():
            turn_source += " (flipped: original was illegal)"
        else:
            board.turn = not board.turn

    return {
        "board": board,
        "grid": grid,
        "white_bottom": white_bottom,
        "turn_source": turn_source,
        "last_move": last_move,
        "low_confidence": [(r, c, visual[r][c], round(conf[r][c], 2))
                           for r in range(8) for c in range(8) if visual[r][c] and conf[r][c] < 0.6],
    }


def _yellowness(bg):
    """How yellow a BGR colour is: red and green high, blue low."""
    return (float(bg[1]) + float(bg[2])) / 2 - float(bg[0])


def _guess_orientation(visual):
    def rows(sym):
        return [r for r in range(8) for c in range(8) if visual[r][c] == sym]
    wp, bp = rows("P"), rows("p")
    if wp and bp:
        return np.mean(wp) >= np.mean(bp)
    wk, bk = rows("K"), rows("k")
    if wk and bk:
        return wk[0] >= bk[0]
    return True


def _infer_castling(board):
    fen = ""
    def has(sq, sym):
        p = board.piece_at(sq)
        return p is not None and p.symbol() == sym
    if has(chess.E1, "K"):
        fen += "K" if has(chess.H1, "R") else ""
        fen += "Q" if has(chess.A1, "R") else ""
    if has(chess.E8, "k"):
        fen += "k" if has(chess.H8, "r") else ""
        fen += "q" if has(chess.A8, "r") else ""
    b = chess.Board(None)
    b.set_castling_fen(fen or "-")
    return b.castling_rights


def square_center(grid, sq, white_bottom):
    f, rk = chess.square_file(sq), chess.square_rank(sq)
    c, r = (f, 7 - rk) if white_bottom else (7 - f, rk)
    s = grid["size"]
    return int(grid["x"] + (c + 0.5) * s), int(grid["y"] + (r + 0.5) * s)
