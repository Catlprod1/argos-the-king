"""Read-only systemd market watch status for owner messages."""

from __future__ import annotations

import subprocess


RULE = ("At 5 minutes past each UTC hour, check configured symbols using public Bybit "
        "MAINNET spot closed 1h candles. Alert only if the latest close exceeds the previous "
        "12 hourly highs AND its volume is at least twice the previous 12-hour median. "
        "Deduplicate by symbol and candle. No account key, OpenAI call, trade or order. "
        "No UI or custom rule editor. /assess SYMBOL is a separate on-demand research command.")


def timer_state() -> str:
    """Report the actual local timer state without inventing it on failure."""
    try:
        enabled = subprocess.run(["systemctl", "is-enabled", "argos-watch.timer"],
                                 capture_output=True, text=True, timeout=3, check=False)
        active = subprocess.run(["systemctl", "is-active", "argos-watch.timer"],
                                capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown (systemd could not be queried)"
    if enabled.returncode == 0 and enabled.stdout.strip() == "enabled" and active.returncode == 0 and active.stdout.strip() == "active":
        return "enabled and active (scheduled hourly)"
    if enabled.stdout.strip() in ("disabled", "masked", "static", "enabled") and active.stdout.strip() in ("inactive", "failed", "active"):
        return f"{enabled.stdout.strip()}, {active.stdout.strip()}"
    return "unknown (systemd status unavailable)"


def watch_context(symbols: list[str]) -> str:
    return (f"Market watch timer: {timer_state()}. Configured symbols: {', '.join(symbols) or 'none'}. "
            f"Installed rule: {RULE} /status reports the timer state; /brief does not. "
            "An enabled timer alone does not prove the latest fetch succeeded; inspect the service result for failures.")
