import os
from collections import defaultdict
import requests
from espn_api.football import League

try:
    from dotenv import load_dotenv
    load_dotenv("fanta.env")
except ImportError:
    pass

MY_HINTS = ("head bangers", "edwin", "familia")
FA_SIZE = 100
BENCH = {"BE", "BN", "IR", "IR+", "IR-", "NA"}
POS_ORDER = ("QB", "RB", "WR", "TE", "K", "D/ST")
MIN_KEEP = {"QB": 2, "RB": 4, "WR": 4, "TE": 2, "K": 1, "D/ST": 1}

def env(name, default=None):
    val = os.getenv(name, default)
    if val is None or val == "":
        return default
    return val

def owner_names(team):
    owners = getattr(team, "owners", None) or [getattr(team, "owner", "?")]
    names = []
    for o in owners:
        if isinstance(o, dict):
            full = f"{o.get('firstName', '')} {o.get('lastName', '')}".strip()
            names.append(full or o.get("displayName", "?"))
        elif isinstance(o, str):
            names.append(o)
        else:
            names.append(getattr(o, "displayName", None) or str(o))
    return ", ".join(names) or "?"

def proj(p):
    for attr in ("projected_points", "projected_total_points"):
        val = getattr(p, attr, None)
        if val is not None:
            return float(val or 0)
    return 0.0

def is_hurt(p):
    return (p.injuryStatus or "").upper() not in ("", "ACTIVE", "NORMAL")

def pos_of(p):
    return (p.position or "").upper()

def lineup_split(lineup):
    s, b = [], []
    for p in lineup:
        slot = getattr(p, "slot_position", "") or ""
        (b if slot in BENCH else s).append(p)
    return s, b

def tg_send(text):
    token = env("TELEGRAM_TOKEN")
    chat = env("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": chat, "text": text, "parse_mode": "Markdown"},
        timeout=30,
    )
    if not r.ok:
        # reintenta sin markdown si ESPN pone un _ o *
        r2 = requests.post(
            url,
            json={"chat_id": chat, "text": text},
            timeout=30,
        )
        r2.raise_for_status()

def main():
    league = League(
        league_id=int(env("LEAGUE_ID")),
        year=int(env("YEAR", "2026")),
        espn_s2=env("ESPN_S2"),
        swid=env("SWID"),
    )
    week = league.current_week
    my = None
    for t in league.teams:
        blob = f"{t.team_name} {owner_names(t)}".lower()
        if any(h in blob for h in MY_HINTS):
            my = t
            break
    if my is None:
        my = league.teams[0]

    opp = None
    my_lineup = opp_lineup = []
    for box in league.box_scores(week):
        if box.home_team.team_id == my.team_id:
            opp, my_lineup, opp_lineup = box.away_team, box.home_lineup, box.away_lineup
            break
        if box.away_team.team_id == my.team_id:
            opp, my_lineup, opp_lineup = box.home_team, box.away_lineup, box.home_lineup
            break

    my_s, my_b = lineup_split(my_lineup) if my_lineup else ([], [])
    opp_s, _ = lineup_split(opp_lineup) if opp_lineup else ([], [])
    if not my_s:
        my_s, my_b = my.roster[:9], my.roster[9:]

    my_proj = sum(proj(p) for p in my_s)
    opp_proj = sum(proj(p) for p in opp_s) if opp else 0

    counts = defaultdict(int)
    by_pos = defaultdict(list)
    for p in my.roster:
        counts[pos_of(p)] += 1
        by_pos[pos_of(p)].append(p)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: proj(x))

    def drop_score(p):
        score = 20 - proj(p)
        if is_hurt(p):
            score += 8
        if pos_of(p) in ("K", "D/ST"):
            score += 2
        if counts[pos_of(p)] <= MIN_KEEP.get(pos_of(p), 1):
            score -= 25
        if p in my_s:
            score -= 15
        return score

    drops, used_pos = [], defaultdict(int)
    for p in sorted(my.roster, key=drop_score, reverse=True):
        if len(drops) >= 3:
            break
        if counts[pos_of(p)] - used_pos[pos_of(p)] <= MIN_KEEP.get(pos_of(p), 1):
            continue
        if drop_score(p) < 0:
            continue
        drops.append(p)
        used_pos[pos_of(p)] += 1

    fas = []
    try:
        fas = league.free_agents(size=FA_SIZE)
    except Exception:
        pass

    add_rows = []
    for fa in fas:
        pos = pos_of(fa)
        if is_hurt(fa) and proj(fa) < 6:
            continue
        worst = by_pos[pos][0] if by_pos[pos] else None
        delta = proj(fa) - (proj(worst) if worst else 0)
        if delta >= 0.8:
            add_rows.append((delta, fa, worst, pos))
    add_rows.sort(key=lambda x: x[0], reverse=True)

    adds, seen_pos = [], set()
    for row in add_rows:
        if len(adds) >= 3:
            break
        if row[3] in seen_pos and len(adds) < 2:
            continue
        adds.append(row)
        seen_pos.add(row[3])
    for row in add_rows:
        if len(adds) >= 3:
            break
        if row not in adds:
            adds.append(row)

    sit_moves = []
    s_pos, b_pos = defaultdict(list), defaultdict(list)
    for p in my_s:
        s_pos[pos_of(p)].append(p)
    for p in my_b:
        b_pos[pos_of(p)].append(p)
    for pos in POS_ORDER:
        sitters = sorted(s_pos.get(pos, []), key=proj)
        cands = sorted(b_pos.get(pos, []), key=lambda x: -proj(x))
        if sitters and cands:
            w, s = sitters[0], cands[0]
            d = proj(s) - proj(w)
            if d >= 1.5 or (is_hurt(w) and not is_hurt(s) and d > 0):
                sit_moves.append((d, w, s, pos))

    lines = [
        f"NFL Fantasy · Semana {week}",
        f"{my.team_name} {my.wins}-{my.losses}",
        f"Proy {my_proj:.1f} vs {opp.team_name if opp else '-'} {opp_proj:.1f} ({my_proj-opp_proj:+.1f})",
        "",
        "3 ADDS",
    ]
    if not adds:
        lines.append("No hay adds claros.")
    for i, (delta, fa, worst, pos) in enumerate(adds, 1):
        vs = worst.name if worst else "?"
        lines.append(f"{i}. {fa.name} ({pos}, {fa.proTeam}) {proj(fa):.1f} +{delta:.1f} vs {vs}")

    lines += ["", "3 DROPS"]
    if not drops:
        lines.append("No dropees a nadie.")
    for i, p in enumerate(drops, 1):
        lines.append(f"{i}. {p.name} ({p.position}) {proj(p):.1f} {p.injuryStatus}")

    lines += ["", "START / SIT"]
    if not sit_moves:
        lines.append("Sin swap claro.")
    for d, w, s, pos in sit_moves:
        lines.append(f"SIT {w.name} ({proj(w):.1f}) -> START {s.name} ({proj(s):.1f}) {pos} {d:+.1f}")

    if opp:
        hurt = [p for p in opp.roster if is_hurt(p)]
        lines += ["", f"Rival: {opp.team_name}"]
        if hurt:
            for p in hurt[:6]:
                lines.append(f"- {p.name} {p.position} {p.injuryStatus}")
        else:
            lines.append("Sin flags fuertes.")

    text = "\n".join(lines)
    print(text)
    tg_send(text)
    print("Enviado a Telegram.")

if __name__ == "__main__":
    main()