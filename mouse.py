"""Human-like mouse movement for pyautogui.

A person moving a mouse does not slide in a straight line at an even speed:

* Time: longer and smaller targets take longer (Fitts's law), with variation from
  move to move.
* Path: a slightly bent arc, with a small hand tremor on top.
* Speed: a bell-shaped profile, quick to get going and slower to brake, peaking
  before the halfway point; the updates themselves come at uneven intervals.
* Aim: the main movement lands near the target, not on it, and sometimes goes past.
  One or two small, slower corrections follow.
* Clicks: a short pause to settle, then the button is held for a human length of time.

  glide(gui, x, y, width)    # move like a hand to (x, y); width = target size in pixels
  press(gui)                 # a human click where the mouse is
  scatter(cx, cy, size)      # a human click point on a square around (cx, cy)
"""
import math
import random
import time

STEP = 0.008   # seconds between mouse updates (a 125 Hz mouse)


def fitts_time(dist, width, speed=1.0, rng=random):
    """Seconds to point at a target `width` pixels wide, `dist` pixels away. speed > 1 is quicker."""
    if dist < 1:
        return 0.0
    t = 0.07 + 0.11 * math.log2(dist / max(width, 1.0) + 1)
    return t * rng.lognormvariate(0, 0.18) / speed


def scatter(cx, cy, size, rng=random):
    """Where a person clicks on a square centred at (cx, cy): near the middle, rarely the
    exact centre, never outside the inner 60% of the square."""
    lim = size * 0.3
    return (round(cx + max(-lim, min(lim, rng.gauss(0, size * 0.12)))),
            round(cy + max(-lim, min(lim, rng.gauss(0, size * 0.12)))))


def _curve(a, b, rng, bend=1.0):
    """A cubic Bezier from a to b whose control points are pushed sideways, mostly to the
    same side, by up to about a fifth of the distance."""
    (x0, y0), (x1, y1) = a, b
    dx, dy = x1 - x0, y1 - y0
    d = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / d, dx / d
    side1 = rng.gauss(0, 0.10) * d * bend
    side2 = side1 * rng.uniform(0.2, 1.1) + rng.gauss(0, 0.03) * d * bend
    f1, f2 = rng.uniform(0.2, 0.4), rng.uniform(0.6, 0.85)
    c1 = (x0 + dx * f1 + nx * side1, y0 + dy * f1 + ny * side1)
    c2 = (x0 + dx * f2 + nx * side2, y0 + dy * f2 + ny * side2)

    def at(t):
        u = 1 - t
        return (u ** 3 * x0 + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * x1,
                u ** 3 * y0 + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * y1)
    return at


def _progress(u, peak):
    """Share of the path covered after share u of the time: a minimum-jerk (bell-shaped
    speed) profile, skewed so the speed peaks at `peak` instead of halfway."""
    s = u ** (math.log(0.5) / math.log(peak))
    return 10 * s ** 3 - 15 * s ** 4 + 6 * s ** 5


def _segment(gui, a, b, duration, rng, bend=1.0, tremor=0.35):
    """One movement from a to b taking about `duration` seconds."""
    path = _curve(a, b, rng, bend)
    peak = rng.uniform(0.35, 0.48)
    n = max(2, round(duration / STEP))
    start = time.perf_counter()
    jx = jy = 0.0
    last = None
    for k in range(1, n + 1):
        u = k / n
        x, y = path(_progress(u, peak))
        if k < n:   # tremor: smoothed noise, gone at the end point
            jx, jy = 0.7 * jx + rng.gauss(0, tremor), 0.7 * jy + rng.gauss(0, tremor)
            x, y = x + jx, y + jy
        p = (round(x), round(y))
        if p != last:
            gui.moveTo(*p, _pause=False)
            last = p
        wait = start + duration * u - time.perf_counter()
        if wait > 0:
            time.sleep(wait * rng.uniform(0.8, 1.2))


def glide(gui, x, y, width=60.0, speed=1.0, rng=random):
    """Move the mouse to (x, y) like a hand. width: size of the target in pixels (a
    square), which sets how carefully it is approached. speed > 1 is quicker."""
    sx, sy = gui.position()
    d = math.hypot(x - sx, y - sy)
    if d < 2:
        if d:
            gui.moveTo(x, y, _pause=False)
        return
    ux, uy = (x - sx) / d, (y - sy) / d
    if rng.random() < min(0.45, d / 1000):
        # past the target, a little off the line
        past, off = rng.uniform(0.03, 0.09) * d, rng.gauss(0, 0.02) * d
        aim = (x + ux * past - uy * off, y + uy * past + ux * off)
    else:
        # short of it or beside it, by a few percent of the distance
        short = rng.uniform(0.0, 0.06) * d
        aim = (x - ux * short + rng.gauss(0, 0.015 * d), y - uy * short + rng.gauss(0, 0.015 * d))
    _segment(gui, (sx, sy), aim, fitts_time(d, width, speed, rng), rng)
    for k in range(2):   # corrections: notice the miss, then a short, careful move
        cx, cy = gui.position()
        rest = math.hypot(x - cx, y - cy)
        if rest < 1.5:
            break
        time.sleep(rng.uniform(0.015, 0.05) / speed)
        last = k == 1 or rest < 6
        goal = (x, y) if last else (x + rng.gauss(0, rest * 0.1), y + rng.gauss(0, rest * 0.1))
        _segment(gui, (cx, cy), goal, (0.04 + 0.035 * math.log2(rest / 4 + 1)) / speed, rng,
                 bend=0.4, tremor=0.15)
    if gui.position() != (x, y):
        gui.moveTo(x, y, _pause=False)


def press(gui, speed=1.0, rng=random):
    """A human click where the mouse is: settle briefly, hold the button 40-200 ms
    (shorter when hurried)."""
    time.sleep(min(0.2, rng.lognormvariate(math.log(0.05), 0.4)) / speed)
    gui.mouseDown(_pause=False)
    time.sleep(min(0.2, max(0.04, rng.lognormvariate(math.log(0.085), 0.3) / math.sqrt(speed))))
    gui.mouseUp(_pause=False)
