#!/usr/bin/env python3
"""Test whether the Q2 watermark API accepts a 6-unique-pair probe.

The boolean gadget in Q2 relies on sending a long token sequence that repeats a
6-token cycle. The sequence has enough tokens for the API length check, but only
six unique consecutive pairs:

    0->1, 1->2, 2->3, 3->4, 4->5, 5->0

The API currently requires at least 50 submitted tokens. With a 6-token cycle,
the shortest repeated probe satisfying that is 54 tokens.

Usage:
    # simplest: set API_KEY below, then run
    python test_6_pair_probe.py

    python test_6_pair_probe.py --api-key YOUR_KEY
    Q2_API_KEY=YOUR_KEY python test_6_pair_probe.py
    python test_6_pair_probe.py --key-file Q2_key.txt
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


API_URL = "https://isl-llms.ethz.ch/api"
VOCAB_SIZE = 50277
CACHE_PATH = Path(__file__).resolve().parent / "test_6_pair_probe_cache.jsonl"

# Optional: paste your API key here for quick local testing.
# Command-line, env var, and key-file values still work if this is left blank.
API_KEY = ""


def tokens_to_string(tokens: list[int]) -> str:
    return ",".join(str(t) for t in tokens)


def unique_pairs(tokens: list[int]) -> set[tuple[int, int]]:
    return {(tokens[i - 1], tokens[i]) for i in range(1, len(tokens))}


def repeated_cycle_probe(cycle: list[int], min_tokens: int = 50) -> list[int]:
    if len(set(cycle)) != len(cycle):
        raise ValueError("cycle tokens must be distinct")
    if not all(0 <= token < VOCAB_SIZE for token in cycle):
        raise ValueError(f"tokens must be in [0, {VOCAB_SIZE})")

    probe: list[int] = []
    while len(probe) < min_tokens:
        probe.extend(cycle)
    return probe


def get_api_key(args: argparse.Namespace) -> str:
    if API_KEY:
        return API_KEY.strip()

    if args.api_key:
        return args.api_key.strip()

    env_key = os.environ.get("Q2_API_KEY")
    if env_key:
        return env_key.strip()

    key_file_candidates = [
        Path(args.key_file),
        Path(__file__).resolve().parent / args.key_file,
    ]
    for key_file in key_file_candidates:
        if key_file.exists():
            return key_file.read_text(encoding="utf-8").strip()

    raise SystemExit(
        "No API key found. Pass --api-key, set Q2_API_KEY, or create Q2_key.txt."
    )


def call_bool_api(api_key: str, tokens: list[int]) -> dict:
    import requests

    response = requests.post(
        f"{API_URL}/watermark/get_bool",
        headers={"Content-Type": "application/json", "x-api-key": api_key},
        json={"tokens": tokens_to_string(tokens)},
        timeout=30,
    )
    print("HTTP status:", response.status_code)
    try:
        return response.json()
    except ValueError:
        return {"raw_text": response.text}


def load_cached_response(tokens_str: str) -> dict | None:
    if not CACHE_PATH.exists():
        return None

    with CACHE_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("endpoint") == "bool" and record.get("tokens") == tokens_str:
                return record.get("response")
    return None


def save_query(tokens_str: str, response: dict) -> None:
    tokens = [int(token) for token in tokens_str.split(",")]
    record = {
        "endpoint": "bool",
        "tokens": tokens_str,
        "token_count": len(tokens),
        "unique_pair_count": len(unique_pairs(tokens)),
        "response": response,
    }
    with CACHE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", help="Q2 API key. Prefer Q2_API_KEY env var.")
    parser.add_argument(
        "--key-file",
        default="Q2_key.txt",
        help="Fallback key file path. Default: Q2_key.txt",
    )
    parser.add_argument(
        "--cycle",
        default="0,1,2,3,4,5",
        help="Six distinct token ids to repeat. Default: 0,1,2,3,4,5",
    )
    parser.add_argument(
        "--min-tokens",
        type=int,
        default=50,
        help="Minimum submitted token count. Default: 50",
    )
    args = parser.parse_args()

    cycle = [int(part) for part in args.cycle.split(",")]
    probe = repeated_cycle_probe(cycle, min_tokens=args.min_tokens)
    pairs = unique_pairs(probe)

    print("tokens:", len(probe))
    print("unique consecutive pairs:", len(pairs))
    print("pairs:", sorted(pairs))

    tokens_str = tokens_to_string(probe)
    result = load_cached_response(tokens_str)
    if result is not None:
        print("loaded cached response from:", CACHE_PATH)
    else:
        api_key = get_api_key(args)
        print("submitting to /watermark/get_bool ...")
        result = call_bool_api(api_key, probe)
        save_query(tokens_str, result)
        print("saved query to:", CACHE_PATH)

    print("response:", result)

    if "wmark_response" in result:
        print("ACCEPTED: API returned wmark_response.")
    else:
        print("NOT ACCEPTED or unexpected response: no wmark_response field.")


if __name__ == "__main__":
    main()
