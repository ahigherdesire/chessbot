"""Command-line tools in one program (the entry point of chessbot-cli.exe).

  chessbot-cli analyze board.png | screen    # chessbot.py
  chessbot-cli fen "<FEN>"
  chessbot-cli calibrate start.png --fen "<FEN>"
  chessbot-cli play [options]                # auto_play.py
  chessbot-cli board map | watch | ...       # board_coords.py
"""
import sys

USAGE = __doc__.split("\n\n", 1)[1]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd in ("analyze", "fen", "calibrate"):
        import chessbot as tool
        sys.argv = ["chessbot-cli"] + sys.argv[1:]
    elif cmd in ("play", "board"):
        import auto_play
        import board_coords
        tool = auto_play if cmd == "play" else board_coords
        sys.argv = [f"chessbot-cli {cmd}"] + sys.argv[2:]
    else:
        print(USAGE)
        sys.exit(0 if cmd in ("", "-h", "--help") else 2)
    tool.main()


if __name__ == "__main__":
    main()
