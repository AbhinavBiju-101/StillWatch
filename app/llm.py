"""LLM interpreter. Turns detector findings into a short plain-language note.

The LLM never decides whether to alert: detectors do that. It only explains.
If the provider is off, unreachable, or returns wording that breaks our rules,
interpret() returns None and the caller keeps the template message.
"""
import json
import logging
import re
import urllib.request

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You help operators of stored-water tanks (hostels, schools, villages).
A low-cost sensor node measures only proxies: pH, TDS, turbidity, and sometimes
temperature/ORP. It cannot detect bacteria, lead, arsenic, pesticides or fluoride.

Rules:
- Say only that unusual change was detected. NEVER say or imply the water is safe,
  potable, drinkable or fit for use, and never say it is unsafe either.
- Offer likely causes only as possibilities ("possibly", "could be"), using the
  readings, baseline and logged events you are given. Do not invent facts.
- Give one practical inspection step, and always recommend a lab test.
- 2 to 4 short sentences, plain language, no markdown."""

# Wording we never allow in output.
_BANNED = re.compile(r"\b(safe|safely|potable|drinkable|fit for (drinking|consumption))\b", re.I)


def build_prompt(context):
    return ("Write the alert note for this tank.\n\n"
            + json.dumps(context, indent=2, default=str))


def validate_output(text):
    """Returns cleaned text, or None if it breaks the rules."""
    text = (text or "").strip()
    if not text or len(text) > 800 or _BANNED.search(text):
        return None
    if "test" not in text.lower():
        text += " A lab test is recommended."
    return text


def _post_json(url, body, headers=None, timeout=30):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _gemini(cfg, prompt):
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{cfg['GEMINI_MODEL']}:generateContent")
    data = _post_json(url, {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": prompt}]}],
    }, headers={"x-goog-api-key": cfg["GEMINI_API_KEY"]})
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _ollama(cfg, prompt):
    data = _post_json(cfg["OLLAMA_URL"].rstrip("/") + "/api/generate", {
        "model": cfg["OLLAMA_MODEL"], "system": SYSTEM_PROMPT,
        "prompt": prompt, "stream": False,
    }, timeout=120)
    return data["response"]


def interpret(cfg, context):
    provider = cfg.get("LLM_PROVIDER", "none")
    try:
        if provider == "gemini" and cfg.get("GEMINI_API_KEY"):
            raw = _gemini(cfg, build_prompt(context))
        elif provider == "ollama":
            raw = _ollama(cfg, build_prompt(context))
        else:
            return None
    except Exception:                      # network, quota, bad response...
        log.exception("LLM call failed; keeping template message")
        return None
    cleaned = validate_output(raw)
    if cleaned is None:
        log.warning("LLM output rejected by validator")
    return cleaned
