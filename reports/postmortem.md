# Postmortem — DR Drill Lab 23

## Timeline

| Time | Event | Evidence |
|---|---|---|
| 05:45:09 | Region A was stopped; RTO clock started. | `chaos/chaos-events.jsonl:3` |
| 05:45:09 | First edge request failed. | `reports/drill-2-withdr.jsonl:25` |
| 05:45:31 | Health checker declared Region A UNHEALTHY after three consecutive failures. | `reports/health-events.jsonl:2` |
| 05:45:31 | Operator incident notification and automated runbook confirmation occurred. | `reports/runbook-run.jsonl:2` |
| 05:45:38 | Region B became ready and DNS cutover completed. | `reports/failover-events.jsonl:4` and `reports/failover-events.jsonl:5` |
| 05:45:41 | Runbook recorded a successful post-incident verification. | `reports/runbook-run.jsonl:7` |

## RTO/RPO and gap analysis

- RTO target: 300s; measured: 32.1s; gap: 267.9s under target.
- RPO target: 300s; measured: 12.02s and 6 documents lost; gap: 287.98s under target.
- Longest component: health-check detection (22.0s observed). The 5s interval and threshold of 3 impose a 15s detection floor; client timeout and polling alignment account for the additional observed delay.

## Root cause (5 whys)

1. Users received errors because Region A was intentionally stopped.
2. The edge still routed traffic to A until failover completed.
3. Region B began empty and warm, so it could not serve immediately.
4. The runbook restored the most recent filesystem snapshot, scaled the pool, waited for readiness, then cut over.
5. The remaining loss was bounded by the 30-second replication cadence, rather than being an unbounded state loss.

This is blameless: the drill exposed the expected recovery path and its time costs, not an operator error.

## Action items

| # | Action item | Owner | Deadline | Expected effect |
|---|---|---|---|---|
| 1 | Evaluate a 2s health-check interval with the same anti-flap threshold. | On-call / platform | Next drill | Reduce detection floor from 15s to 6s. |
| 2 | Test a continuously warm Region B pool. | Serving platform | Next sprint | Remove most of the 6.39s warm-up. |
| 3 | Reduce replication cadence after storage-cost review. | Data platform | Next sprint | Reduce RPO and documents lost. |

## Required reflection

1. `interval × threshold = 5 × 3 = 15s`; it is 46.7% of the 32.1s measured RTO.
2. At 1s with threshold 3, the floor becomes 3s: roughly 12s less. The cost is more probes and a higher flapping/false-positive risk, so the threshold and timeout need revalidation.
3. For a six-hour permanent primary loss, the 6 lost documents represent writes accepted after the last replicated snapshot. Their business impact depends on the customer workflow, so those writes must be reconciled or replayed from an upstream source.
