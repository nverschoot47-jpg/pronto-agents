"""Pronto AI collector, stap 1.

Haalt een paar agents op via de Concordium Agent Registry Indexer, downloadt per
agent de agentkaart, vergelijkt de SHA-256 van de kaart met metadata_hash_hex
en schrijft het resultaat naar results.json.

Alleen lezen. Geen sleutels nodig. Instellingen via omgevingsvariabelen:
  INDEXER_URL  (standaard: de indexer die Pablo van Concordium noemde)
  NETWORK      mainnet of testnet (standaard: mainnet, altijd expliciet meegestuurd)
  LIMIT        aantal agents (standaard: 20)
"""
import hashlib
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import requests

INDEXER_URL = os.environ.get(
    "INDEXER_URL", "https://agent-registry-indexer.concordium.com"
).rstrip("/")
NETWORK = os.environ.get("NETWORK", "mainnet")
LIMIT = int(os.environ.get("LIMIT", "20"))
PAUSE_SECONDS = 1.0  # rustig aan: Pablo adviseert vertragen en cachen
TIMEOUT = 15
HEADERS = {"User-Agent": "pronto-ai-collector/0.1 (pronto-ai.be)"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_json(path, params=None):
    response = requests.get(
        f"{INDEXER_URL}{path}", params=params, headers=HEADERS, timeout=TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def check_agent(agent):
    """Download de kaart en vergelijk de hash. Fouten worden bewaard, niet verborgen."""
    expected = (agent.get("metadata_hash_hex") or "").lower()
    result = {
        "network": agent.get("network"),
        "token_id": agent.get("token_id"),
        "owner_account": agent.get("owner_account"),
        "status": agent.get("status"),
        "registered_at": agent.get("registered_at"),
        "agent_uri": agent.get("agent_uri"),
        "expected_hash": expected,
        "card_hash": None,
        "card_reachable": False,
        "hash_match": False,
        "card_name": None,
        "error": None,
        "checked_at": now(),
    }
    uri = agent.get("agent_uri")
    if not uri:
        result["error"] = "geen agent_uri"
        return result
    try:
        response = requests.get(uri, headers=HEADERS, timeout=TIMEOUT)
        if response.status_code != 200:
            result["error"] = f"HTTP {response.status_code}"
            return result
        body = response.content  # exact de bytes die de server teruggeeft
        result["card_reachable"] = True
        result["card_hash"] = hashlib.sha256(body).hexdigest()
        result["hash_match"] = result["card_hash"] == expected
        try:
            result["card_name"] = json.loads(body).get("name")
        except (ValueError, AttributeError):
            result["error"] = "kaart is geen geldige JSON"
    except requests.RequestException as exc:
        result["error"] = f"{type(exc).__name__}"
    return result


def main():
    # Controleer eerst of de indexer bij is, zodat een achterstand niet als "agent weg" telt.
    try:
        status = get_json("/status")
        net = status.get("networks", {}).get(NETWORK, {})
        print(f"Indexer {NETWORK}: achterstand {net.get('lag_blocks')} blokken, "
              f"laatst gesynchroniseerd {net.get('last_synced_at')}")
        if net.get("lag_blocks") not in (0, None):
            print("Let op: de indexer loopt achter. Resultaten met voorzichtigheid lezen.")
    except requests.RequestException as exc:
        print(f"Kon /status niet lezen ({type(exc).__name__}), ga toch door.")

    try:
        data = get_json("/agents", {"network": NETWORK, "limit": LIMIT})
    except requests.RequestException as exc:
        print(f"Kon de lijst niet ophalen: {exc}")
        sys.exit(1)

    agents = data.get("agents", [])
    print(f"Register {NETWORK}: {data.get('total')} agents, nu {len(agents)} gecontroleerd.")

    results = []
    for agent in agents:
        results.append(check_agent(agent))
        time.sleep(PAUSE_SECONDS)

    uri_counts = Counter(r["agent_uri"] for r in results if r["agent_uri"])
    for r in results:
        r["uri_shared_with_others"] = uri_counts.get(r["agent_uri"], 0) > 1

    summary = {
        "network": NETWORK,
        "total_in_register": data.get("total"),
        "checked": len(results),
        "active": sum(1 for r in results if r["status"] == "Active"),
        "card_reachable": sum(1 for r in results if r["card_reachable"]),
        "hash_match": sum(1 for r in results if r["hash_match"]),
        "shared_uri": sum(1 for r in results if r["uri_shared_with_others"]),
        "collected_at": now(),
    }
    with open("results.json", "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "agents": results}, handle, indent=2, ensure_ascii=False)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
