"""Auto-compress setting for the context card: a fixed set of percentages or Off.

Hermes reads compression.enabled and compression.threshold (a ratio) from config.yaml. HARP only
offers the presets below, so the card never writes a value Hermes would reject or floor oddly.
"""

PRESETS = (0.5, 0.6, 0.75, 0.85)
DEFAULT_THRESHOLD = 0.5


def parse_settings(config):
    """Read the effective setting from a loaded config.yaml dict."""
    section = config.get("compression") if isinstance(config, dict) else None
    section = section if isinstance(section, dict) else {}
    enabled = section.get("enabled", True) is not False
    raw = section.get("threshold", DEFAULT_THRESHOLD)
    threshold = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else DEFAULT_THRESHOLD
    return {"enabled": enabled, "threshold": threshold, "presets": list(PRESETS)}


def settings_change(body):
    """Validate a POST body into `hermes config set` writes: a list of (key, value) pairs."""
    if not isinstance(body, dict):
        raise ValueError("Send {enabled, threshold}.")
    if body.get("enabled") is False:
        return [("compression.enabled", "false")]
    threshold = body.get("threshold")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or round(float(threshold), 2) not in PRESETS:
        raise ValueError("Choose 50%, 60%, 75% or 85%, or turn auto-compress off.")
    return [("compression.enabled", "true"), ("compression.threshold", str(round(float(threshold), 2)))]


def context_view(usage):
    """What the card shows, from session.usage fields. None when Hermes has not reported a window yet."""
    if not isinstance(usage, dict):
        return None
    used, maximum = usage.get("context_used"), usage.get("context_max")
    if not (isinstance(used, int) and isinstance(maximum, int) and maximum > 0):
        return None
    percent = usage.get("context_percent")
    percent = min(100, max(0, int(percent))) if isinstance(percent, (int, float)) else round(used / maximum * 100)
    return {"used": used, "max": maximum, "percent": percent, "compressions": int(usage.get("compressions") or 0)}


def format_tokens(count):
    """124000 -> '124K', 1500000 -> '1.5M' (the card's compact form)."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M".replace(".0M", "M")
    if count >= 1_000:
        return f"{round(count / 1_000)}K"
    return str(count)
