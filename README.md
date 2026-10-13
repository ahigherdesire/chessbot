# Screenshot chess bot

Reads a chess board from a screenshot (or straight off your screen), converts it
to a position, and finds the best move.

## Usage

```
python chessbot.py analyze examples/sample_board.png   # a screenshot file
python chessbot.py analyze screen --monitor 1          # capture the screen now
python chessbot.py fen "<FEN>"                         # no image, just think
```

Options: `--time 5` (seconds to think), `--turn w|b` (force side to move),
`--side white|black` (colour at the bottom), `--engine path\to\stockfish.exe`.

The bot prints the board, the FEN, a lichess analysis link, the engine's thinking,
and the best move. It also saves a copy of the image with an arrow drawn on it.

## How it reads the board

* **Board location**: it finds the two square colours that form an 8x8 checker
  pattern, then fits the grid to the pixel. It works when the board sits inside a
  full-screen screenshot or is cropped a few pixels at the edges.
* **Pieces**: it separates each piece from its square, decides the colour from
  brightness, and matches the shape against templates in `templates/default`.
* **Orientation**: worked out from where the pawns and kings are.
* **Side to move**: worked out from the yellow last-move highlight. With no
  highlight, it assumes the side at the bottom is to move.
* **Castling rights**: inferred from king and rook positions. **En passant**:
  inferred from the highlight.

## Square coordinates and clicking

`board_coords.py` finds the board on screen and gives each of the 64 squares
(a1..h8) a box and a centre point in screen pixels.

```
python board_coords.py map             # print every square's centre, save a labelled board_coords.png
python board_coords.py watch           # live: shows which square the mouse is over
python board_coords.py hover e4        # move the mouse onto e4
python board_coords.py click e4        # move onto e4 and click it
python board_coords.py move e2 e4      # click e2 then e4 (add --drag to drag instead)
python board_coords.py shell           # type e4 to hover, e4! to click, e2e4 to play a move
```

From code: `bm = BoardMap.from_screen()`, then `bm.center("e4")`,
`bm.square_at(x, y)`, `bm.hover("e4")`, `bm.click("e4")` and `bm.move("e2", "e4")`.
Orientation is detected from the pieces. You can force it with `--side black`.
To abort a mouse action, move the mouse into a screen corner.

## Auto play

`auto_play.py` watches the board and, when it is your turn, thinks and clicks the
move. Every click goes to the square centres from `board_coords.py`, with the
monitor offset added.

```
python auto_play.py                         # play the side at the bottom of the board
python auto_play.py --time 1 --accuracy 85  # think 1s a move, play a bit worse on purpose
python auto_play.py --dry-run               # hover the squares, never click
python auto_play.py --once --turn-now       # play one move right now and exit
```

Use `--drag` to drag pieces instead of clicking them. Use `--auto-queen` if the
site promotes to a queen automatically. Otherwise the bot clicks the right piece
in the promotion picker. It finds your turn from the last-move highlight, or
from the board changing after its own move. If a move does not register, it
clicks again after 3 seconds. To stop, move the mouse into a screen corner or
press Ctrl+C.

### Control panel

```
python bot_ui.py
```

Or double-click `Chess Bot.bat`. A small window opens in the top-right corner
with the bot's settings: accuracy, think time, human timing, max think, the
rating for the human index, side, and click or drag. Press **Start** and **Stop**
to run the bot. Settings apply from the next move, even while it plays.
Below the settings it shows:

* an **eval bar** (white/black win chances, your colour at the bottom) with the
  engine's score,
* the **last move** and how long the bot took,
* the **human index** of that move,
* a log of what the bot is doing.

Keep the window off the chess board, since the bot reads the board from the screen.

### Human-like timing

`--human` makes the bot take a believable amount of time. It plays easy moves
fast and thinks longer in hard positions:

```
python auto_play.py --human            # rapid pace (the default)
python auto_play.py --human blitz      # bullet, blitz, rapid or classical
```

| situation | rapid | blitz |
|---|---|---|
| only one legal move | ~0.5s | ~0.5s |
| opening, recapture, free piece | 1-2s | 0.5-1s |
| winning easily | ~2s | ~1s |
| several near-equal moves, tactics | 5-13s | 2-7s |

Difficulty is judged from a quick Stockfish look at the top 3 moves (how close
they are), how many captures and checks are on the board, and whether the
opponent's move changed the evaluation a lot. `--max-think` caps the longest
wait (default 30s). After a long think, the bot checks that the board is
unchanged before moving.

## Human index

`--human-index [RATING]` prints how likely a human of that rating (default
1500) is to play the move. It works with `chessbot.py analyze`/`fen` and with
`auto_play.py`. It only reports: it never changes which move is played.

```
python chessbot.py fen "<FEN>" --human-index 1200
Human:  36% chance a 1200-rated player picks Re1 (natural)
        most human: Re1 36%, Rc1 15%, a3 9%
```

Labels: **very human** (over 50%), **natural** (10-50%), **unusual** (3-10%), and
below 3% either **engine-like** (a strong move few people find) or **rare
mistake** (a bad move few people make).

It is an estimate, not real data. A move's chance falls with how much worse it
is than the best move (stronger players give away less), and with its rank among
the engine's choices. It rises for moves humans find natural (captures,
recaptures, checks, castling, saving an attacked piece), and falls for moves
they overlook (retreats, long quiet moves, early king walks). It is roughly
tuned so 1.e4 gets about 40% at the start and engine-best moves land around
50-60%. A model trained on real games, such as Maia, would be more accurate.

## Other board or piece themes

The bundled templates are for chess.com's default pieces. For another site or
theme, screenshot the starting position and run:

```
python chessbot.py calibrate start.png               # white at the bottom
python chessbot.py calibrate start.png --side black  # black at the bottom
```

## Strength

* With **Stockfish** installed, it plays at a superhuman level (3500+ Elo). It
  finds Stockfish on PATH, through `STOCKFISH_PATH`, in a `engines/` folder here,
  or through `--engine`.
* Without Stockfish, a built-in alpha-beta engine is used. It searches about
  7-8 ply in a few seconds, which is roughly club level.

## Accuracy (playing worse on purpose)

`--accuracy 0-100` sets how accurately the bot plays. The default, 100, always
plays the best move. Below 100, every legal move is scored and one is picked at
random. A move's chance drops the more it loses compared with the best move, and
lower accuracy accepts bigger losses:

| accuracy | typical behaviour |
|---------:|-------------------|
| 100 | always the best move |
| 90  | best move most of the time, otherwise a near-equal move |
| 75  | regular small inaccuracies (~0.3 pawn) |
| 50  | frequent inaccuracies, occasional real mistakes |
| 25  | many mistakes and some blunders |
| 0   | close to random |

```
python chessbot.py analyze screen --accuracy 80
```

Scoring every move takes more work, so in the same time the search is a little
shallower than at accuracy 100.
