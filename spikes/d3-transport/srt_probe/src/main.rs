//! OM-004 spike: one-way latency and loss over SRT using srt-tokio 0.4.4.
//!
//! Production intent remains Haivision libsrt (MPL-2.0); this probe uses the
//! Apache-2.0 pure-Rust srt-tokio stack only because the Windows libsrt
//! installer required elevation on this host. Numbers are transport-only,
//! shared-clock, not glass-to-glass.

use bytes::Bytes;
use futures::prelude::*;
use serde::Serialize;
use srt_tokio::SrtSocket;
use std::env;
use std::fs;
use std::net::{IpAddr, SocketAddr};
use std::path::PathBuf;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::time::sleep;

#[derive(Serialize)]
struct Summary {
    packets_sent: usize,
    packets_received: usize,
    packets_lost_estimate: usize,
    loss_ratio: f64,
    latency_ms_min: Option<f64>,
    latency_ms_mean: Option<f64>,
    latency_ms_p50: Option<f64>,
    latency_ms_p95: Option<f64>,
    latency_ms_p99: Option<f64>,
    latency_ms_max: Option<f64>,
}

#[derive(Serialize)]
struct Report {
    probe: &'static str,
    label: String,
    listen_host: String,
    listen_port: u16,
    call_host: String,
    rate_hz: f64,
    payload_bytes: usize,
    configured_latency_ms: u64,
    impl_crate: &'static str,
    impl_note: &'static str,
    metric: &'static str,
    summary: Summary,
    host_time_unix: f64,
}

fn percentile(sorted: &[f64], p: f64) -> Option<f64> {
    if sorted.is_empty() {
        return None;
    }
    if sorted.len() == 1 {
        return Some(sorted[0]);
    }
    let k = (sorted.len() - 1) as f64 * (p / 100.0);
    let f = k.floor() as usize;
    let c = (f + 1).min(sorted.len() - 1);
    if f == c {
        Some(sorted[f])
    } else {
        Some(sorted[f] + (sorted[c] - sorted[f]) * (k - f as f64))
    }
}

fn summarize(sent: usize, latencies_ms: &mut [f64]) -> Summary {
    latencies_ms.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let received = latencies_ms.len();
    let lost = sent.saturating_sub(received);
    let mean = if received == 0 {
        None
    } else {
        Some(latencies_ms.iter().sum::<f64>() / received as f64)
    };
    Summary {
        packets_sent: sent,
        packets_received: received,
        packets_lost_estimate: lost,
        loss_ratio: if sent == 0 {
            1.0
        } else {
            lost as f64 / sent as f64
        },
        latency_ms_min: latencies_ms.first().copied(),
        latency_ms_mean: mean,
        latency_ms_p50: percentile(latencies_ms, 50.0),
        latency_ms_p95: percentile(latencies_ms, 95.0),
        latency_ms_p99: percentile(latencies_ms, 99.0),
        latency_ms_max: latencies_ms.last().copied(),
    }
}

fn pack(seq: u32, send_ns: u64, payload_bytes: usize) -> Bytes {
    let mut buf = Vec::with_capacity(payload_bytes.max(12));
    buf.extend_from_slice(&seq.to_be_bytes());
    buf.extend_from_slice(&send_ns.to_be_bytes());
    if buf.len() < payload_bytes {
        buf.resize(payload_bytes, 0);
    }
    Bytes::from(buf)
}

fn unpack(data: &[u8]) -> Option<(u32, u64)> {
    if data.len() < 12 {
        return None;
    }
    let seq = u32::from_be_bytes(data[0..4].try_into().ok()?);
    let send_ns = u64::from_be_bytes(data[4..12].try_into().ok()?);
    Some((seq, send_ns))
}

fn now_ns() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock")
        .as_nanos() as u64
}

#[tokio::main]
async fn main() {
    let mut label = "srt".to_string();
    let mut listen_host = "127.0.0.1".to_string();
    let mut call_host = "127.0.0.1".to_string();
    let mut port: u16 = 9200;
    let mut count: usize = 1000;
    let mut rate_hz: f64 = 200.0;
    let mut payload_bytes: usize = 1200;
    let mut latency_ms: u64 = 120;
    let mut out: Option<PathBuf> = None;
    let mut self_check = false;

    let args: Vec<String> = env::args().skip(1).collect();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--label" => {
                label = args[i + 1].clone();
                i += 2;
            }
            "--listen-host" => {
                listen_host = args[i + 1].clone();
                i += 2;
            }
            "--call-host" => {
                call_host = args[i + 1].clone();
                i += 2;
            }
            "--port" => {
                port = args[i + 1].parse().expect("port");
                i += 2;
            }
            "--count" => {
                count = args[i + 1].parse().expect("count");
                i += 2;
            }
            "--rate-hz" => {
                rate_hz = args[i + 1].parse().expect("rate");
                i += 2;
            }
            "--payload-bytes" => {
                payload_bytes = args[i + 1].parse().expect("payload");
                i += 2;
            }
            "--latency-ms" => {
                latency_ms = args[i + 1].parse().expect("latency-ms");
                i += 2;
            }
            "--out" => {
                out = Some(PathBuf::from(&args[i + 1]));
                i += 2;
            }
            "--self-check" => {
                self_check = true;
                i += 1;
            }
            other => panic!("unknown arg {other}"),
        }
    }

    if self_check {
        label = "srt-self-check".into();
        count = 200;
        rate_hz = 200.0;
        port = 9210;
        listen_host = "127.0.0.1".into();
        call_host = "127.0.0.1".into();
    }

    let call_ip: IpAddr = call_host.parse().expect("call host ip");
    let call_addr = SocketAddr::new(call_ip, port);
    let interval = Duration::from_secs_f64(1.0 / rate_hz);
    let listen_port = port;
    let bind_ip: IpAddr = listen_host.parse().expect("listen host ip");

    let latency = Duration::from_millis(latency_ms);
    let receiver = tokio::spawn(async move {
        let mut rx = SrtSocket::builder()
            .local_ip(bind_ip)
            .latency(latency)
            .listen_on(listen_port)
            .await
            .expect("srt listen");
        let mut latencies_ms = Vec::with_capacity(count);
        let deadline = Instant::now() + Duration::from_secs_f64((count as f64 / rate_hz) + 5.0);
        while Instant::now() < deadline && latencies_ms.len() < count {
            match tokio::time::timeout(Duration::from_millis(500), rx.try_next()).await {
                Ok(Ok(Some((_instant, data)))) => {
                    let recv_ns = now_ns();
                    if let Some((_seq, send_ns)) = unpack(&data) {
                        let ms = (recv_ns as i128 - send_ns as i128) as f64 / 1_000_000.0;
                        if ms >= 0.0 {
                            latencies_ms.push(ms);
                        }
                    }
                }
                Ok(Ok(None)) => break,
                Ok(Err(_)) | Err(_) => continue,
            }
        }
        latencies_ms
    });

    sleep(Duration::from_millis(400)).await;

    let mut tx = SrtSocket::builder()
        .latency(latency)
        .call(call_addr, None)
        .await
        .expect("srt call");

    let mut next = Instant::now();
    for seq in 0..count as u32 {
        let send_ns = now_ns();
        tx.send((Instant::now(), pack(seq, send_ns, payload_bytes)))
            .await
            .expect("srt send");
        next += interval;
        let now = Instant::now();
        if next > now {
            sleep(next - now).await;
        }
    }
    let _ = tx.close().await;

    let mut latencies_ms = receiver.await.expect("receiver join");
    let summary = summarize(count, &mut latencies_ms);
    let report = Report {
        probe: "srt",
        label,
        listen_host,
        listen_port: port,
        call_host,
        rate_hz,
        payload_bytes,
        configured_latency_ms: latency_ms,
        impl_crate: "srt-tokio 0.4.4 (Apache-2.0)",
        impl_note: "spike probe only; production path intends Haivision libsrt MPL-2.0",
        metric: "one-way latency via shared host clock; not glass-to-glass",
        summary,
        host_time_unix: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_secs_f64(),
    };

    let text = serde_json::to_string_pretty(&report).expect("json");
    println!("{text}");
    if let Some(path) = out {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).expect("mkdir");
        }
        fs::write(path, text + "\n").expect("write");
    }

    if self_check {
        assert_eq!(report.summary.packets_sent, count);
        assert!(report.summary.packets_received >= (count as f64 * 0.95) as usize);
        assert!(report.summary.latency_ms_p95.unwrap() < 200.0);
        eprintln!("SELF_CHECK_PASS");
    }
}
