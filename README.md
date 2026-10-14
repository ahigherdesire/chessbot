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
with the bot's settings: play as an engine or as a human of some rating, accuracy,
think time, human timing, max think, the rating for the human index, human moves,
side, click or drag, natural mouse, premoves, and the clock. Press **Start** and **Stop**
to run the bot. Settings apply from the next move, even while it plays.
Below the settings it shows:

* an **eval bar** (white/black win chances, your colour at the bottom) with the
  engine's score,
* the **last move** and how long the bot took,
* the **human index** of that move,
* an **eval graph**: the win chances after each of the bot's moves, from the
  start of the game (yellow dots are premoves),
* an **accuracy graph**: each move's accuracy as a bar, with the game average as a
  line. Accuracy uses lichess's formula: 100% for the engine's best move, less
  the more win chance a move gives away. Bars are green (good), yellow
  (inaccuracy), orange (mistake) or red (blunder). Point at either graph to see
  that move. At accuracy 100% every move is the engine's choice, so it shows 100%,
* a log of what the bot is doing.

The window can be resized; the graphs and log stretch to fill it. Untick
**Graphs** for a narrow, one-column window with the log under the game.

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

When [playing as a human](#play-as-a-human), human timing is always on. If
**Human timing** is off, the pace comes from the clock (blitz without one).
Obviousness is then judged the way people see the position: when Maia says at
least 70% of players at that rating play the same move, the bot moves quickly.
When people are split between several moves, it thinks longer. When low on
time, it still varies its moves (0.05-0.4s), rather than moving like a
metronome. On 300 positions from real blitz games, playing as a 1500 human:

| reason | share of moves | think time |
|---|---:|---|
| obvious to a human | 13% | 0.26-0.75s |
| recapture, only move, clearly best | 4% | 0.3-1.1s |
| normal move | 55% | 0.5-5.8s (median 1.7s) |
| hard choice | 21% | 1.3-7s (median 3.5s) |

That is thinking time only. Reading the board and moving the mouse add about
another 0.5-1.5s.

### Natural mouse

By default the bot moves the mouse like a hand (`mouse.py`), not in instant jumps:

* **Time** follows Fitts's law: longer and smaller targets take longer. It varies
  from move to move.
* **Path**: a slightly bent arc, never a straight line, with a small hand tremor.
* **Speed**: quick to start, slower to stop, fastest before the halfway point.
  The mouse updates come at uneven intervals.
* **Aim**: the main movement lands near the square, not on it, and sometimes
  goes past it. One or two small corrections follow.
* **Clicks** land around the middle of the square, rarely the exact centre. The
  button is held 40-200 ms after a short pause to settle. Drags are a little
  slower while the button is held.

It moves quicker in faster games (from the clock or Human timing) and 1.5x
quicker again when low on time. Mouse work per move, from beside the board to
both clicks: about 1.2s in rapid, 1.0s in blitz, 0.75s in bullet and 0.6s in
bullet time trouble.
`--mouse instant` (or untick **Natural mouse**) goes back to fast, straight moves.
The mouse still aborts the bot when slammed into a screen corner.

### Premoves

`--premove` makes the bot queue its next move while the opponent is still
thinking. The site then plays it the moment they move. Right after each of its
own moves, the bot decides whether a premove is worth it. It estimates how likely
the opponent's most likely reply is:

* **Only legal move**: certain.
* **Book opening**: the share of players who answer that way in a well-known
  position (1.e4 e5 2.Nf3 is answered by Nc6 80% of the time).
* **The opponent is an engine** (`--opponent-engine`): how far the engine's best
  move stands out. When several moves are almost as good, the engine may pick
  any of them, so the likelihood is low.
* **Otherwise**: the [human index](#human-index), using the **Human index**
  rating. It favours clearly best moves and natural ones such as recaptures.

The bot premoves when the reply is at least **70% likely**. When low on time
(`--clock`, below `--low-time` seconds, default 20), it premoves at **40%**,
because a wrong guess then costs less than thinking, and it thinks at most 0.3s
a move. It never premoves more than `--premove-streak` moves in a row (default
2) before thinking a move over again.

Some examples: after 1.e4, the bot gives ...e5 about 45%, so no premove. A
forced recapture comes out at 90-100%, so it premoves. A quiet middlegame move
usually comes out at 45-60%, so it only premoves that when low on time. In
simulated games, 5-20% of the bot's moves were premoves, and about half when
low on time.

```
python auto_play.py --premove                       # premove when the reply is clear
python auto_play.py --premove --opponent-engine     # against another engine
python auto_play.py --premove --clock 3+2           # also premove more in a time scramble
```

The site plays a premove after **any** reply where it is legal, not only the one
the bot predicted. So before each premove, the bot checks it against the
opponent's 8 best replies (4 when low on time). It skips the premove if it would
leave us more than 0.8 pawns worse off (2.5 when low on time) than the expected
line after any of those replies. The log shows each premove
(`premove Nxd4 if cxd4 (book, 98% of players)`) and whether it landed as
predicted, after another reply, or was dropped because their reply made it
illegal. When it decides not to premove, the log says why
(`no premove: d6 is only 46% likely (most natural reply), need 70%`).

The clock on screen is not read. `--clock` gives the time control, and the bot
counts down from how long it takes per move. Start the bot before the game for
an accurate count. Premoves are planned in about 0.3-1s, so against an engine
that replies instantly they often come too late. In that case the bot sees the
reply and just plays normally. Premoves that need the promotion picker are
skipped, unless `--auto-queen` is set.

In the control panel, the **Premoves** section has the mode (*off*, *on* or *vs
engine*), **Max in a row**, the **Clock** and the low-time limit. The game
panel shows the premove that is queued or just played, or why the bot decided
against one. It also shows how many
premoves landed or were dropped this game, and the bot's estimate of your clock.
The estimate turns orange when you are low on time.

#### Premoves are not always successful

A queued premove is a guess, and it can fail:

* **Their reply makes it illegal.** The site drops it, and the bot plays
  normally. It counts as *dropped*.
* **The site ignores it.** If premoves are turned off in the site's settings,
  or the site cancels the premove (for example after a misclick or lag), it never
  plays. The bot waits up to 1.5 seconds after their reply appears, then thinks
  normally. That wait costs clock time.
* **They reply first.** Against a fast opponent, the reply can land while the
  bot is still planning or clicking. A click meant as a premove then becomes a
  normal move, made for a reply that did not happen. The bot checks the screen
  just before clicking, so this is rare, but it cannot be ruled out.
* **It lands after an unexpected reply.** The site plays a premove after any
  reply where it is legal. The bot only checks the premove against the
  opponent's 8 best replies (4 when low on time). After any other reply, the
  premove can be a poor move.
* **The clock is an estimate.** The low-time limit uses the bot's own count,
  not the clock on screen. It can be off, so premoves may start a little early
  or late.

Use premoves when speed matters more than the quality of a few moves, such as
bullet, a time scramble, or a known opening.

## Human index

`--human-index [RATING]` prints how likely a human of that rating (default
1500) is to play the move. It works with `chessbot.py analyze`/`fen` and with
`auto_play.py`. On its own it only reports. To make the bot play only human
moves, see [Human mode](#human-mode).

```
python chessbot.py fen "<FEN>" --human-index 1200
Human:  36% chance a 1200-rated player picks Re1 (natural)
        most human: Re1 36%, Rc1 15%, a3 9%
```

Labels: **very human** (over 50%), **natural** (10-50%), **unusual** (3-10%), and
below 3% either **engine-like** (a strong move few people find) or **rare
mistake** (a bad move few people make).

It is an estimate, not real data. Moves are judged the way a player of that
rating sees them: with a search only as deep as they calculate (2 plies at 1000,
4 at 1500, 6 at 2000, 8 at 2500). So a move that is only good because of a deep
line looks bad and gets a low chance, the way people miss it. A move's chance
falls with how much worse it looks than the best-looking move (stronger players
give away less), and with its rank. It rises for moves humans find natural
(captures, recaptures, checks, castling, saving an attacked piece), and falls
for moves they overlook (retreats, long quiet moves, early king walks). 1.e4
gets about 40% at the start. A model trained on real games, such as Maia, would
be more accurate.

### Human mode

`--human-moves X` makes the bot play only moves a human of the `--human-index`
rating (default 1500) would pick at least X% of the time. Among those moves, it
plays the engine's choice (`--accuracy` still applies). It never plays a move
only an engine finds. If no move reaches X%, it plays the most human move.

```
python auto_play.py --human-moves 20 --human-index 1500
```

In the control panel, set **Human moves** to X (0 turns it off). It uses the
**Human index** rating. In simulated games at 1500, the move differed from the
engine's choice this often, usually costing 0.2-0.5 pawns:

| Human moves | moves changed |
|---:|---:|
| 10% | about 1 in 8 |
| 20% | about 1 in 3 |
| 35% | about 1 in 2 |

The best move usually gets 30-60%, so values above 50% mean "always play the
single most human move". Premoves follow human mode too. The log shows what it
did (`human mode: 2 moves 20%+ human, skipped the engine's e5`).

### Play as a human

`--play-like RATING` makes the bot play like a human of that rating, mistakes
and all, instead of like an engine. It uses [Maia](https://github.com/CSSLab/maia-chess):
neural networks from the University of Toronto, trained on about 12 million
lichess games per rating band to predict the move a human of that rating plays.
For each move the bot draws one at random with Maia's chances. So it plays the
natural-looking moves people play, misses what they miss, and blunders about as
often as they do. That includes falling for the traps real players fall for: after
1.e4 e5 2.Nf3 Nc6 3.Bc4 Nd4, Maia-1500 grabs the e5 pawn 40% of the time.

Maia needs the lc0 engine and its networks (about 35 MB). Download them once:

```
python get_maia.py
python auto_play.py --play-like 1200
```

Maia has networks for 1100 to 1900; the bot stretches them:

* **Between networks** (1550): the two nearest networks' chances are blended.
* **Below 1100**: Maia-1100's chances are flattened, so more mistakes slip in.
* **Above 1900**: Stockfish's scores filter Maia-1900's choices. A move loses
  weight with how much worse it is than the best move, more strictly the higher
  the rating. It still plays human moves, but sees through more mistakes.
* **Bullet** (from the clock, or Human timing set to bullet) and **time
  trouble** flatten the chances too, because people blunder more when rushed.

In the control panel, set **Play as** to *human* and pick or type a rating
(400-3000). *engine* plays Stockfish's moves, with **Accuracy** and **Human
moves**; those two are greyed out while playing as a human. The engine still
searches for **Think time** to score the moves, for the eval bar and the graphs.
The log shows each pick
(`as a 1200 human (blitz, maia): 18% of them play this, best was Nxe5 (1.4 pawns better)`).

Without lc0 and Maia, a rougher built-in model is used. It thinks only as deep as
the rating allows, and sometimes lapses: it just glances at its own move, misses
the reply and hangs a piece.

How human is it? I graded 424 real lichess arena games (bullet, blitz and rapid)
move by move with Stockfish. Then I graded self-play games of the bot at the
matching rating the same way. Rates are per move. A blunder gives away 15%+ win
chance, as on lichess:

| | accuracy | avg loss (cp) | mistakes | blunders |
|---|---:|---:|---:|---:|
| real 800-1199, rapid | 84.9% | 87 | 4.2% | 10.2% |
| bot as 1100, rapid | 85.0% | 99 | 3.8% | 11.1% |
| real 1200-1599, rapid | 87.0% | 68 | 3.6% | 7.8% |
| bot as 1400, rapid | 87.7% | 73 | 4.0% | 8.7% |
| real 1200-1599, blitz | 85.3% | 81 | 4.5% | 9.0% |
| bot as 1400, blitz | 86.1% | 84 | 4.1% | 9.3% |
| real 1600-1999, bullet | 86.2% | 74 | 4.1% | 7.9% |
| bot as 1800, bullet | 87.7% | 70 | 3.8% | 7.5% |
| real 2000-2399, blitz | 89.0% | 54 | 3.7% | 5.4% |
| bot as 2200, blitz | 89.9% | 46 | 3.6% | 4.8% |
| real 2400+, bullet | 93.7% | 32 | 1.5% | 2.4% |
| bot as 2500, bullet | 91.9% | 31 | 2.2% | 1.9% |

The old engine-based model at 1500 had 94% accuracy and 0.2 blunders a game
(in a small check), nothing like a real 1500. The real-game sample is small at the
extremes (11 players over 2400). Ratings are lichess ratings.

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
