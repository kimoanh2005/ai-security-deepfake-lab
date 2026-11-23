"""Smoke test 2 Lab qua API thật. Chạy khi backend đang chạy:
    python smoke_labs.py --email admin@lab.local --password <mat_khau> [--base http://127.0.0.1:8000]
Chỉ in trạng thái/số liệu, không in token.
"""
import argparse, json, sys, time
import httpx

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8000")
ap.add_argument("--email", required=True)
ap.add_argument("--password", required=True)
a = ap.parse_args()
c = httpx.Client(base_url=a.base + "/api/v1", timeout=30)

r = c.post("/auth/login", json={"email": a.email, "password": a.password})
if r.status_code != 200:
    sys.exit(f"Đăng nhập lỗi {r.status_code}: {r.text[:200]}")
c.headers["Authorization"] = "Bearer " + r.json()["access_token"]

h = c.get("/health").json()
print("health:", json.dumps(h, ensure_ascii=False))


def run(lab, body, wait=300):
    r = c.post(f"/labs/{lab}/experiments", json=body, headers={"Idempotency-Key": f"smoke-{lab}-{time.time_ns()}"})
    if r.status_code != 202:
        print(f"[{lab}] không tạo được: {r.status_code} {r.text[:300]}")
        return False
    exp_id, end = r.json()["id"], time.time() + wait
    while time.time() < end:
        d = c.get(f"/experiments/{exp_id}").json()
        if d["state"] in ("done", "failed", "cancelled"):
            break
        time.sleep(2)
    print(f"\n[{lab}] {exp_id}: state={d['state']} result_kind={d.get('result_kind')}")
    if d["state"] == "done":
        print(json.dumps(d["result"]["metrics"], ensure_ascii=False, indent=2))
        return True
    print("lỗi:", d.get("error"))
    return False


ok_api = run("api_lab", {"scenario_code": "burst-30"})
ok_pi = run("prompt_injection", {"case_codes": ["PI-01", "PI-05", "PI-06", "PI-07", "PI-08", "PI-09", "PI-10", "PI-11", "PI-12", "PI-13", "PI-14"], "repeat": 1})
print("\nKẾT QUẢ: api_lab", "OK" if ok_api else "LỖI", "| prompt_injection", "OK" if ok_pi else "LỖI")
sys.exit(0 if ok_api and ok_pi else 1)
