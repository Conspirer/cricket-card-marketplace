"""Battle balance harness: simulate bot battles on the real active pool.

    python -m backend.scripts.battle_balance              # 10,000 battles per matchup
    python -m backend.scripts.battle_balance --battles 20000 --seed 7

Uses the same rule functions as the API (backend/battles.py) and the verified
theme stats battles would snapshot. Bots:
  perfect  knows every true stat
  noisy    believes each stat is the true value x N(1, NOISE_SD), fixed per battle
  random   picks cards and stats uniformly
Knowledgeable bots pick the unused card strongest for the revealed theme
(mean percentile across the theme's stats) and call the stat they believe
they win by the widest percentile margin. No look-ahead.
"""

import argparse
import random
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict

from backend import battles as rules
from backend.database import get_connection
from backend.themes import STATS, THEMES

NOISE_SD = 0.25


def load_pool():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.id, p.name, p.role,
                       max(array_position(ARRAY['Common', 'Rare', 'Epic', 'Legendary'], d.rarity)) AS tier_rank,
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


def play(deck_a, deck_b, bot_a, bot_b, rng, log):
    """One battle; A is the challenger. Returns 1 / -1 / 0 for A win / B win / draw."""
    themes = rules.draw_themes(rng=rng)
    sd_caller = rng.choice("AB")
    used = {"A": set(), "B": set()}
    wins = {"A": 0, "B": 0}
    decks, bots = {"A": deck_a, "B": deck_b}, {"A": bot_a, "B": bot_b}

    for number in range(1, rules.SUDDEN_DEATH_ROUND + 1):
        if number == rules.SUDDEN_DEATH_ROUND and wins["A"] != wins["B"]:
            break
        theme = themes[number - 1]
        cards = {}
        for side in "AB":
            options = decks[side] if number == rules.SUDDEN_DEATH_ROUND else [c for c in decks[side] if c["id"] not in used[side]]
            cards[side] = bots[side].pick(options, theme)
            used[side].add(cards[side]["id"])
        caller = rules.caller_for_round(number, "A", "B", sd_caller)
        other = "B" if caller == "A" else "A"
        stat = bots[caller].call(cards[caller], cards[other], theme)
        a_value = rules.stat_value(cards["A"], theme, stat)
        b_value = rules.stat_value(cards["B"], theme, stat)
        result = rules.compare(stat, a_value, b_value)
        if result > 0:
            wins["A"] += 1
        elif result < 0:
            wins["B"] += 1
        log.append({
            "round": number, "theme": theme, "stat": stat, "caller": caller, "result": result,
            "no_data_draw": result == 0 and a_value is None and b_value is None,
            "winner_role": cards["A"]["role"] if result > 0 else cards["B"]["role"] if result < 0 else None,
            "caller_won": (result > 0) == (caller == "A") and result != 0,
        })
    return (wins["A"] > wins["B"]) - (wins["A"] < wins["B"])


def matchup(pool, pct, level_a, level_b, n, rng, deck_a=None, deck_b=None):
    tally, log = Counter(), []
    for _ in range(n):
        a = deck_a(rng) if deck_a else random_deck(pool, rng)
        b = deck_b(rng) if deck_b else random_deck(pool, rng)
        # Alternate who challenges so neither side keeps the odd-round calls.
        if rng.random() < 0.5:
            tally[play(a, b, Bot(level_a, pct, rng), Bot(level_b, pct, rng), rng, log)] += 1
        else:
            tally[-play(b, a, Bot(level_b, pct, rng), Bot(level_a, pct, rng), rng, [])] += 1
    return tally, log


def pct(x, total):
    return f"{100 * x / total:5.1f}%" if total else "   – "


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
    print(f"pool: {len(pool)} players, tiers {dict(tiers)}; {n:,} battles per matchup, seed {args.seed}\n")

    print("1. Knowledge (A's results; decks random within the cap)")
    for a, b in [("perfect", "random"), ("noisy", "random"), ("perfect", "noisy"), ("random", "random")]:
        t, _ = matchup(pool, percentiles, a, b, n, rng)
        print(f"   {a:>7} vs {b:<7}  win {pct(t[1], n)}  draw {pct(t[0], n)}  loss {pct(t[-1], n)}")

    print("\n2. Caller advantage per round (perfect vs perfect)")
    _, log = matchup(pool, percentiles, "perfect", "perfect", n, rng)
    by_round = defaultdict(Counter)
    for r in log:
        by_round[r["round"]]["caller" if r["caller_won"] else "draw" if r["result"] == 0 else "other"] += 1
    for number in sorted(by_round):
        c = by_round[number]
        total = sum(c.values())
        label = "sudden death" if number == rules.SUDDEN_DEATH_ROUND else f"round {number}"
        print(f"   {label:<13} caller wins {pct(c['caller'], total)}  draw {pct(c['draw'], total)}  caller loses {pct(c['other'], total)}  (n={total:,})")

    print("\n3. Draws per theme (perfect vs perfect)")
    by_theme = defaultdict(Counter)
    for r in log:
        by_theme[r["theme"]]["rounds"] += 1
        by_theme[r["theme"]]["draw"] += r["result"] == 0
        by_theme[r["theme"]]["no_data"] += r["no_data_draw"]
    for theme in THEMES:
        c = by_theme[theme]
        print(f"   {THEMES[theme]['label']:<27} rounds {c['rounds']:>6,}  draws {pct(c['draw'], c['rounds'])}  (both no data {pct(c['no_data'], c['rounds'])})")

    print("\n4. Upsets: 60-credit deck (all Common tier) vs 95-100-credit deck, both perfect")
    commons = [c for c in pool if c["tier"] == "Common"]
    low = lambda r: r.sample(commons, rules.DECK_SIZE)
    high = lambda r: random_deck(pool, r, min_cost=95)
    t, _ = matchup(pool, percentiles, "perfect", "perfect", n, rng, deck_a=low, deck_b=high)
    print(f"   low-credit deck  win {pct(t[1], n)}  draw {pct(t[0], n)}  loss {pct(t[-1], n)}")

    print("\n5. Role balance in decided rounds (perfect vs perfect)")
    roles_in_pool = Counter(c["role"] for c in pool)
    winners = Counter(r["winner_role"] for r in log if r["winner_role"])
    decided = sum(winners.values())
    for role in ("Batter", "All-rounder", "Bowler"):
        print(f"   {role:<12} {pct(roles_in_pool[role], len(pool))} of pool   {pct(winners[role], decided)} of round wins")
    calls = Counter(r["stat"] for r in log)
    total_calls = sum(calls.values())
    print("   stats called: " + ", ".join(f"{STATS[s]['label']} {pct(v, total_calls).strip()}" for s, v in calls.most_common()))


if __name__ == "__main__":
    main()
