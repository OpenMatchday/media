#!/usr/bin/env python3
"""OM-004 spike: one-way latency and loss for RTP-shaped UDP (RFC 3550 header layout).

Packet layout (12-byte RTP-like header + payload):
  V=2,P=0,X=0,CC=0 | M=0,PT=96 | sequence (u16 BE) | timestamp (u32 BE) | SSRC (u32 BE)
  payload: send_time_ns (u64 BE) + padding to --payload-bytes

One-way latency uses a shared host clock (sender and receiver on the same machine).
This is transport-only; it is not glass-to-glass camera-to-display latency.
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import statistics
import sys
import threading
import time
from pathlib import Path

RTP_HDR = struct.Struct("!BBHII")
SEND_NS = struct.Struct("!Q")
SSRC = 0x4F4D4433  # 'OMD3'


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def build_packet(seq: int, send_ns: int, payload_bytes: int) -> bytes:
    header = RTP_HDR.pack(0x80, 96, seq & 0xFFFF, (send_ns // 1_000_000) & 0xFFFFFFFF, SSRC)
    body = SEND_NS.pack(send_ns)
    pad_len = max(0, payload_bytes - len(header) - len(body))
    return header + body + (b"\x00" * pad_len)


def parse_packet(data: bytes) -> tuple[int, int] | None:
    if len(data) < RTP_HDR.size + SEND_NS.size:
        return None
    _b0, _pt, seq, _ts, ssrc = RTP_HDR.unpack_from(data, 0)
    if ssrc != SSRC:
        return None
    (send_ns,) = SEND_NS.unpack_from(data, RTP_HDR.size)
    return seq, send_ns


def run_receiver(
    bind_host: str,
    port: int,
    stop_at: float,
    results: dict,
) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind_host, port))
    sock.settimeout(0.2)
    latencies_ms: list[float] = []
    seqs: list[int] = []
    bytes_rx = 0
    try:
        while time.time() < stop_at:
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            recv_ns = time.time_ns()
            parsed = parse_packet(data)
            if parsed is None:
                continue
            seq, send_ns = parsed
            latencies_ms.append((recv_ns - send_ns) / 1_000_000.0)
            seqs.append(seq)
            bytes_rx += len(data)
    finally:
        sock.close()
    results["latencies_ms"] = latencies_ms
    results["seqs"] = seqs
    results["bytes_rx"] = bytes_rx


def run_sender(
    dest_host: str,
    port: int,
    count: int,
    rate_hz: float,
    payload_bytes: int,
    start_at: float,
) -> dict:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / rate_hz
    sent = 0
    try:
        while time.time() < start_at:
            time.sleep(0.001)
        next_t = time.perf_counter()
        for seq in range(count):
            send_ns = time.time_ns()
            sock.sendto(build_packet(seq, send_ns, payload_bytes), (dest_host, port))
            sent += 1
            next_t += interval
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
    finally:
        sock.close()
    return {"sent": sent}


def summarize(latencies_ms: list[float], seqs: list[int], sent: int) -> dict:
    ordered = sorted(latencies_ms)
    lost = max(0, sent - len(seqs))
    if seqs:
        expected = max(seqs) - min(seqs) + 1
        gaps = expected - len(set(seqs))
    else:
        gaps = sent
    return {
        "packets_sent": sent,
        "packets_received": len(seqs),
        "packets_lost_estimate": lost,
        "loss_ratio": (lost / sent) if sent else 1.0,
        "seq_gap_estimate": gaps,
        "latency_ms_min": min(ordered) if ordered else None,
        "latency_ms_mean": statistics.fmean(ordered) if ordered else None,
        "latency_ms_p50": percentile(ordered, 50) if ordered else None,
        "latency_ms_p95": percentile(ordered, 95) if ordered else None,
        "latency_ms_p99": percentile(ordered, 99) if ordered else None,
        "latency_ms_max": max(ordered) if ordered else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="OM-004 UDP/RTP-shaped latency/loss probe")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--dest", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5004)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--rate-hz", type=float, default=200.0)
    parser.add_argument("--payload-bytes", type=int, default=1200)
    parser.add_argument("--label", default="udp-rtp")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()

    if args.self_check:
        args.count = 200
        args.rate_hz = 400.0
        args.bind = "127.0.0.1"
        args.dest = "127.0.0.1"
        args.port = 51004
        args.label = "udp-rtp-self-check"

    recv_results: dict = {}
    start_at = time.time() + 0.3
    stop_at = start_at + (args.count / args.rate_hz) + 1.5
    receiver = threading.Thread(
        target=run_receiver,
        args=(args.bind, args.port, stop_at, recv_results),
        daemon=True,
    )
    receiver.start()
    send_info = run_sender(
        args.dest,
        args.port,
        args.count,
        args.rate_hz,
        args.payload_bytes,
        start_at,
    )
    receiver.join(timeout=stop_at - time.time() + 2.0)

    summary = summarize(
        recv_results.get("latencies_ms", []),
        recv_results.get("seqs", []),
        send_info["sent"],
    )
    report = {
        "probe": "udp-rtp-shaped",
        "label": args.label,
        "bind": args.bind,
        "dest": args.dest,
        "port": args.port,
        "rate_hz": args.rate_hz,
        "payload_bytes": args.payload_bytes,
        "rfc": "RFC 3550 header layout (PT=96 dynamic; not a registered clock rate)",
        "metric": "one-way latency via shared host clock; not glass-to-glass",
        "summary": summary,
        "host_time_unix": time.time(),
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="ascii")

    if args.self_check:
        assert summary["packets_sent"] == args.count
        assert summary["packets_received"] >= int(args.count * 0.99)
        assert summary["latency_ms_p95"] is not None
        assert summary["latency_ms_p95"] < 50.0
        print("SELF_CHECK_PASS", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
