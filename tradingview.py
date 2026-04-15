"""TradingView connectivity health check."""

import time
import urllib.request
import urllib.error

_TV_HEALTH_URL = "https://www.tradingview.com"
_TIMEOUT = 5  # seconds


def tv_health_check() -> dict:
    """Probe TradingView to verify connectivity.

    Returns a dict with:
      connected  (bool)         – True if TradingView is reachable
      latency_ms (float | None) – round-trip time in milliseconds
      error      (str  | None)  – human-readable error when not connected
    """
    start = time.monotonic()
    try:
        req = urllib.request.Request(
            _TV_HEALTH_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            method="HEAD",
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            status = resp.status
        latency_ms = (time.monotonic() - start) * 1000
        connected = 200 <= status < 400
        return {
            "connected": connected,
            "latency_ms": round(latency_ms, 1),
            "error": None if connected else f"HTTP {status}",
        }
    except urllib.error.URLError as exc:
        return {
            "connected": False,
            "latency_ms": None,
            "error": str(exc.reason),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "connected": False,
            "latency_ms": None,
            "error": str(exc),
        }
