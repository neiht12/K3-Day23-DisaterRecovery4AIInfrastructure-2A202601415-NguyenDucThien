# RTO/RPO Evidence — Lab 23

All values below are derived from the local drill logs, not estimated. The Windows drill used `--mode stop`: Region A was terminated locally and recovery was served by Region B.

## 1. Drill 1 — no disaster recovery

| Metric | Measured value | Evidence |
|---|---:|---|
| Outage time | 2026-08-25T05:42:17 | `chaos/chaos-events.jsonl:1` |
| First user-visible failure | +2.2s | `reports/measure-drill-1.json:24` |
| Failed requests | 14 | `reports/measure-drill-1.json:38` |
| RTO verdict | NO_RECOVERY | `reports/measure-drill-1.json:33` |

## 2. Drill 2 — health-check based recovery

| Milestone | Seconds from outage | Evidence |
|---|---:|---|
| Outage, Region A stopped | 0.0s | `chaos/chaos-events.jsonl:3` |
| First failed request | 0.0s | `reports/drill-2-withdr.jsonl:25` |
| Health checker marked A UNHEALTHY | 22.0s | `reports/health-events.jsonl:2` |
| Snapshot restored | 22.5s | `reports/failover-events.jsonl:2` |
| Region B ready | 28.9s | `reports/failover-events.jsonl:4` |
| DNS/LB cutover to B | 28.9s | `reports/failover-events.jsonl:5` |
| First successful request from B after failure | 32.1s | `reports/measure-drill-2.json:28` |

| Metric | Measured | Target | Verdict |
|---|---:|---:|---|
| RTO — inference API | 32.1s | 300s | PASS |
| RPO — vector DB | 12.02s / 6 documents lost | 300s | PASS |

The measurement tool confirms `valid: true`, no warnings, and `recovered_by_region: b`: `reports/measure-drill-2.json:2`.

## 3. RTO breakdown

| Component | Seconds | Source | Reduction option |
|---|---:|---|---|
| Health-check detection | 22.0s observed; 15.0s floor | `reports/health-events.jsonl:2` | Reduce interval only after evaluating false-positive/flapping risk. |
| Snapshot restore | 0.01s | `reports/failover-events.jsonl:2` to `reports/failover-events.jsonl:3` | Keep the replica local and test restore regularly. |
| GPU pool warm-up | 6.39s | `reports/failover-events.jsonl:4` | Keep a warm standby or pre-warm the pool. |
| DNS/LB TTL cache | 3.2s | `reports/measure-drill-2.json:23` and `reports/measure-drill-2.json:24` | Lower TTL with care for extra edge lookups. |

The small difference between the rounded components and 32.1s is request scheduling and rounded timestamps; the authoritative RTO is the load-generator value above.
