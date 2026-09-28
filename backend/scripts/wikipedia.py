"""Minimal English Wikipedia client: raw wikitext for article titles.

Content is CC BY-SA 4.0 (see the Credits page). Requests identify the app per
the Wikimedia User-Agent policy and are batched to stay light on the API.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "CreaseCardGame/0.1 (personal project; contact via github.com/Conspirer)"
BATCH = 50
RETRIES = 6


def open_json(request, timeout=60):
    """urlopen + JSON, backing off on 429/503 (honouring Retry-After)."""
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 503) or attempt == RETRIES - 1:
                raise
            wait = error.headers.get("Retry-After")
            time.sleep(int(wait) if wait and wait.isdigit() else 5 * 2 ** attempt)


def fetch_wikitext(titles):
    """{requested title: wikitext or None}. Follows redirects."""
    titles = list(dict.fromkeys(titles))
    out = {}
    for start in range(0, len(titles), BATCH):
        chunk = titles[start:start + BATCH]
        params = {
            "action": "query", "format": "json", "formatversion": "2", "redirects": "1",
            "prop": "revisions", "rvprop": "content|timestamp", "rvslots": "main",
            "titles": "|".join(chunk),
        }
        request = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers={"User-Agent": USER_AGENT})
        data = open_json(request)["query"]

        # requested -> normalized -> redirect target
        alias = {t: t for t in chunk}
        for step in ("normalized", "redirects"):
            for item in data.get(step, []):
                for requested, current in alias.items():
                    if current == item["from"]:
                        alias[requested] = item["to"]

        pages = {p["title"]: p for p in data.get("pages", [])}
        for requested, final in alias.items():
            page = pages.get(final)
            if page and "revisions" in page:
                out[requested] = page["revisions"][0]["slots"]["main"]["content"]
            else:
                out[requested] = None
        time.sleep(1)
    return out
