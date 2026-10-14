"""Chess bot that reads a board from a screenshot and finds the best move.

  python chessbot.py analyze board.png            # read a screenshot file
  python chessbot.py analyze screen               # grab the screen right now
  python chessbot.py analyze board.png --turn b   # force side to move
  python chessbot.py fen "<FEN>"                  # skip vision, just think
  python chessbot.py calibrate start.png --fen "<FEN of that screenshot>"
"""
import argparse
import os
import sys
import time

import chess
import cv2
import numpy as np

import board_reader as br
import engine
import human_index


def grab_screen(monitor=0):
    import mss
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[monitor])
    return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)


def draw_board(board, white_bottom=True):
    """Letters: UPPERCASE = white, lowercase = black."""
    ranks = range(7, -1, -1) if white_bottom else range(8)
    files = range(8) if white_bottom else range(7, -1, -1)
    lines = []
    for r in ranks:
        row = [str(board.piece_at(chess.square(f, r)) or ".") for f in files]
        lines.append(f"  {r + 1}  " + " ".join(row))
    lines.append("     " + " ".join(chess.FILE_NAMES[f] for f in files))
    return "\n".join(lines)


def describe_score(r):
    if r["mate"] is not None:
        return f"mate in {r['mate']}" if r["mate"] > 0 else f"getting mated in {-r['mate']}"
    return f"{r['score_cp'] / 100:+.2f} (side to move)"


def think(board, args, last_to=None):
    if board.is_game_over():
        print(f"Game over: {board.result()}")
        return None
    def on_iter(r):
        print(f"  depth {r['depth']:2d}  {describe_score(r):>22}  {board.variation_san(r['pv'][:8])}", flush=True)
    t = time.time()
    r = engine.best_move(board, seconds=args.time, depth=args.depth, engine_path=args.engine,
                         use_stockfish=not args.no_stockfish, on_iter=on_iter, accuracy=args.accuracy)
    print(f"\nEngine: {r['engine']}  depth {r['depth']}  {r.get('nodes') or 0:,} nodes  {time.time() - t:.1f}s")
    best = r.get("best")
    if best and best["move"] != r["move"]:
        print(f"Best:   {board.san(best['move'])}  {describe_score(best)}")
        print(f"Chosen: {board.san(r['move'])}  {describe_score(r)}  "
              f"(costs {r['loss_cp'] / 100:.2f} pawns, accuracy {args.accuracy:g}%)")
        print(f"Line:   {board.variation_san(r['pv'][:10])}")
        print_human_index(board, r, args, last_to)
        print(f"\n>>> PLAY: {board.san(r['move'])}  ({r['move'].uci()})   [not the best move on purpose]\n")
        return r
    print(f"Eval:   {describe_score(r)}")
    print(f"Line:   {board.variation_san(r['pv'][:10])}")
    print_human_index(board, r, args, last_to)
    print(f"\n>>> BEST MOVE: {board.san(r['move'])}  ({r['move'].uci()})\n")
    return r


def print_human_index(board, r, args, last_to=None):
    if args.human_index is None:
        return
    move = r["move"]
    chance, lab, top = human_index.report(board, move, args.human_index, last_to,
                                          engine_path=args.engine, use_stockfish=not args.no_stockfish,
                                          cands=r.get("cands"))
    print(f"Human:  {chance:.0%} chance a {args.human_index}-rated player picks {board.san(move)} ({lab})")
    print(f"        most human: {', '.join(f'{san} {p:.0%}' for san, p in top)}")


def accuracy_arg(s):
    v = float(s)
    if not 0 <= v <= 100:
        raise argparse.ArgumentTypeError("accuracy must be between 0 and 100")
    return v


def cmd_analyze(args):
    img = grab_screen(args.monitor) if args.image == "screen" else cv2.imread(args.image)
    if img is None:
        sys.exit(f"cannot read image: {args.image}")
    turn = None if args.turn is None else args.turn == "w"
    white_bottom = None if args.side is None else args.side == "white"
    try:
        info = br.read_board(img, br.load_templates(args.templates), white_bottom, turn)
    except br.BoardNotFound as e:
        sys.exit(f"Error: {e}")
    board = info["board"]
    print(draw_board(board, info["white_bottom"]))
    print(f"\nFEN:          {board.fen()}")
    print(f"Orientation:  {'white' if info['white_bottom'] else 'black'} at the bottom")
    print(f"To move:      {'white' if board.turn else 'black'}  [{info['turn_source']}]")
    if info["low_confidence"]:
        print(f"Warning: unsure about {len(info['low_confidence'])} square(s): {info['low_confidence']}")
    if not board.is_valid():
        print(f"Warning: position looks illegal ({board.status()!r}) - check the FEN above.")
        if not any(board.legal_moves):
            return
    print(f"Analysis:     https://lichess.org/analysis/standard/{board.fen().replace(' ', '_')}\n")
    r = think(board, args, info["last_move"][1] if info["last_move"] else None)
    if r and args.out != "none":
        out = args.out or ("screen_move.png" if args.image == "screen"
                           else os.path.splitext(args.image)[0] + "_move.png")
        ann = img.copy()
        g, wb = info["grid"], info["white_bottom"]
        a = br.square_center(g, r["move"].from_square, wb)
        b = br.square_center(g, r["move"].to_square, wb)
        cv2.arrowedLine(ann, a, b, (0, 140, 255), max(3, int(g["size"] / 9)), cv2.LINE_AA, tipLength=0.25)
        cv2.imwrite(out, ann)
        print(f"Saved move arrow to {out}")


def cmd_fen(args):
    think(chess.Board(args.fen), args)


def cmd_calibrate(args):
    img = cv2.imread(args.image)
    if img is None:
        sys.exit(f"cannot read image: {args.image}")
    learned, meta = br.calibrate(img, args.fen, args.templates, args.side != "black")
    print(f"Learned templates for: {' '.join(learned)}  -> {args.templates}")
    missing = set(br.SYMBOLS) - set(learned)
    if missing:
        print(f"Missing pieces (use a screenshot containing them): {' '.join(sorted(missing))}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p =argparse.ArgumentParser(description="Screenshot-reading chess bot")
    sub = p.add_subparsers(dest="cmd", required=True)

    def engine_opts(sp):
        sp.add_argument("--time", type=float, default=3.0, help="seconds to think (default 3)")
        sp.add_argument("--depth", type=int, help="fixed search depth instead of time")
        sp.add_argument("--engine", help="path to a UCI engine such as stockfish.exe")
        sp.add_argument("--no-stockfish", action="store_true", help="always use the built-in engine")
        sp.add_argument("--accuracy", type=accuracy_arg, default=100,
                        help="0-100: how accurately to play (default 100 = always the best move; "
                             "lower = more frequent and bigger mistakes)")
        sp.add_argument("--human-index", nargs="?", type=int, const=1500, metavar="RATING",
                        help="also show the chance a human of this rating (default 1500) plays the move")

    a = sub.add_parser("analyze", help="read a screenshot (or 'screen') and find the best move")
    a.add_argument("image", help="image path, or 'screen' to capture the screen")
    a.add_argument("--turn", choices=["w", "b"], help="side to move (default: auto)")
    a.add_argument("--side", choices=["white", "black"], help="which colour is at the bottom (default: auto)")
    a.add_argument("--monitor", type=int, default=0, help="monitor for 'screen' (0 = all, 1 = primary)")
    a.add_argument("--templates", default=br.DEFAULT_TEMPLATES)
    a.add_argument("--out", help="where to save the image with the move arrow ('none' to skip)")
    engine_opts(a)
    a.set_defaults(func=cmd_analyze)

    f = sub.add_parser("fen", help="find the best move for a FEN")
    f.add_argument("fen")
    engine_opts(f)
    f.set_defaults(func=cmd_fen)

    c = sub.add_parser("calibrate", help="learn piece images for a new board/piece theme")
    c.add_argument("image")
    c.add_argument("--fen", default=chess.STARTING_FEN, help="position shown in the image (default: start)")
    c.add_argument("--side", choices=["white", "black"], default="white", help="colour at the bottom")
    c.add_argument("--templates", default=br.DEFAULT_TEMPLATES)
    c.set_defaults(func=cmd_calibrate)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
