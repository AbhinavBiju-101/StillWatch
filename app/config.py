import os

try:                       # optional: load a local .env file during development
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env(name, default=""):
    return os.environ.get(name, default)


class Config:
    SECRET_KEY = _env("SECRET_KEY", "dev-only-change-me")
    DATABASE_URL = _env("DATABASE_URL")            # Supabase Postgres connection string
    DEVICE_TOKEN = _env("DEVICE_TOKEN")            # shared secret the ESP32 sends
    DEMO_PASSWORD = _env("DEMO_PASSWORD")          # empty = account disabled
    TESTING_PASSWORD = _env("TESTING_PASSWORD")    # empty = account disabled

    # LLM interpreter: none | gemini | ollama
    LLM_PROVIDER = _env("LLM_PROVIDER", "none")
    GEMINI_API_KEY = _env("GEMINI_API_KEY")
    GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.5-flash")   # check current model names
    OLLAMA_URL = _env("OLLAMA_URL", "http://localhost:11434")
    OLLAMA_MODEL = _env("OLLAMA_MODEL", "llama3.2:3b")

    ALERT_COOLDOWN_MIN = int(_env("ALERT_COOLDOWN_MIN", "180"))
    DEMO_REFRESH_HOURS = float(_env("DEMO_REFRESH_HOURS", "2"))
    BEHIND_PROXY = _env("BEHIND_PROXY", "0") == "1"   # 1 on Render: trust X-Forwarded-For/-Proto
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env("COOKIE_SECURE", "0") == "1"   # set to 1 on Render (HTTPS)
