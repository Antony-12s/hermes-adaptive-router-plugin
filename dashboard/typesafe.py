"""TypeSafe (Jev) API keys: a free key check through GET /v1/models.

TypeSafe's API has no balance or usage endpoint (only /v1/models and /v1/systemone), so a
key check is the live reading HARP can make; balance lives in the TypeSafe console.
The key is sent only in the Authorization header and never returned or logged.
"""

import json
import time
import urllib.error
import urllib.request

BASE_URL = "https://api.typesafe.ai/v1"
CONSOLE_URL = "https://console.typesafe.ai/home"
PROBLEMS = {401: "rejected: invalid key", 403: "rejected: no access", 429: "rate-limited", 529: "TypeSafe overloaded"}


def check(key, base_url=BASE_URL, opener=urllib.request.urlopen):
    request = urllib.request.Request(base_url.rstrip("/") + "/models",
                                     headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                                              "User-Agent": "hermes-harp"})
    started = time.monotonic()
    result = {"ok": False, "status": None, "models": [], "problem": None, "checked_at": time.time()}
    try:
        with opener(request, timeout=15) as response:
            body = json.loads(response.read() or b"{}")
            result.update(ok=True, status=getattr(response, "status", 200),
                          models=[m.get("name") for m in body.get("models", []) if isinstance(m, dict) and m.get("name")])
    except urllib.error.HTTPError as error:
        result.update(status=error.code, problem=PROBLEMS.get(error.code, f"HTTP {error.code}"))
    except (urllib.error.URLError, TimeoutError, OSError):
        result["problem"] = "unreachable"
    except ValueError:
        result.update(ok=True, status=200, problem="unexpected answer")
    result["ms"] = round((time.monotonic() - started) * 1000)
    return result


def key_accepted(result):
    """True when TypeSafe recognised the key, even if it is busy or rate-limited right now."""
    return result.get("ok") or result.get("status") in (429, 529)
