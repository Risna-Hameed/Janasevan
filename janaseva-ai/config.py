"""Shared settings (owned by all three; change only together)."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _secret_key():
    """Use SECRET_KEY from the environment; otherwise create a random one and keep it in instance/."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(BASE_DIR, "instance", "secret_key")
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        import secrets
        os.makedirs(os.path.dirname(path), exist_ok=True)
        key = secrets.token_hex(32)
        with open(path, "w") as fh:
            fh.write(key)
        return key


class Config:
    SECRET_KEY = _secret_key()
    DATABASE = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "instance", "janaseva.db"))
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024          # uploads over 6 MB are rejected (413)
    JSON_AS_ASCII = False                         # keep Malayalam readable in JSON


Config.PRIVATE_UPLOAD_DIR = os.environ.get("PRIVATE_UPLOAD_DIR", os.path.join(BASE_DIR, "instance", "private_documents"))
Config.SESSION_COOKIE_HTTPONLY = True
Config.SESSION_COOKIE_SAMESITE = "Lax"
Config.SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"   # set 1 behind HTTPS
Config.PERMANENT_SESSION_LIFETIME = 8 * 60 * 60                                      # official sessions: 8 hours
Config.PROCESSING_TARGET_DAYS = int(os.environ.get("PROCESSING_TARGET_DAYS", "7"))
Config.SEED_DEMO = os.environ.get("SEED_DEMO", "1") == "1"                            # fictional demo data on first run
