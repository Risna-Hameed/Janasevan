"""Small helpers shared by P1 routes."""
import re

from flask import request

CLIENT_RE = re.compile(r"^[A-Za-z0-9\-]{8,64}$")


def client_id():
    """The browser sends a random id in X-Client-Id. Returns None if missing/invalid."""
    cid = request.headers.get("X-Client-Id", "")
    return cid if CLIENT_RE.match(cid) else None
