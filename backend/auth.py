"""Accounts: username + password, server-side sessions in an HttpOnly cookie.

Passwords are hashed with scrypt (stdlib, per-user salt). A session is a
random 256-bit token; the cookie carries the token, the database stores only
its SHA-256. Every endpoint that acts for a user checks the session with
`current_user_id` and refuses to act for anyone else (`require_self`).
"""

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from backend.database import get_connection

COOKIE = "crease_session"
SESSION_DAYS = 30

USERNAME = re.compile(r"^[A-Za-z0-9_]{3,20}$")
MIN_PASSWORD = 8

# scrypt cost: n=2^14, r=8 is the widely used interactive-login setting (~16 MB).
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1

router = APIRouter(prefix="/auth")


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
    except (AttributeError, ValueError):
        return False
    if scheme != "scrypt":
        return False
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    return hmac.compare_digest(candidate, bytes.fromhex(digest))


# A real hash to check against when the username doesn't exist, so a login
# attempt takes the same time whether or not the account exists.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(cursor, response, request, user_id):
    token = secrets.token_urlsafe(32)
    cursor.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, now() + %s);",
        (_token_hash(token), user_id, timedelta(days=SESSION_DAYS)),
    )
    response.set_cookie(
        COOKIE, token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,                          # not readable from JavaScript
        samesite="lax",                         # not sent on cross-site POSTs
        secure=request.url.scheme == "https",   # HTTPS-only in production
        path="/",
    )


def current_user_id(request: Request):
    """The logged-in user's id, or 401."""
    token = request.cookies.get(COOKIE)
    if token:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT user_id FROM sessions WHERE token_hash = %s AND expires_at > now();",
                    (_token_hash(token),),
                )
                row = cursor.fetchone()
        if row:
            return row["user_id"]
    raise HTTPException(401, "Log in first")


def require_self(me, user_id):
    if me != user_id:
        raise HTTPException(403, "You can only act as yourself")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class Credentials(BaseModel):
    username: str
    password: str


def _me(cursor, user_id):
    cursor.execute("SELECT id, username, balance FROM users WHERE id = %s;", (user_id,))
    return cursor.fetchone()


@router.post("/register")
def register(body: Credentials, request: Request, response: Response):
    from backend.main import SIGNUP_GRANT, apply_balance_change  # avoid an import cycle

    username = body.username.strip()
    if not USERNAME.match(username):
        raise HTTPException(400, "Usernames are 3-20 letters, digits or underscores")
    if len(body.password) < MIN_PASSWORD:
        raise HTTPException(400, f"Passwords need at least {MIN_PASSWORD} characters")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM users WHERE lower(username) = lower(%s);", (username,))
            if cursor.fetchone():
                raise HTTPException(409, "That username is taken")
            try:
                cursor.execute(
                    "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id;",
                    (username, hash_password(body.password)),
                )
            except Exception as error:  # a simultaneous registration took the name
                if "users_username_lower_key" in str(error) or "users_username_key" in str(error):
                    raise HTTPException(409, "That username is taken")
                raise
            user_id = cursor.fetchone()["id"]
            apply_balance_change(cursor, user_id, SIGNUP_GRANT, "MINT_SIGNUP")
            start_session(cursor, response, request, user_id)
            return _me(cursor, user_id)


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, password_hash FROM users WHERE lower(username) = lower(%s);",
                (body.username.strip(),),
            )
            row = cursor.fetchone()
            ok = verify_password(body.password, row["password_hash"] if row and row["password_hash"] else _DUMMY_HASH)
            if not row or not row["password_hash"] or not ok:
                raise HTTPException(401, "Wrong username or password")
            start_session(cursor, response, request, row["id"])
            return _me(cursor, row["id"])


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM sessions WHERE token_hash = %s;", (_token_hash(token),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user_id: int = Depends(current_user_id)):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            return _me(cursor, user_id)
