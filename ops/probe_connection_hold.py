#!/usr/bin/env python3
"""Measure how long a quiet connection to the render service survives.

A render call carries nothing on the connection for minutes at a time while the service
works on the answer. Something between the client and the service drops a connection that
quiet and tells neither end, so the answer never arrives and the client waits for it until
the job is stopped. One render waited 1 hour 56 minutes for an answer the service had
already sent.

This asks the service's /connection_hold endpoint to wait a given number of seconds before
answering, and reports whether the answer came back. Running it for a range of delays finds
the point where answers stop arriving.

Each delay is tried twice: once on a connection with keepalive, once without. The pair is
the point. If only the plain connection loses its answer, the cause is the quiet connection
being dropped and keepalive is the cure.

Run it on a CI runner, not a laptop. The equipment under suspicion is the runner's own
network, and a laptop goes through different equipment.

Usage:
    CODEPLAIN_API_KEY=... ops/probe_connection_hold.py --api https://api.test.codeplain.ai
    ... --delays 60,180,240,300,420,540 --repeats 3
"""

import argparse
import json
import os
import socket
import sys
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection

# A read timeout, so one lost answer cannot hold the probe open the way it holds a render
# open. It has to clear the longest delay asked for, so it is worked out from the delays.
TIMEOUT_MARGIN_SECONDS = 120


def keepalive_socket_options(idle, interval, probes):
    options = list(HTTPConnection.default_socket_options)
    options.append((socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1))
    for idle_option in ("TCP_KEEPIDLE", "TCP_KEEPALIVE"):
        if hasattr(socket, idle_option):
            options.append((socket.IPPROTO_TCP, getattr(socket, idle_option), idle))
            break
    if hasattr(socket, "TCP_KEEPINTVL"):
        options.append((socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval))
    if hasattr(socket, "TCP_KEEPCNT"):
        options.append((socket.IPPROTO_TCP, socket.TCP_KEEPCNT, probes))
    return options


class KeepaliveAdapter(HTTPAdapter):
    def __init__(self, idle, interval, probes, **kwargs):
        self._options = keepalive_socket_options(idle, interval, probes)
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["socket_options"] = self._options
        return super().init_poolmanager(*args, **kwargs)


def make_session(keepalive, idle, interval, probes):
    session = requests.Session()
    if keepalive:
        session.mount("https://", KeepaliveAdapter(idle, interval, probes))
        session.mount("http://", KeepaliveAdapter(idle, interval, probes))
    return session


def one_attempt(api_url, api_key, delay, keepalive, timeout, idle, interval, probes):
    """Ask for an answer after `delay` seconds. Report what happened and how long it took."""
    session = make_session(keepalive, idle, interval, probes)
    started = time.time()
    try:
        response = session.post(
            f"{api_url}/connection_hold",
            headers={"Content-Type": "application/json"},
            json={"api_key": api_key, "seconds": delay},
            timeout=timeout,
        )
        elapsed = time.time() - started
        if response.status_code != 200:
            return {"outcome": "http_error", "status": response.status_code, "elapsed": elapsed}
        return {"outcome": "answered", "status": 200, "elapsed": elapsed}
    except requests.exceptions.ReadTimeout:
        return {"outcome": "no_answer", "status": None, "elapsed": time.time() - started}
    except requests.exceptions.RequestException as e:
        return {"outcome": type(e).__name__, "status": None, "elapsed": time.time() - started}
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="https://api.test.codeplain.ai", help="Base URL of the render service")
    parser.add_argument("--delays", default="60,180,240,300,420,540", help="Seconds to wait, comma separated")
    parser.add_argument("--repeats", type=int, default=2, help="How many times to try each delay, per connection kind")
    parser.add_argument("--keepalive-idle", type=int, default=60, help="Seconds of quiet before the first probe")
    parser.add_argument("--keepalive-interval", type=int, default=30, help="Seconds between probes")
    parser.add_argument("--keepalive-probes", type=int, default=4, help="Probes before the connection is given up")
    parser.add_argument("--json-out", help="Write every attempt to this file as JSON")
    args = parser.parse_args()

    api_key = os.environ.get("CODEPLAIN_API_KEY")
    if not api_key:
        print("Error: CODEPLAIN_API_KEY is required.", file=sys.stderr)
        return 69

    delays = [float(d) for d in args.delays.split(",") if d.strip()]
    timeout = max(delays) + TIMEOUT_MARGIN_SECONDS

    print(f"Service: {args.api}")
    print(f"Delays: {delays}")
    print(f"Repeats per delay, per connection kind: {args.repeats}")
    print(f"Read timeout: {timeout:.0f}s. An attempt that reaches it is counted as no answer.\n")
    print(f"{'delay':>7} {'connection':>12} {'try':>4} {'outcome':>14} {'elapsed':>9}")
    print("-" * 52)

    results = []
    for delay in delays:
        for keepalive in (True, False):
            for attempt in range(1, args.repeats + 1):
                r = one_attempt(
                    args.api,
                    api_key,
                    delay,
                    keepalive,
                    timeout,
                    args.keepalive_idle,
                    args.keepalive_interval,
                    args.keepalive_probes,
                )
                kind = "keepalive" if keepalive else "plain"
                shown = r["outcome"] if r["status"] in (None, 200) else f"{r['outcome']} {r['status']}"
                print(f"{delay:>7.0f} {kind:>12} {attempt:>4} {shown:>14} {r['elapsed']:>8.1f}s")
                r.update({"delay": delay, "keepalive": keepalive, "attempt": attempt})
                results.append(r)

    print("\nSummary: answers that came back, out of attempts")
    print(f"{'delay':>7} {'keepalive':>12} {'plain':>12}")
    print("-" * 33)
    for delay in delays:
        counts = {}
        for keepalive in (True, False):
            group = [r for r in results if r["delay"] == delay and r["keepalive"] is keepalive]
            counts[keepalive] = (sum(1 for r in group if r["outcome"] == "answered"), len(group))
        print(
            f"{delay:>7.0f} {f'{counts[True][0]}/{counts[True][1]}':>12} {f'{counts[False][0]}/{counts[False][1]}':>12}"
        )

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, sort_keys=True)
        print(f"\nWrote every attempt to {args.json_out}")

    # The probe reports; it does not judge. A lost answer is the finding, not a failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())
