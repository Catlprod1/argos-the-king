"""Read six fixed public onion pages through Argos's local Tor SOCKS proxy."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from .config import Config

MAX_PAGE_BYTES = 1_500_000
MAX_OBSERVATIONS_PER_PAGE = 100
SOURCES = {
    "site_01": "prime46qpwswt3fqmydo3kpuihcq5x423usgzgqkt6lmeh7woqxmsyyd.onion",
    "site_02": "kfj2am4ee2asdqflt4tuxxwbeuzmh6tv64ojbqscc4u55skrechsxzad.onion",
    "site_03": "ue5sfh6yl74zon64c2swxomyojsyipiunod5w3eyhwt3ft6ladtgflad.onion",
    "site_04": "tormartrge7x4244fws34nct54pt5mym2qbjmobunag5rwhirtesgdad.onion",
    "site_05": "hidden2h77d53dvbftj6exhfi5llymfdxw5s5fe5nytauql2ztpxofad.onion",
    "site_06": "cfy7bcsb3yt6ll63itulkcbkmogagetwavh6z2egtynbbeaosqmd3iyd.onion",
}


class OnionObserverError(RuntimeError):
    pass


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)


def _settings(config: Config) -> tuple[str, set[str]]:
    section = config.section("crypto_monitoring")
    if section.get("enabled") is not True:
        raise OnionObserverError("Set crypto_monitoring.enabled to true after configuring Tor")
    proxy = os.environ.get(section.get("tor_proxy_env", "ARGOS_TOR_SOCKS_PROXY"),
                           "socks5h://127.0.0.1:9050").strip()
    if not re.fullmatch(r"socks5h?://127\.0\.0\.1:[0-9]{2,5}", proxy):
        raise OnionObserverError("Tor SOCKS proxy must use a localhost socks5h URL")
    allowed = section.get("allowed_source_ids", list(SOURCES))
    if (not isinstance(allowed, list) or len(allowed) != 6
            or any(not isinstance(x, str) for x in allowed)
            or set(allowed) != set(SOURCES) or len(set(allowed)) != 6):
        raise OnionObserverError("Configure exactly the six fixed source IDs")
    return proxy, set(allowed)


def _extract_identifiers(text: str, source_id: str) -> list[dict[str, str]]:
    found: dict[tuple[str, str, str], dict[str, str]] = {}
    patterns = (
        ("wallet_address", "bitcoin", re.compile(r"(?<![A-Za-z0-9])(?:bc1[ac-hj-np-z02-9]{11,71}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})(?![A-Za-z0-9])", re.I)),
        ("wallet_address", "ethereum", re.compile(r"(?<![A-Za-z0-9])0x[0-9a-fA-F]{40}(?![A-Za-z0-9])")),
        ("wallet_address", "tron", re.compile(r"(?<![A-Za-z0-9])T[1-9A-HJ-NP-Za-km-z]{33}(?![A-Za-z0-9])")),
        ("transaction_id", "ethereum", re.compile(r"(?<![A-Za-z0-9])0x[0-9a-fA-F]{64}(?![A-Za-z0-9])")),
    )
    for kind, chain, pattern in patterns:
        for value in pattern.findall(text):
            found[(kind, chain, value.lower())] = {
                "source_id": source_id, "kind": kind, "chain": chain, "value": value,
            }
    btc_hash = re.compile(r"\b(?:txid|transaction\s*(?:hash|id)|tx\s*hash)\b\s*[:=#-]?\s*([0-9a-fA-F]{64})\b", re.I)
    for value in btc_hash.findall(text):
        found[("transaction_id", "bitcoin", value.lower())] = {
            "source_id": source_id, "kind": "transaction_id", "chain": "bitcoin", "value": value,
        }
    return list(found.values())[:MAX_OBSERVATIONS_PER_PAGE]


def _page_text(response) -> str:
    content_type = response.headers.get("Content-Type", "")
    if not any(x in content_type.lower() for x in ("text/html", "text/plain")):
        raise ValueError("unsupported content type")
    size = response.headers.get("Content-Length")
    if size and int(size) > MAX_PAGE_BYTES:
        raise ValueError("page exceeds size cap")
    chunks = bytearray()
    for part in response.iter_content(65536):
        chunks.extend(part)
        if len(chunks) > MAX_PAGE_BYTES:
            raise ValueError("page exceeds size cap")
    text = bytes(chunks).decode("utf-8", errors="replace")
    if "html" in content_type.lower():
        parser = _TextOnly()
        parser.feed(text)
        text = " ".join(parser.parts)
    return html.unescape(text)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="crypto-observer-", delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            json.dump(value, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def poll_onions(config: Config, notifier, *, session=None, now=None) -> int:
    """GET the allow-listed onion roots through local Tor; store only IDs."""
    proxy, allowed_sources = _settings(config)
    request_error_types = ()
    if session is None:
        try:
            import requests
        except ImportError:
            raise OnionObserverError("Install Argos requests SOCKS dependency before polling") from None
        session = requests.Session()
        request_error_types = (requests.RequestException,)
    session.trust_env = False
    session.proxies = {"http": proxy, "https": proxy}

    state_path = config.data_directory / "crypto-observer-state.json"
    findings_path = config.data_directory / "crypto-observations.jsonl"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not isinstance(state.get("seen"), list):
            raise ValueError
    except FileNotFoundError:
        state = {"seen": []}
    except (json.JSONDecodeError, ValueError, TypeError):
        raise OnionObserverError("Local crypto observer state needs repair") from None

    seen = {x for x in state["seen"] if isinstance(x, str)}
    new_items: list[dict[str, str]] = []
    successes = 0
    scan_time = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    try:
        for source_id in SOURCES:
            if source_id not in allowed_sources:
                continue
            host = SOURCES[source_id]
            url = f"http://{host}/"
            try:
                response = session.get(url, timeout=(15, 35), allow_redirects=False,
                                       stream=True, headers={"Accept": "text/html,text/plain;q=0.8"})
                if 300 <= response.status_code < 400:
                    raise ValueError("redirect refused")
                if response.status_code != 200:
                    raise ValueError(f"HTTP {response.status_code}")
                final = urlparse(response.url)
                if final.scheme != "http" or final.hostname != host:
                    raise ValueError("unexpected response host")
                matches = _extract_identifiers(_page_text(response), source_id)
                successes += 1
                for item in matches:
                    fingerprint = hashlib.sha256("\x1f".join(
                        (source_id, item["kind"], item["chain"], item["value"])
                    ).encode()).hexdigest()
                    if fingerprint not in seen:
                        seen.add(fingerprint)
                        new_items.append({**item, "first_seen": scan_time})
            except Exception as exc:
                if request_error_types and isinstance(exc, request_error_types):
                    print(f"{source_id}: fetch failed ({type(exc).__name__})")
                else:
                    print(f"{source_id}: fetch failed ({type(exc).__name__})")
    except Exception as exc:
        raise OnionObserverError(f"Tor onion request failed ({type(exc).__name__})") from None
    if not successes:
        raise OnionObserverError("No allow-listed onion page could be read through Tor")

    if new_items:
        lines = ["Argos crypto mentions (read-only; public identifiers on monitored pages):"]
        lines.extend(f"{x['source_id']} · {x['chain']} {x['kind'].replace('_', ' ')} · {x['value']}"
                     for x in new_items[:18])
        if len(new_items) > 18:
            lines.append(f"Plus {len(new_items) - 18} additional identifiers; see local observer log.")
        lines.append("Page mentions are not verified transactions or trading signals.")
        try:
            notifier.send("\n".join(lines))
        except Exception as exc:
            raise OnionObserverError(f"Owner notification failed ({type(exc).__name__})") from None
        findings_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with findings_path.open("a", encoding="utf-8") as output:
                os.chmod(findings_path, 0o600)
                for item in new_items:
                    output.write(json.dumps(item, separators=(",", ":")) + "\n")
                output.flush()
                os.fsync(output.fileno())
        except OSError:
            raise OnionObserverError("Could not save local crypto observation log") from None
    _atomic_json(state_path, {
        "seen": list(seen)[-20000:], "checked_at": scan_time,
    })
    return len(new_items)
