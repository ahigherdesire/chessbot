"""Map every square of an on-screen chess board to screen coordinates.

Each of the 64 squares gets a name (a1..h8) and a box in screen pixels. With the
map you can move the mouse onto a square and click it by name, or ask which
square the mouse is over right now.

  python board_coords.py map                  # find the board, print every square's centre
  python board_coords.py watch                # live: show the square under the mouse
  python board_coords.py hover e4             # move the mouse onto e4
  python board_coords.py click e4             # move onto e4 and click it
  python board_coords.py move e2 e4 [--drag]  # click e2 then e4 (or drag e2 -> e4)
  python board_coords.py shell                # type squares, the mouse goes there

From code:

  bm = BoardMap.from_screen()
  bm.center("e4")      # (x, y) on screen
  bm.square_at(x, y)   # "e4" or None
  bm.click("e4")
"""
import argparse
import bisect
import random
import sys
import time
from dataclasses import dataclass

import chess
import cv2
import numpy as np

import board_reader as br

try:
    import pyautogui
    pyautogui.FAILSAFE = True   # slam the mouse into a screen corner to abort
    pyautogui.PAUSE = 0
except ImportError:
    pyautogui = None


@dataclass(frozen=True)
class Square:
    name: str       # "e4"
    left: int
    top: int
    right: int      # exclusive
    bottom: int     # exclusive
    center: tuple   # (x, y)

    def contains(self, x, y):
        return self.left <= x < self.right and self.top <= y < self.bottom


class BoardMap:
    """Screen coordinates for all 64 squares of one board."""

    def __init__(self, grid, white_bottom=True, origin=(0, 0)):
        """grid: {"x", "y", "size"} in image pixels (from board_reader.find_board).
        origin: screen position of the image's top-left pixel (monitor offset)."""
        self.white_bottom = white_bottom
        self.x = grid["x"] + origin[0]
        self.y = grid["y"] + origin[1]
        self.size = grid["size"]
        # the 9 grid lines on each axis, in whole screen pixels
        self.xs = [round(self.x + i * self.size) for i in range(9)]
        self.ys = [round(self.y + i * self.size) for i in range(9)]
        self.squares = {}
        for sq in chess.SQUARES:
            c, r = self._visual(sq)
            self.squares[chess.square_name(sq)] = Square(
                chess.square_name(sq), self.xs[c], self.ys[r], self.xs[c + 1], self.ys[r + 1],
                (round(self.x + (c + 0.5) * self.size), round(self.y + (r + 0.5) * self.size)),
            )

    # ---- building a map ------------------------------------------------- #
    @classmethod
    def from_image(cls, img, white_bottom=None, origin=(0, 0), templates=None):
        """Find the board in a BGR image. Orientation is read from the pieces unless given."""
        grid = br.find_board(img)
        if white_bottom is None:
            try:
                white_bottom = br.read_board(img, templates or br.load_templates())["white_bottom"]
            except Exception:
                white_bottom = True
        return cls(grid, white_bottom, origin)

    @classmethod
    def from_screen(cls, monitor=0, white_bottom=None, templates=None):
        """Screenshot a monitor (0 = all of them) and find the board on it."""
        img, origin = grab(monitor)
        return cls.from_image(img, white_bottom, origin, templates)

    # ---- lookups -------------------------------------------------------- #
    def _visual(self, sq):
        """(column, row) on screen, row 0 = top."""
        f, rk = chess.square_file(sq), chess.square_rank(sq)
        return (f, 7 - rk) if self.white_bottom else (7 - f, rk)

    def __getitem__(self, name):
        return self.squares[_name(name)]

    def __iter__(self):
        return iter(self.squares.values())

    def center(self, name):
        return self[name].center

    def square_at(self, x, y):
        """Name of the square at screen point (x, y), or None if off the board."""
        c = bisect.bisect_right(self.xs, x) - 1
        r = bisect.bisect_right(self.ys, y) - 1
        if not (0 <= c < 8 and 0 <= r < 8):
            return None
        sq = chess.square(c, 7 - r) if self.white_bottom else chess.square(7 - c, r)
        return chess.square_name(sq)

    def under_mouse(self):
        return self.square_at(*_gui().position())

    # ---- mouse ---------------------------------------------------------- #
    def _point(self, name, jitter):
        x, y = self.center(name)
        j = int(min(jitter, self.size * 0.3))  # never wander out of the square
        return x + random.randint(-j, j), y + random.randint(-j, j)

    def hover(self, name, duration=0.1, jitter=0):
        """Move the mouse onto a square. jitter: random offset in pixels from the centre."""
        x, y = self._point(name, jitter)
        _gui().moveTo(x, y, duration=duration, _pause=False)
        return x, y

    def click(self, name, duration=0.1, jitter=0):
        """Move onto a square and click it."""
        x, y = self.hover(name, duration, jitter)
        _gui().click(x, y)
        return x, y

    def move(self, frm, to, drag=False, delay=0.15, jitter=0):
        """Play a move: click from-square then to-square, or drag between them."""
        gui = _gui()
        if drag:
            self.hover(frm, jitter=jitter)
            gui.mouseDown()
            time.sleep(0.05)
            gui.moveTo(*self._point(to, jitter), duration=max(0.08, delay), _pause=False)
            gui.mouseUp()
        else:
            self.click(frm, jitter=jitter)
            time.sleep(delay)
            self.click(to, jitter=jitter)

    # ---- output --------------------------------------------------------- #
    def table(self):
        """The board as seen on screen, each square with its centre point."""
        rows = []
        for r in range(8):
            cells = []
            for c in range(8):
                name = self.square_at(self.x + (c + 0.5) * self.size, self.y + (r + 0.5) * self.size)
                x, y = self.center(name)
                cells.append(f"{name}({x},{y})")
            rows.append("  ".join(f"{cell:<15}" for cell in cells).rstrip())
        return "\n".join(rows)

    def draw(self, img, origin=(0, 0)):
        """Return a copy of `img` with every square outlined and labelled."""
        out = img.copy()
        ox, oy = origin
        scale = max(0.4, self.size / 110)
        for s in self:
            cv2.rectangle(out, (s.left - ox, s.top - oy), (s.right - ox - 1, s.bottom - oy - 1), (0, 140, 255), 1)
            cx, cy = s.center[0] - ox, s.center[1] - oy
            (tw, th), _ = cv2.getTextSize(s.name, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
            org = (cx - tw // 2, cy + th // 2)
            cv2.putText(out, s.name, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(out, s.name, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2, cv2.LINE_AA)
        return out


def grab(monitor=0):
    """Screenshot a monitor (0 = all). Returns (BGR image, screen position of its top-left pixel)."""
    import mss
    with mss.MSS() as sct:
        mon = sct.monitors[monitor]
        shot = sct.grab(mon)
    return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR), (mon["left"], mon["top"])


def _name(name):
    name = name.strip().lower()
    if name not in chess.SQUARE_NAMES:
        raise KeyError(f"not a square: {name!r} (use a1..h8)")
    return name


def _gui():
    if pyautogui is None:
        raise RuntimeError("pip install pyautogui")
    return pyautogui


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #
def _load(args):
    wb = None if args.side is None else args.side == "white"
    try:
        if args.image:
            img = cv2.imread(args.image)
            if img is None:
                sys.exit(f"cannot read image: {args.image}")
            return BoardMap.from_image(img, wb), img
        return BoardMap.from_screen(args.monitor, wb), None
    except br.BoardNotFound as e:
        sys.exit(f"Error: {e}")


def cmd_map(args):
    bm, img = _load(args)
    print(f"Board at ({bm.x:.0f}, {bm.y:.0f}), squares {bm.size:.1f}px, "
          f"{'white' if bm.white_bottom else 'black'} at the bottom\n")
    print(bm.table())
    if args.out != "none":
        img, origin = grab(args.monitor) if img is None else (img, (0, 0))
        cv2.imwrite(args.out, bm.draw(img, origin))
        print(f"\nSaved labelled board to {args.out}")


def cmd_watch(args):
    bm, _ = _load(args)
    print("Move the mouse over the board (Ctrl+C to stop).")
    last = object()
    try:
        while True:
            x, y = _gui().position()
            name = bm.square_at(x, y)
            if name != last:
                label = name or "off board"
                print(f"\r{label:<10} ({x}, {y})      ", end="", flush=True)
                last = name
            time.sleep(0.03)
    except KeyboardInterrupt:
        print()


def cmd_hover(args):
    bm, _ = _load(args)
    print(f"{args.square} -> {bm.hover(args.square)}")


def cmd_click(args):
    bm, _ = _load(args)
    print(f"clicked {args.square} at {bm.click(args.square)}")


def cmd_move(args):
    bm, _ = _load(args)
    bm.move(args.frm, args.to, drag=args.drag)
    print(f"{'dragged' if args.drag else 'clicked'} {args.frm} -> {args.to}")


def cmd_shell(args):
    bm, _ = _load(args)
    print("Type a square to hover it (e4), '!' after it to click (e4!), "
          "two squares to play a move (e2e4), 'where', or 'q' to quit.")
    while True:
        try:
            line = input("square> ").strip().lower().replace(" ", "")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        try:
            if line in ("q", "quit", "exit"):
                return
            if line == "where":
                print(bm.under_mouse() or "off board")
            elif len(line) == 4:
                bm.move(line[:2], line[2:])
            elif line.endswith("!"):
                print(bm.click(line[:-1]))
            elif line:
                print(bm.hover(line))
        except KeyError as e:
            print(e.args[0])


def main():
    p = argparse.ArgumentParser(description="Screen coordinates for every square of a chess board")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--side", choices=["white", "black"], help="colour at the bottom (default: auto)")
        sp.add_argument("--monitor", type=int, default=0, help="monitor to search (0 = all, 1 = primary)")
        sp.add_argument("--image", help="use a screenshot file instead of the live screen")
        return sp

    s = common(sub.add_parser("map", help="print the screen coordinates of every square"))
    s.add_argument("--out", default="board_coords.png", help="labelled image to save ('none' to skip)")
    s.set_defaults(func=cmd_map)
    common(sub.add_parser("watch", help="show which square the mouse is over")).set_defaults(func=cmd_watch)
    s = common(sub.add_parser("hover", help="move the mouse onto a square"))
    s.add_argument("square")
    s.set_defaults(func=cmd_hover)
    s = common(sub.add_parser("click", help="move onto a square and click it"))
    s.add_argument("square")
    s.set_defaults(func=cmd_click)
    s = common(sub.add_parser("move", help="click one square then another"))
    s.add_argument("frm")
    s.add_argument("to")
    s.add_argument("--drag", action="store_true", help="drag instead of two clicks")
    s.set_defaults(func=cmd_move)
    common(sub.add_parser("shell", help="type squares and the mouse goes there")).set_defaults(func=cmd_shell)

    args = p.parse_args()
    try:
        args.func(args)
    except KeyError as e:
        sys.exit(e.args[0])


if __name__ == "__main__":
    main()
