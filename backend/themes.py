"""Battle themes: which stats each theme plays, and how its data is sourced.

Every stat value shown in a battle must be complete and correct, so stat sets
contain only stats that could be made complete for the whole active pool
(see scripts/build_theme_stats.py and its report).
"""

STATS = {
    "runs":            {"label": "Runs",            "lower_wins": False, "decimals": 0},
    "batting_average": {"label": "Batting average", "lower_wins": False, "decimals": 2},
    "strike_rate":     {"label": "Strike rate",     "lower_wins": False, "decimals": 2},
    "hundreds":        {"label": "Hundreds",        "lower_wins": False, "decimals": 0},
    "sixes":           {"label": "Sixes",           "lower_wins": False, "decimals": 0},
    "wickets":         {"label": "Wickets",         "lower_wins": False, "decimals": 0},
    "bowling_average": {"label": "Bowling average", "lower_wins": True,  "decimals": 2},
    "economy":         {"label": "Economy",         "lower_wins": True,  "decimals": 2},
    "catches":         {"label": "Catches",         "lower_wins": False, "decimals": 0},
}

# Rate stats need a minimum sample; below it the stat is "no data".
# "batting" samples are matches for Wikipedia-sourced themes (infoboxes have
# no innings column) and innings for Cricsheet-sourced ones.
FORMAT_MIN_BATTING_MATCHES = 10
FORMAT_MIN_BALLS_BOWLED = 300
IPL_MIN_BATTING_INNINGS = 10
IPL_MIN_BALLS_BOWLED = 300
TOURNAMENT_MIN_BATTING_INNINGS = 3
ODI_TOURNAMENT_MIN_BALLS_BOWLED = 120
T20_TOURNAMENT_MIN_BALLS_BOWLED = 48

# Tournament editions Cricsheet covers completely. An edition qualifies only
# when every match with a ball bowled is present: Cricsheet's match count
# equals the official total (Wikipedia edition infobox), or the only gaps are
# matches abandoned without a ball bowled (checked against the edition's
# match reports). Editions featuring Afghanistan are excluded: Cricsheet
# withholds Afghanistan men's matches by policy.
#   ODI World Cup  2011 49/49. 2003 and 2007 have unexplained gaps; 2015,
#                  2019 and 2023 lack Afghanistan's matches.
#   T20 World Cup  2009 27/27. 2007 has an unexplained gap; 2010 onward lack
#                  Afghanistan's matches.
#   Champions Tr.  2006 21/21, 2009 15/15, 2013 15/15, 2017 15/15. 2004 has an
#                  unexplained gap; 2025 lacks Afghanistan's matches.
#   IPL            all 19 seasons. The 7 seasons short of the official total
#                  (2008, 2009, 2011, 2012, 2015, 2017, 2024) are short only by
#                  matches abandoned without a ball bowled.
ODI_WC_SEASONS = {"2010/11"}
T20_WC_SEASONS = {"2009"}
CT_SEASONS = {"2006/07", "2009/10", "2013", "2017"}

ODI_SET = ["runs", "batting_average", "hundreds", "wickets", "economy", "catches"]
T20I_SET = ["runs", "batting_average", "wickets", "economy", "catches"]
T20_BALL_BY_BALL_SET = ["runs", "strike_rate", "sixes", "wickets", "economy", "catches"]
ODI_BALL_BY_BALL_SET = ["runs", "batting_average", "strike_rate", "wickets", "economy", "catches"]

THEMES = {
    "TEST": {
        "label": "Test", "tier": "common", "source": "wikipedia", "format": "Test",
        "stats": ["runs", "batting_average", "hundreds", "wickets", "bowling_average", "catches"],
    },
    "ODI": {
        "label": "ODI", "tier": "common", "source": "wikipedia", "format": "ODI",
        # Strike rate needs balls faced, which only ball-by-ball data has, and
        # Cricsheet can't be complete for anyone who has played Afghanistan.
        "stats": ODI_SET,
    },
    "T20I": {
        "label": "T20I", "tier": "common", "source": "wikipedia", "format": "T20",
        # Strike rate and sixes dropped for the same reason as ODI strike rate.
        "stats": T20I_SET,
    },
    "IPL": {
        "label": "IPL 2008–2026", "tier": "common", "source": "cricsheet",
        "archive": "ipl_json.zip", "events": {"Indian Premier League"}, "seasons": None,
        "min_innings": IPL_MIN_BATTING_INNINGS, "min_balls": IPL_MIN_BALLS_BOWLED,
        "stats": T20_BALL_BY_BALL_SET,
    },
    "ODI_WC": {
        "label": "World Cup 2011", "tier": "rare", "source": "cricsheet",
        "archive": "odis_male_json.zip", "events": {"ICC Cricket World Cup", "ICC World Cup", "World Cup"},
        "seasons": ODI_WC_SEASONS,
        "min_innings": TOURNAMENT_MIN_BATTING_INNINGS, "min_balls": ODI_TOURNAMENT_MIN_BALLS_BOWLED,
        "stats": ODI_BALL_BY_BALL_SET,
    },
    "T20_WC": {
        "label": "T20 World Cup 2009", "tier": "rare", "source": "cricsheet",
        "archive": "t20s_male_json.zip", "events": {"ICC World Twenty20"}, "seasons": T20_WC_SEASONS,
        "min_innings": TOURNAMENT_MIN_BATTING_INNINGS, "min_balls": T20_TOURNAMENT_MIN_BALLS_BOWLED,
        "stats": T20_BALL_BY_BALL_SET,
    },
    "CT": {
        "label": "Champions Trophy 2006–2017", "tier": "rare", "source": "cricsheet",
        "archive": "odis_male_json.zip", "events": {"ICC Champions Trophy"}, "seasons": CT_SEASONS,
        "min_innings": TOURNAMENT_MIN_BATTING_INNINGS, "min_balls": ODI_TOURNAMENT_MIN_BALLS_BOWLED,
        "stats": ODI_BALL_BY_BALL_SET,
    },
}

# Battle theme draw weights: common themes most rounds, rare ones occasionally.
THEME_WEIGHTS = {"common": 22, "rare": 2}


def stat_set(theme):
    return THEMES[theme]["stats"]
