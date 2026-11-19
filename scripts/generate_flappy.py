#!/usr/bin/env python3
"""Generate an animated Flappy-Bird-style SVG on top of a GitHub contribution graph.

The output is one self-contained SVG (SMIL animation, no JavaScript) that works
inside a normal README <img> tag on github.com.

Data sources (``--source``):
  graphql  GitHub GraphQL API, needs GITHUB_TOKEN (used by the Actions workflow)
  html     public contributions page, no token needed
  demo     deterministic synthetic grid (offline preview / testing only)
  empty    blank grid, used as the initial placeholder before real data exists
  auto     graphql if GITHUB_TOKEN is set, otherwise html

Only the Python standard library is used.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import sys
import urllib.request

# ----------------------------------------------------------------- geometry --
CELL, PITCH = 11, 14            # contribution cell size / pitch (cell + gap)
WEEKS, DAYS = 53, 7
PAD_X = 24
GRID_W = WEEKS * PITCH - (PITCH - CELL)
GRID_H = DAYS * PITCH - (PITCH - CELL)
W = PAD_X * 2 + GRID_W          # 787
H = 200
FIELD_TOP = 34                  # play field starts below the HUD
GROUND_Y = 186
GRID_Y = FIELD_TOP + ((GROUND_Y - FIELD_TOP) - GRID_H) // 2

# ------------------------------------------------------------------ palette --
BG_TOP, BG_BOTTOM = "#0d1117", "#150d26"
LEVEL_COLORS = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]
PIPE_BODY, PIPE_LIGHT, PIPE_DARK, PIPE_EDGE = "#2ea043", "#56d364", "#196c2e", "#0a2a12"
PINK, PURPLE = "#f778ba", "#a371f7"
GROUND_FILL, GROUND_LINE = "#1a1030", "#3b2a66"

# -------------------------------------------------------------- bird sprite --
# 14 x 8 pixel art, scaled by SCALE. O outline, P body, L belly, W/K eye, B beak.
SPRITE = [
    "....OOOOOO....",
    "...OPPPPPWWO..",
    "..OPPPPPWWKWO.",
    ".OPPPPPPPWWWOO",
    ".OPPPPPPPPOBBO",
    "..OLLLPPPPOOO.",
    "...OLLLLLLO...",
    "....OOOOOO....",
]
SPRITE_COLORS = {
    "O": "#2a1245", "P": PINK, "L": "#fbcfe8",
    "W": "#ffffff", "K": "#2a1245", "B": "#ffa657",
}
WING_COLOR = PURPLE
# wing overlays: (col, row, width) runs for up / middle / down positions
WINGS = {
    "up":   [(2, 2, 4), (3, 3, 3)],
    "mid":  [(2, 3, 4), (3, 4, 3)],
    "down": [(2, 4, 4), (3, 5, 3)],
}
SCALE = 3
BIRD_W, BIRD_H = len(SPRITE[0]) * SCALE, len(SPRITE) * SCALE   # 42 x 24

# ------------------------------------------------------------ game tuning ----
LOOP_SECONDS = 14.0
FPS = 24
PIPE_W, PIPE_CAP_W, PIPE_CAP_H, GAP = 38, 46, 12, 72
N_PIPES = 6
HIT_W, HIT_H = 34, 20           # forgiving hit box used for the self-check


# ================================================================ data input ==
LEVELS = {"NONE": 0, "FIRST_QUARTER": 1, "SECOND_QUARTER": 2,
          "THIRD_QUARTER": 3, "FOURTH_QUARTER": 4}


def _normalise(grid):
    """Keep the last WEEKS columns, left-pad with empty (None) columns."""
    grid = grid[-WEEKS:]
    while len(grid) < WEEKS:
        grid.insert(0, [None] * DAYS)
    return grid


def fetch_graphql(user: str, token: str):
    query = ("query($u:String!){user(login:$u){contributionsCollection{"
             "contributionCalendar{weeks{contributionDays{contributionLevel weekday}}}}}}")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": {"u": user}}).encode(),
        headers={"Authorization": f"bearer {token}",
                 "Content-Type": "application/json",
                 "User-Agent": "flappy-contrib-generator"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if data.get("errors") or not data.get("data", {}).get("user"):
        raise RuntimeError(f"GraphQL error: {data.get('errors') or 'user not found'}")
    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    grid = []
    for week in weeks:
        col = [None] * DAYS
        for day in week["contributionDays"]:
            col[day["weekday"]] = LEVELS.get(day["contributionLevel"], 0)
        grid.append(col)
    return _normalise(grid)


def fetch_html(user: str):
    req = urllib.request.Request(
        f"https://github.com/users/{user}/contributions",
        headers={"User-Agent": "flappy-contrib-generator"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", "replace")
    days = {}
    for tag in re.findall(r"<td\b[^>]*>", html):
        d = re.search(r'data-date="(\d{4}-\d{2}-\d{2})"', tag)
        lv = re.search(r'data-level="(\d)"', tag)
        if d and lv:
            days[dt.date.fromisoformat(d.group(1))] = int(lv.group(1))
    if not days:
        raise RuntimeError("no contribution cells found in the HTML response")
    first = min(days)
    start = first - dt.timedelta(days=(first.weekday() + 1) % 7)   # back to Sunday
    n_weeks = (max(days) - start).days // 7 + 1
    grid = [[None] * DAYS for _ in range(n_weeks)]
    for date, level in days.items():
        grid[(date - start).days // 7][(date.weekday() + 1) % 7] = level
    return _normalise(grid)


def demo_grid(seed: int = 2711):
    rng = random.Random(seed)
    grid = []
    for w in range(WEEKS):
        busy = 0.35 + 0.45 * abs(((w * 7) % 29) / 29 - 0.5) * 2
        col = []
        for d in range(DAYS):
            if d in (0, 6):
                p = busy * 0.45
            else:
                p = busy
            col.append(rng.choice([1, 1, 2, 3, 4]) if rng.random() < p else 0)
        grid.append(col)
    return grid


def load_grid(source: str, user: str):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if source == "demo":
        return demo_grid(), "demo"
    if source == "empty":      # honest placeholder: no activity shown until real data arrives
        return [[0] * DAYS for _ in range(WEEKS)], "empty"
    if source == "graphql" or (source == "auto" and token):
        if not token:
            sys.exit("error: --source graphql needs GITHUB_TOKEN")
        return fetch_graphql(user, token), "graphql"
    return fetch_html(user), "html"


# ================================================================ simulation ==
def plan_pipes(rng: random.Random):
    first_x, last_x = 150, W - 90
    xs = [first_x + i * (last_x - first_x) / (N_PIPES - 1) for i in range(N_PIPES)]
    edge = PIPE_CAP_H + 10      # keep room for a visible pipe body above/below the gap
    lo = FIELD_TOP + GAP / 2 + edge
    hi = GROUND_Y - GAP / 2 - edge
    centers = [rng.uniform(lo, hi)]
    while len(centers) < len(xs):
        c = rng.uniform(lo, hi)
        # alternate high/low a little so the flight looks lively, but stay reachable
        if 10 <= abs(c - centers[-1]) <= 34:
            centers.append(c)
    return xs, centers


def simulate(xs, centers):
    """Autopilot: Euler-integrated gravity + flap impulses, sampled at FPS."""
    g, flap_v, dt_ = 1100.0, -200.0, 1 / 240
    x0, x1 = 30 + BIRD_W / 2, W - 30 - BIRD_W / 2
    speed = (x1 - x0) / LOOP_SECONDS
    y, vy, t = (FIELD_TOP + GROUND_Y) / 2, 0.0, 0.0
    samples = []

    def target(cx):
        for px, c in zip(xs, centers):
            if cx < px + PIPE_CAP_W / 2 + BIRD_W / 2 - 6:
                return c
        return centers[-1]

    for f in range(int(LOOP_SECONDS * FPS) + 1):
        until = f / FPS
        while t < until - 1e-9:
            cx = x0 + speed * t
            if y > target(cx) + 10 and vy >= 0:
                vy = flap_v
            vy += g * dt_
            y += vy * dt_
            y = max(FIELD_TOP + 14, min(GROUND_Y - 14, y))
            t += dt_
        angle = max(-24.0, min(55.0, vy * 0.08))
        samples.append((x0 + speed * until, y, angle))
    return samples, x0, x1


def check_collisions(samples, xs, centers):
    worst = 1e9
    for cx, cy, _ in samples:
        for px, c in zip(xs, centers):
            if abs(cx - px) < PIPE_CAP_W / 2 + HIT_W / 2:
                top, bottom = c - GAP / 2, c + GAP / 2
                worst = min(worst, cy - HIT_H / 2 - top, bottom - (cy + HIT_H / 2))
    return worst


# ================================================================== drawing ==
def sprite_rects(rows, colors, scale):
    out = []
    for r, row in enumerate(rows):
        c = 0
        while c < len(row):
            ch = row[c]
            if ch == ".":
                c += 1
                continue
            start = c
            while c < len(row) and row[c] == ch:
                c += 1
            out.append(f'<rect x="{start * scale}" y="{r * scale}" '
                       f'width="{(c - start) * scale}" height="{scale}" fill="{colors[ch]}"/>')
    return "".join(out)


def wing_group(name, runs, values, key_times):
    rects = "".join(f'<rect x="{c * SCALE}" y="{r * SCALE}" width="{w * SCALE}" '
                    f'height="{SCALE}" fill="{WING_COLOR}"/>' for c, r, w in runs)
    return (f'<g opacity="0">{rects}<animate attributeName="opacity" calcMode="discrete" '
            f'values="{values}" keyTimes="{key_times}" dur="0.4s" repeatCount="indefinite"/></g>')


def pipe_svg(px, center):
    top_end, bot_start = center - GAP / 2, center + GAP / 2
    body_x, cap_x = px - PIPE_W / 2, px - PIPE_CAP_W / 2

    def column(x, y, w, h):
        return (f'<rect x="{x - 2}" y="{y - 2}" width="{w + 4}" height="{h + 4}" fill="{PIPE_EDGE}"/>'
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{PIPE_BODY}"/>'
                f'<rect x="{x + 4}" y="{y}" width="6" height="{h}" fill="{PIPE_LIGHT}"/>'
                f'<rect x="{x + w - 9}" y="{y}" width="9" height="{h}" fill="{PIPE_DARK}"/>')

    top_h = top_end - FIELD_TOP
    bot_h = GROUND_Y - bot_start
    parts = [
        column(body_x, FIELD_TOP, PIPE_W, top_h - PIPE_CAP_H),
        column(cap_x, top_end - PIPE_CAP_H, PIPE_CAP_W, PIPE_CAP_H),
        column(cap_x, bot_start, PIPE_CAP_W, PIPE_CAP_H),
        column(body_x, bot_start + PIPE_CAP_H, PIPE_W, bot_h - PIPE_CAP_H),
    ]
    return "".join(parts)


def score_text(j, n, key_fracs):
    a = 0.0 if j == 0 else key_fracs[j - 1]
    b = 1.0 if j == n else key_fracs[j]
    pts = []
    if a > 0:
        pts.append((0.0, 0))
    pts.append((a, 1))
    if b < 1:
        pts.append((b, 0))
    pts.append((1.0, 1 if b >= 1 else 0))
    vals = ";".join(str(v) for _, v in pts)
    kts = ";".join(f"{k:.4f}" for k, _ in pts)
    return (f'<text x="{W - PAD_X}" y="22" text-anchor="end" fill="{PINK}" opacity="0">'
            f'SCORE {j:02d}<animate attributeName="opacity" calcMode="discrete" '
            f'values="{vals}" keyTimes="{kts}" dur="{LOOP_SECONDS}s" repeatCount="indefinite"/></text>')


def build_svg(grid, user):
    rng = random.Random(f"flappy:{user}")
    xs, centers = plan_pipes(rng)
    samples, x0, x1 = simulate(xs, centers)
    margin = check_collisions(samples, xs, centers)
    if margin < 2:
        raise RuntimeError(f"bird collides with a pipe (margin {margin:.1f}px) - retune the controller")

    cells = []
    for w, col in enumerate(grid):
        for d, level in enumerate(col):
            if level is None:
                continue
            cells.append(f'<rect x="{PAD_X + w * PITCH}" y="{GRID_Y + d * PITCH}" width="{CELL}" '
                         f'height="{CELL}" rx="2" fill="{LEVEL_COLORS[level]}"/>')

    pipes = "".join(pipe_svg(px, c) for px, c in zip(xs, centers))

    ground = [f'<rect y="{GROUND_Y}" width="{W}" height="{H - GROUND_Y}" fill="{GROUND_FILL}"/>',
              f'<rect y="{GROUND_Y}" width="{W}" height="3" fill="{GROUND_LINE}"/>']
    for gx in range(0, W, 24):
        ground.append(f'<rect x="{gx}" y="{GROUND_Y + 7}" width="12" height="3" fill="{GROUND_LINE}" opacity=".6"/>')

    key_fracs = [(px + PIPE_CAP_W / 2 - x0) / (x1 - x0) for px in xs]
    scores = "".join(score_text(j, N_PIPES, key_fracs) for j in range(N_PIPES + 1))

    translate = ";".join(f"{cx:.1f} {cy:.1f}" for cx, cy, _ in samples)
    rotate = ";".join(f"{a:.1f} 0 0" for _, _, a in samples)
    body = sprite_rects(SPRITE, SPRITE_COLORS, SCALE)
    wings = (wing_group("up", WINGS["up"], "1;0;0;0", "0;0.25;0.5;0.75")
             + wing_group("mid", WINGS["mid"], "0;1;0;1", "0;0.25;0.5;0.75")
             + wing_group("down", WINGS["down"], "0;0;1;0", "0;0.25;0.5;0.75"))
    bird = (f'<g><animateTransform attributeName="transform" type="translate" calcMode="linear" '
            f'values="{translate}" dur="{LOOP_SECONDS}s" repeatCount="indefinite"/>'
            f'<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.02;0.97;1" '
            f'dur="{LOOP_SECONDS}s" repeatCount="indefinite"/>'
            f'<g><animateTransform attributeName="transform" type="rotate" calcMode="linear" '
            f'values="{rotate}" dur="{LOOP_SECONDS}s" repeatCount="indefinite"/>'
            f'<g transform="translate({-BIRD_W / 2} {-BIRD_H / 2})" shape-rendering="crispEdges">'
            f'{body}{wings}</g></g></g>')

    label = f"Flappy Bird style animation flying over the GitHub contribution graph of {user}"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="{label}">
<title>{label}</title>
<desc>A pixel-art bird flies from left to right through green pipes drawn over a contribution grid, then the scene loops.</desc>
<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BG_TOP}"/><stop offset="1" stop-color="{BG_BOTTOM}"/></linearGradient></defs>
<rect width="{W}" height="{H}" rx="14" fill="url(#bg)"/>
<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="14" fill="none" stroke="#30363d"/>
<g shape-rendering="crispEdges">{"".join(cells)}</g>
<g shape-rendering="crispEdges">{pipes}</g>
<g shape-rendering="crispEdges">{"".join(ground)}</g>
<g font-family="'Courier New',Courier,monospace" font-size="12" font-weight="700" letter-spacing="2">
<text x="{PAD_X}" y="22" fill="{PURPLE}">FLAPPY COMMITS</text>
{scores}
</g>
{bird}
</svg>
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", default=os.environ.get("GH_USER", "nithesh2711"))
    ap.add_argument("--source", choices=["auto", "graphql", "html", "demo", "empty"], default="auto")
    ap.add_argument("--out", default="assets/flappy-contributions.svg")
    args = ap.parse_args()

    grid, used = load_grid(args.source, args.user)
    svg = build_svg(grid, args.user)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"wrote {args.out} ({len(svg) / 1024:.1f} KiB) using {used} data")


if __name__ == "__main__":
    main()
