"""Per-user sessions for tests. Kept out of conftest.py, which pytest imports
twice (as `conftest` and as `backend.tests.conftest`); a module imported by
both copies gives them one shared TOKENS."""

import json
import re

import httpx

from backend.auth import COOKIE

# Session token per user id, filled in by make_user. Requests act for whoever
# the request names (user_id / buyer_id / seller_id / challenger_id, or the
# /users/{id}/ path), so tests read like "Alice buys" and get Alice's cookie.
TOKENS = {}
ACTING_KEYS = ("user_id", "buyer_id", "seller_id", "challenger_id")
USER_PATH = re.compile(r"/users/(\d+)/")


def acting_user(url, params=None, body=None):
    for source in (params or {}, body or {}):
        for key in ACTING_KEYS:
            if key in source:
                return int(source[key])
    match = USER_PATH.search(str(url))
    return int(match.group(1)) if match else None


def session_cookie(user_id):
    token = TOKENS.get(user_id)
    return {COOKIE: token} if token else {}


def _attach_session(request):
    body = None
    if request.content and request.headers.get("content-type", "").startswith("application/json"):
        body = json.loads(request.content)
    user = acting_user(request.url, dict(request.url.params), body)
    request.headers.pop("cookie", None)
    if user in TOKENS:
        request.headers["cookie"] = f"{COOKIE}={TOKENS[user]}"


class acting:
    """httpx.get/post for parallel requests, with the acting user's session."""

    @staticmethod
    def post(url, json=None, params=None, **kw):
        return httpx.post(url, json=json, params=params,
                          cookies=session_cookie(acting_user(url, params, json)), **kw)

    @staticmethod
    def get(url, params=None, **kw):
        return httpx.get(url, params=params, cookies=session_cookie(acting_user(url, params)), **kw)
