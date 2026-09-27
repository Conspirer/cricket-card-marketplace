"""Battle balance harness: simulate bot battles on the real active pool.

    python -m backend.scripts.battle_balance              # 10,000 battles per matchup
    python -m backend.scripts.battle_balance --battles 20000 --seed 7

Uses the same rule functions as the API (backend/battles.py) and the verified
theme stats battles would snapshot. Bots:
  perfect  knows every true stat
  noisy    believes each stat is the true value x N(1, NOISE_SD), fixed per battle
  random   picks cards and stats uniformly
Knowledgeable bots pick the unused card strongest for the revealed theme
(mean percentile across the theme's stats); both bots then call the stat they
believe their own card wins by the widest percentile margin. No look-ahead.
"""

import argparse
import random
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict

from backend import battles as rules
from backend.database import get_connection
from backend.themes import STATS, THEME_WEIGHTS, THEMES

NOISE_SD = 0.25


def load_pool():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.id, p.name, p.role,
                       COALESCE(max(array_position(ARRAY['Common', 'Rare', 'Epic', 'Legendary'], d.rarity)) FILTER (WHERE d.is_active),
                                max(array_position(ARRAY['Common', 'Rare', 'Epic', 'Legendary'], d.rarity))) AS tier_rank,
                       COALESCE((SELECT jsonb_object_agg(t.theme, t.stats) FROM player_theme_stats t
                                  WHERE t.player_id = p.id AND t.verified), '{}'::jsonb) AS stats
                FROM players p JOIN card_definitions d ON d.player_id = p.id
                WHERE p.id IN (SELECT player_id FROM card_definitions WHERE is_active)
                GROUP BY p.id;
                """
            )
            rows = cursor.fetchall()
    pool = []
    for r in rows:
        tier = rules.TIER_ORDER[r["tier_rank"] - 1]
        pool.append({"id": r["id"], "name": r["name"], "role": r["role"], "tier": tier,
                     "credits": rules.credits_for_tier(tier), "stats": r["stats"]})
    return pool


class Percentiles:
    def __init__(self, pool):
        self.sorted = defaultdict(list)
        for card in pool:
            for theme, config in THEMES.items():
                for stat in config["stats"]:
                    value = rules.stat_value(card, theme, stat)
                    if value is not None:
                        self.sorted[(theme, stat)].append(value)
        for values in self.sorted.values():
            values.sort()

    def of(self, theme, stat, value):
        """0..1 where 1 is best; no data is 0."""
        if value is None:
            return 0.0
        values = self.sorted[(theme, stat)]
        if not values:
            return 0.5
        p = (bisect_left(values, value) + bisect_right(values, value)) / 2 / len(values)
        return 1 - p if STATS[stat]["lower_wins"] else p


class Bot:
    def __init__(self, level, pct, rng):
        self.level, self.pct, self.rng = level, pct, rng
        self.noise = {}

    def believed(self, card, theme, stat):
        value = rules.stat_value(card, theme, stat)
        if self.level == "perfect" or value is None:
            return value
        key = (card["id"], theme, stat)
        if key not in self.noise:
            self.noise[key] = max(0.0, self.rng.gauss(1, NOISE_SD))
        return value * self.noise[key]

    def strength(self, card, theme):
        stats = THEMES[theme]["stats"]
        return sum(self.pct.of(theme, s, self.believed(card, theme, s)) for s in stats) / len(stats)

    def pick(self, options, theme):
        if self.level == "random":
            return self.rng.choice(options)
        return max(options, key=lambda c: (self.strength(c, theme), self.rng.random()))

    def pick_for_stat(self, options, theme, stat):
        """Sudden death: the stat is known before the pick."""
        if self.level == "random":
            return self.rng.choice(options)
        return max(options, key=lambda c: (self.pct.of(theme, stat, self.believed(c, theme, stat)), self.rng.random()))

    def call(self, mine, theirs, theme):
        stats = THEMES[theme]["stats"]
        if self.level == "random":
            return self.rng.choice(stats)

        def edge(stat):
            a, b = self.believed(mine, theme, stat), self.believed(theirs, theme, stat)
            win = rules.compare(stat, a, b)
            margin = self.pct.of(theme, stat, a) - self.pct.of(theme, stat, b)
            return (win, margin, self.rng.random())

        return max(stats, key=edge)


def random_deck(pool, rng, min_cost=0, max_cost=rules.CREDIT_CAP):
    for _ in range(10000):
        deck = rng.sample(pool, rules.DECK_SIZE)
        cost = sum(c["credits"] for c in deck)
        if min_cost <= cost <= max_cost:
            return deck
    raise RuntimeError(f"no deck costing {min_cost}-{max_cost}")


def play(deck_a, deck_b, bot_a, bot_b, rng, log, battles_log=None):
    """One battle; A is the challenger. Returns 1 / -1 / 0 for A win / B win / draw."""
    used = {"A": set(), "B": set()}
    previous = None
    points = {"A": 0, "B": 0}
    decks, bots = {"A": deck_a, "B": deck_b}, {"A": bot_a, "B": bot_b}

    number = 0
    for number in range(1, rules.MAX_ROUNDS + 1):
        sudden = rules.is_sudden_death(number)
        hands = {side: decks[side] if sudden else [c for c in decks[side] if c["id"] not in used[side]] for side in "AB"}
        theme = rules.draw_round_theme(hands["A"], hands["B"], previous=previous, rng=rng)
        previous = theme
        if sudden:
            # One drawn stat, known before the pick; any deck card with it may be played.
            sd_stat = rules.draw_sudden_death_stat(theme, hands["A"], hands["B"], rng=rng)
            cards = {side: bots[side].pick_for_stat(rules.pickable(hands[side], theme, sd_stat), theme, sd_stat)
                     for side in "AB"}
            result = rules.sudden_death(theme, sd_stat, cards["A"], cards["B"])["result"]
            points["A"] += result > 0
            points["B"] += result < 0
            log.append({"kind": "sudden_death", "theme": theme, "stat": sd_stat, "result": result})
            break
        cards = {}
        for side in "AB":
            cards[side] = bots[side].pick(rules.pickable(hands[side], theme), theme)
            used[side].add(cards[side]["id"])
        calls = {side: bots[side].call(cards[side], cards["B" if side == "A" else "A"], theme) for side in "AB"}
        scored = rules.score_round(theme, cards["A"], cards["B"], calls["A"], calls["B"])
        points["A"] += scored["challenger_points"]
        points["B"] += scored["opponent_points"]
        for side, key in (("A", "challenger_call"), ("B", "opponent_call")):
            call = scored[key]
            winner = "A" if call["result"] > 0 else "B" if call["result"] < 0 else None
            log.append({
                "kind": "call", "theme": theme, "stat": call["stat"], "caller": side,
                "point": winner, "own_point": winner == side,
                "winner_role": cards[winner]["role"] if winner else None,
            })
        log.append({"kind": "round", "theme": theme, "round": number,
                    "split": (scored["challenger_points"], scored["opponent_points"])})
        if rules.battle_over(number, points["A"], points["B"]):
            break

    winner, decided_by = rules.outcome(number, points["A"], points["B"])
    if battles_log is not None:
        battles_log.append({"rounds": number, "sudden_death": rules.is_sudden_death(number),
                            "drawn": winner is None, "decided_by": decided_by})
    return {"challenger": 1, "opponent": -1, None: 0}[winner]


def matchup(pool, pct, level_a, level_b, n, rng, deck_a=None, deck_b=None):
    tally, log, battles = Counter(), [], []
    for _ in range(n):
        a = deck_a(rng) if deck_a else random_deck(pool, rng)
        b = deck_b(rng) if deck_b else random_deck(pool, rng)
        # Alternate who challenges; with simultaneous calls it shouldn't matter.
        if rng.random() < 0.5:
            tally[play(a, b, Bot(level_a, pct, rng), Bot(level_b, pct, rng), rng, log, battles)] += 1
        else:
            tally[-play(b, a, Bot(level_b, pct, rng), Bot(level_a, pct, rng), rng, log, battles)] += 1
    return tally, log, battles


def pct(x, total):
    return f"{100 * x / total:5.1f}%" if total else "   – "


# Assume players use about half of each timer; the reveal pause is fixed.
PICK_SECONDS_USED = rules.CARD_PICK_SECONDS / 2
CALL_SECONDS_USED = rules.CALL_SECONDS / 2


def minutes(rounds):
    # Regulation rounds have a pick and a call; sudden death is a pick only.
    # The final round ends the battle immediately: no reveal pause after it.
    regulation = min(rounds, rules.REGULATION_ROUNDS)
    sudden = rounds - regulation
    seconds = regulation * (PICK_SECONDS_USED + CALL_SECONDS_USED) + sudden * PICK_SECONDS_USED
    return (seconds + (rounds - 1) * rules.REVEAL_SECONDS) / 60


def splits(log):
    c = Counter()
    for r in log:
        if r["kind"] == "round":
            a, b = r["split"]
            c["1-1" if (a, b) == (1, 1) else "2-0" if max(a, b) == 2 else "1-0" if a + b == 1 else "0-0"] += 1
    return c


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--battles", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    n = args.battles

    pool = load_pool()
    percentiles = Percentiles(pool)
    tiers = Counter(c["tier"] for c in pool)
    print(f"pool: {len(pool)} players, tiers {dict(tiers)}; {n:,} battles per matchup, seed {args.seed}")
    print(f"theme weights: {THEME_WEIGHTS}\n")

    results = {}
    print("1. Knowledge (A's results; decks random within the cap)")
    for a, b in [("perfect", "random"), ("noisy", "random"), ("perfect", "noisy"), ("perfect", "perfect"), ("random", "random")]:
        t, log, battles = matchup(pool, percentiles, a, b, n, rng)
        results[(a, b)] = (log, battles)
        print(f"   {a:>7} vs {b:<7}  win {pct(t[1], n)}  draw {pct(t[0], n)}  loss {pct(t[-1], n)}")

    print("\n2. Round splits, sudden death and draws")
    for key in [("perfect", "perfect"), ("perfect", "noisy"), ("perfect", "random")]:
        log, battles = results[key]
        c = splits(log)
        total = sum(c.values())
        sd = sum(b["sudden_death"] for b in battles)
        drawn = sum(b["drawn"] for b in battles)
        how = Counter(b["decided_by"] for b in battles)
        print(f"   {key[0]} vs {key[1]:<8} rounds 1-1 {pct(c['1-1'], total)}  2-0 {pct(c['2-0'], total)}  "
              f"1-0 {pct(c['1-0'], total)}  0-0 {pct(c['0-0'], total)}   "
              f"sudden death {pct(sd, len(battles))}  drawn {pct(drawn, len(battles))}")
        sd_log = [r for r in log if r["kind"] == "sudden_death"]
        sd_draws = sum(r["result"] == 0 for r in sd_log)
        print(f"   {'':<20} decided in regulation {pct(how['regulation'], len(battles))}  in sudden death {pct(how['sudden_death'], len(battles))}  "
              f"draw {pct(how['draw'], len(battles))}   (sudden deaths drawn: {pct(sd_draws, len(sd_log)).strip()})")

    print("\n3. Per theme (perfect vs perfect): calls that score no point, rounds that end level")
    log, battles = results[("perfect", "perfect")]
    by_theme = defaultdict(Counter)
    for r in log:
        if r["kind"] == "call":
            by_theme[r["theme"]]["calls"] += 1
            by_theme[r["theme"]]["no_point"] += r["point"] is None
        elif r["kind"] == "round":
            by_theme[r["theme"]]["rounds"] += 1
            by_theme[r["theme"]]["level"] += r["split"][0] == r["split"][1]
    for theme in THEMES:
        c = by_theme[theme]
        print(f"   {THEMES[theme]['label']:<27} rounds {c['rounds']:>6,}  no-point calls {pct(c['no_point'], c['calls'])}  "
              f"level rounds {pct(c['level'], c['rounds'])}")

    print("\n4. Battle length (perfect vs perfect), players using half of each timer")
    lengths = Counter(b["rounds"] for b in battles)
    mean_rounds = sum(b["rounds"] for b in battles) / len(battles)
    mean_minutes = sum(minutes(b["rounds"]) for b in battles) / len(battles)
    print(f"   mean {mean_rounds:.2f} rounds, {mean_minutes:.1f} min   "
          f"(6 rounds {minutes(6):.1f} min, 7 rounds {minutes(7):.1f} min)")
    print("   rounds played: " + ", ".join(f"{k}: {pct(v, len(battles)).strip()}" for k, v in sorted(lengths.items())))

    print("\n5. Upsets: 60-credit deck (all Common tier) vs 95-100-credit deck, both perfect")
    commons = [c for c in pool if c["tier"] == "Common"]
    low = lambda r: r.sample(commons, rules.DECK_SIZE)
    high = lambda r: random_deck(pool, r, min_cost=95)
    t, _, _ = matchup(pool, percentiles, "perfect", "perfect", n, rng, deck_a=low, deck_b=high)
    print(f"   low-credit deck  win {pct(t[1], n)}  draw {pct(t[0], n)}  loss {pct(t[-1], n)}")

    print("\n6. Role balance of points won (perfect vs perfect)")
    roles_in_pool = Counter(c["role"] for c in pool)
    winners = Counter(r["winner_role"] for r in log if r["kind"] == "call" and r["winner_role"])
    decided = sum(winners.values())
    for role in ("Batter", "All-rounder", "Bowler"):
        print(f"   {role:<12} {pct(roles_in_pool[role], len(pool))} of pool   {pct(winners[role], decided)} of points")
    calls = Counter(r["stat"] for r in log if r["kind"] == "call")
    total_calls = sum(calls.values())
    own = sum(r["own_point"] for r in log if r["kind"] == "call")
    print(f"   calls that score for the caller: {pct(own, total_calls).strip()}")
    print("   stats called: " + ", ".join(f"{STATS[s]['label']} {pct(v, total_calls).strip()}" for s, v in calls.most_common()))


if __name__ == "__main__":
    main()
