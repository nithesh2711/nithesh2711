#!/usr/bin/env python3
"""Generate a self-hosted GitHub stats card (assets/github-stats.svg).

Replaces third-party image widgets (which can be rate-limited or go offline) with
one static SVG built from your own data via the GitHub GraphQL API.

  GITHUB_TOKEN=... python3 scripts/generate_stats.py --user nithesh2711
  python3 scripts/generate_stats.py --source empty      # placeholder, no data

Only the Python standard library is used.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import sys
import urllib.request

W, H = 560, 244
PURPLE, PINK, GREEN = "#a371f7", "#f778ba", "#39d353"
LANG_COLORS = ["#39d353", "#a371f7", "#f778ba", "#26a641", "#7c3aed", "#6e7681"]

QUERY = """query($u:String!){user(login:$u){
  pullRequests{totalCount} issues{totalCount}
  repositories(ownerAffiliations:OWNER,isFork:false,privacy:PUBLIC,first:100){
    totalCount nodes{stargazerCount languages(first:8,orderBy:{field:SIZE,direction:DESC}){edges{size node{name}}}}}
  contributionsCollection{totalCommitContributions contributionCalendar{totalContributions
    weeks{contributionDays{contributionCount date}}}}}}"""


def fetch(user: str, token: str) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"u": user}}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "stats-card-generator"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if data.get("errors") or not data.get("data", {}).get("user"):
        raise RuntimeError(f"GraphQL error: {data.get('errors') or 'user not found'}")
    u = data["data"]["user"]
    cc = u["contributionsCollection"]
    days = sorted((d["date"], d["contributionCount"])
                  for w in cc["contributionCalendar"]["weeks"] for d in w["contributionDays"])
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    days = [d for d in days if d[0] <= today]

    longest = run = 0
    for _, n in days:
        run = run + 1 if n > 0 else 0
        longest = max(longest, run)
    current = 0
    for i, (_, n) in enumerate(reversed(days)):
        if n > 0:
            current += 1
        elif i == 0:          # today may simply not have any contribution yet
            continue
        else:
            break

    sizes: dict[str, int] = {}
    for repo in u["repositories"]["nodes"]:
        for edge in repo["languages"]["edges"]:
            sizes[edge["node"]["name"]] = sizes.get(edge["node"]["name"], 0) + edge["size"]
    total = sum(sizes.values()) or 1
    top = sorted(sizes.items(), key=lambda kv: -kv[1])
    langs = [(n, 100 * s / total) for n, s in top[:4]]
    rest = 100 - sum(p for _, p in langs)
    if rest >= 0.5:
        langs.append(("Other", rest))

    return {
        "commits": cc["totalCommitContributions"],
        "prs": u["pullRequests"]["totalCount"],
        "issues": u["issues"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in u["repositories"]["nodes"]),
        "repos": u["repositories"]["totalCount"],
        "total": cc["contributionCalendar"]["totalContributions"],
        "current": current, "longest": longest, "langs": langs,
    }


def fmt(v):
    return "–" if v is None else f"{v:,}"


def build(user: str, d: dict | None) -> str:
    g = (lambda k: d[k]) if d else (lambda k: None)
    rows = [("Commits (past year)", g("commits")), ("Pull requests", g("prs")),
            ("Issues", g("issues")), ("Stars earned", g("stars")), ("Public repos", g("repos"))]
    out = []
    for i, (label, val) in enumerate(rows):
        y = 70 + i * 24
        out.append(f'<text x="24" y="{y}" fill="#8b949e" font-size="13">{label}</text>'
                   f'<text x="256" y="{y}" fill="{GREEN}" font-size="15" font-weight="700" text-anchor="end">{fmt(val)}</text>')

    streaks = [("TOTAL (PAST YEAR)", g("total"), GREEN), ("CURRENT STREAK", g("current"), PINK),
               ("LONGEST (PAST YEAR)", g("longest"), PURPLE)]
    for i, (label, val, color) in enumerate(streaks):
        y = 66 + i * 42
        out.append(f'<text x="320" y="{y}" fill="{color}" font-size="24" font-weight="700">{fmt(val)}</text>'
                   f'<text x="320" y="{y + 16}" fill="#8b949e" font-size="10" letter-spacing="1">{label}</text>')

    out.append('<line x1="288" y1="48" x2="288" y2="168" stroke="#30363d"/>')
    out.append(f'<text x="24" y="190" fill="{PURPLE}" font-size="11" font-weight="700" letter-spacing="2">TOP LANGUAGES</text>')
    langs = d["langs"] if d else []
    if langs:
        x = 24.0
        out.append('<clipPath id="bar"><rect x="24" y="198" width="512" height="8" rx="4"/></clipPath><g clip-path="url(#bar)">')
        for i, (_, pct) in enumerate(langs):
            w = 512 * pct / 100
            out.append(f'<rect x="{x:.1f}" y="198" width="{w:.1f}" height="8" fill="{LANG_COLORS[min(i, 5)]}"/>')
            x += w
        out.append("</g>")
        lx = 24
        for i, (name, pct) in enumerate(langs):
            short = name if len(name) <= 10 else name[:9] + "…"
            label = f"{html.escape(short)} {pct:.0f}%"
            out.append(f'<rect x="{lx}" y="220" width="8" height="8" rx="2" fill="{LANG_COLORS[min(i, 5)]}"/>'
                       f'<text x="{lx + 13}" y="228" fill="#c9d1d9" font-size="11">{label}</text>')
            lx += 13 + 6.3 * len(label) + 16
    else:
        out.append('<text x="24" y="222" fill="#6e7681" font-size="12">Waiting for the first workflow run…</text>')

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="GitHub statistics for {html.escape(user)}">
<title>GitHub statistics for {html.escape(user)}</title>
<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#150d26"/></linearGradient></defs>
<rect width="{W}" height="{H}" rx="14" fill="url(#bg)"/>
<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="14" fill="none" stroke="#30363d"/>
<g font-family="'Segoe UI',Helvetica,Arial,sans-serif">
<text x="24" y="30" fill="{PURPLE}" font-size="13" font-weight="700" letter-spacing="3">GITHUB STATS</text>
<text x="{W - 24}" y="30" fill="#6e7681" font-size="12" text-anchor="end">@{html.escape(user)}</text>
{"".join(out)}
</g>
</svg>
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", default=os.environ.get("GH_USER", "nithesh2711"))
    ap.add_argument("--source", choices=["graphql", "empty"], default="graphql")
    ap.add_argument("--out", default="assets/github-stats.svg")
    args = ap.parse_args()

    data = None
    if args.source == "graphql":
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            sys.exit("error: set GITHUB_TOKEN (or use --source empty)")
        data = fetch(args.user, token)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(build(args.user, data))
    print(f"wrote {args.out} ({'live data' if data else 'empty placeholder'})")


if __name__ == "__main__":
    main()
