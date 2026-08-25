"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """TODO: ghi 1 dòng {ts, iso, step, name, ...} vào LOG."""
    now = time.time()
    event = {"ts": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
             "step": n, "name": name, **kw}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def confirm(auto: bool, msg: str) -> bool:
    """TODO: auto=True -> True; ngược lại hỏi y/N. Đừng bỏ hàm này đi."""
    if auto:
        return True
    return input(f"{msg} [y/N] ").strip().lower() in {"y", "yes"}


def _latest_outage(primary: str) -> dict | None:
    """Return the most recent recorded outage for the affected region, if any."""
    path = pathlib.Path("chaos/chaos-events.jsonl")
    if not path.exists():
        return None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("action") == "kill" and event.get("region") == primary:
            return event
    return None


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """TODO: 7 bước ở trên."""
    if primary == target or primary not in URL or target not in URL:
        raise ValueError("primary and target must be distinct regions a/b")
    from dr.health_checker import probe
    probes = []
    for _ in range(3):
        ready, reason = probe(primary, 1.0)
        probes.append({"ready": ready, "reason": reason})
        if ready:
            break
        time.sleep(0.5)
    # Region B deliberately starts *not ready* (empty state / warm pool), so
    # confirmation needs its liveness endpoint, not readiness.
    try:
        target_alive = httpx.get(f"{URL[target]}/healthz", timeout=1.0).status_code == 200
        target_reason = "alive" if target_alive else "not_alive"
    except Exception as exc:
        target_alive, target_reason = False, type(exc).__name__
    confirmed = len(probes) == 3 and not any(p["ready"] for p in probes) and target_alive
    step(1, "xac_nhan_outage", primary=primary, target=target, probes=probes,
         target_alive=target_alive, target_reason=target_reason, ok=confirmed)
    if not confirmed:
        return {"ok": False, "reason": "outage_not_confirmed"}
    outage = _latest_outage(primary)
    announced_at = time.time()
    step(2, "thong_bao_incident", primary=primary, target=target, ok=True,
         outage_ts=None if outage is None else outage.get("ts"),
         notification_delay_s=None if outage is None else round(announced_at - outage["ts"], 2))
    if not confirm(auto, f"Fail over from region {primary} to {target}?"):
        step(3, "scale_gpu_pool", ok=False, reason="operator_declined")
        return {"ok": False, "reason": "operator_declined"}
    result = fo.failover(target, backend, wait=60)
    step(3, "scale_gpu_pool", ok=result.get("ok", False), failover_called_once=True, result=result)
    if not result.get("ok"):
        return result
    target_state = result.get("target_state", {})
    step(4, "verify_state_replica", target=target, count=target_state.get("count"),
         weights=target_state.get("weights"), embed_model_version=result["restored"].get("embed_model_version"))
    step(5, "dns_cutover", target=target, ok=result.get("cutover", False))
    latencies, failures = [], 0
    for _ in range(10):
        started = time.time()
        try:
            response = httpx.get(f"{URL[target]}/v1/infer", timeout=3.0)
            failures += response.status_code != 200
        except Exception:
            failures += 1
        latencies.append((time.time() - started) * 1000)
    p95 = sorted(latencies)[max(0, int(len(latencies) * .95) - 1)]
    step(6, "verify_golden_signals", requests=10, errors=failures, error_rate=failures / 10,
         p95_latency_ms=round(p95, 1))
    step(7, "post_incident", ok=failures == 0,
         measure_command="python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300")
    return {"ok": failures == 0, "failover": result, "p95_latency_ms": round(p95, 1),
            "error_rate": failures / 10}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
