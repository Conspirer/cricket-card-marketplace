"""The hand-entered scorecard checker (data/withheld_matches/)."""

from backend.scripts import withheld_matches as wm

MATCHES = {
    "T": {"match_key": "T", "format": "TEST"},
    "O": {"match_key": "O", "format": "ODI"},
}


def row(match_key="T", player_id=1, **counts):
    r = {"match_key": match_key, "player_id": str(player_id), "player": f"P{player_id}"}
    r.update({k: str(counts.get(k, 0)) for k in wm.COUNTS})
    return r


def full_xi(match_key, **counts):
    return [row(match_key, pid, **counts) for pid in range(1, 12)]


def test_the_shipped_files_parse_and_have_the_four_matches():
    matches, rows = wm.load()
    assert len(matches) == 4 and {m["format"] for m in matches.values()} == {"TEST", "ODI"}
    errors, _ = wm.check(matches, rows)
    assert not errors


def test_a_complete_valid_scorecard_passes():
    rows = full_xi("T") + full_xi("O")
    rows[0] = row("T", 1, innings_batted=2, runs=126, balls_faced=177, dismissals=1, fours=12, sixes=2, hundreds=1)
    rows[1] = row("T", 2, innings_batted=0, balls_bowled=132, runs_conceded=33, wickets=6, catches=1)
    errors, warnings = wm.check(MATCHES, rows)
    assert errors == [] and warnings == []


def test_impossible_figures_are_errors():
    cases = [
        row("O", 1, innings_batted=2),                              # two innings in an ODI
        row("T", 1, innings_batted=1, dismissals=2),                # out more often than batted
        row("T", 1, innings_batted=1, runs=90, hundreds=1),         # a hundred without 100 runs
        row("T", 1, runs=20, balls_faced=30),                       # runs without batting
        row("T", 1, innings_batted=1, runs=10, fours=3),            # boundaries exceed runs
        row("T", 1, wickets=2),                                     # wickets without bowling
        row("X", 1),                                                # unknown match
        {**row("T", 1), "runs": "ten"},                             # not a number
    ]
    for bad in cases:
        errors, _ = wm.check(MATCHES, full_xi("T")[1:] + [bad])
        assert errors, bad


def test_duplicates_and_short_or_oversized_xis():
    errors, _ = wm.check(MATCHES, full_xi("T") + [row("T", 1)])
    assert any("appears 2 times" in e for e in errors)
    errors, warnings = wm.check(MATCHES, full_xi("T")[:9])
    assert not errors and any("only 9 players" in w for w in warnings) and any("O: no players" in w for w in warnings)
    errors, _ = wm.check(MATCHES, full_xi("T") + [row("T", 12), row("T", 13)])
    assert any("more than an XI" in e for e in errors)
