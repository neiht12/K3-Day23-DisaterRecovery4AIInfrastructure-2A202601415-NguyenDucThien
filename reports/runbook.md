# Runbook — Primary Region Down

Use PowerShell from the repository root. The on-call engineer owns execution; the incident commander owns the decision to cut over or roll back.

| # | Step | Command | Completion signal | Owner |
|---|---|---|---|---|
| 1 | Confirm outage | `python chaos/kill_region.py status` | Region A is not alive/ready; Region B is alive. Confirm three failed readiness probes. | On-call |
| 2 | Open incident and start clock | `python dr/health_checker.py --interval 5 --threshold 3 --duration 100 --out reports/health-events.jsonl` | `reports/health-events.jsonl` contains A `UNHEALTHY` with interval and threshold. | On-call |
| 3 | Confirm a current replica exists | `python state/snapshot.py lag --backend fs` | A finite `rpo_seconds` is returned. | Data platform |
| 4 | Execute one failover | `python dr/runbook.py --primary a --target b --backend fs --auto` | `reports/failover-events.jsonl` records steps 1 through 5 in order. | On-call |
| 5 | Verify DNS/LB cutover | `Invoke-RestMethod http://localhost:8080/edge/state` | `active_region` is `b`. | Serving platform |
| 6 | Verify golden signals | `1..10 | ForEach-Object { Invoke-RestMethod http://localhost:8080/v1/infer }` | Ten successful responses; runbook log has error rate 0. | On-call |
| 7 | Measure and record | `python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | `valid: true`, `rto_verdict: PASS`, and reports are updated. | Incident commander |

## Rollback

Do not automatically return traffic to Region A. The incident commander may authorize rollback only after A is restored, its data is reconciled with B, `/readyz` remains successful through the agreed observation window, and a new snapshot is available. Record the decision and timestamps in `reports/runbook-run.jsonl`.
