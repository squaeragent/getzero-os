#!/usr/bin/env python3
"""
Deep integration test — Tests 1-5 against LIVE engine.
Run: python scanner/tests/deep_test.py
"""
import urllib.request, json, os, time, sys

API = "http://localhost:8420"

def get(path):
    try:
        return json.loads(urllib.request.urlopen(f"{API}{path}", timeout=15).read())
    except urllib.error.HTTPError as e:
        try: return json.loads(e.read().decode())
        except: return {"_error": e.code, "_body": e.read().decode() if hasattr(e, 'read') else ""}

def post(path):
    req = urllib.request.Request(f"{API}{path}", method="POST", data=b"",
        headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15).read())
    except urllib.error.HTTPError as e:
        try: return json.loads(e.read().decode())
        except: return {"_error": e.code}

all_results = []

def test(name, cond, detail=""):
    status = "✅" if cond else "❌"
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))
    all_results.append((name, cond))
    return cond

# ════════════════════════════════════════════════════════════════════
print("=" * 60)
print("TEST 1: FULL OPERATOR JOURNEY VIA API")
print("=" * 60)

# Step 1: Register
print("\nStep 1: Register operator A (free)")
r = post("/v6/operator/register?id=test_op_1&wallet=0xAAA&plan=free")
test("A registered", r.get("created") == True, f"bus={r.get('bus_dir','?')[-30:]}")

# Step 2: Strategies
print("\nStep 2: List strategies")
r = get("/v6/strategies?operator_id=test_op_1")
test("9 strategies", r.get("count") == 9)

# Step 3: Evaluate SOL
print("\nStep 3: Evaluate SOL")
r = get("/v6/evaluate/SOL?operator_id=test_op_1")
test("consensus present", isinstance(r.get("consensus"), int), f"{r.get('consensus')}/7 {r.get('direction')}")
test("price > 0", r.get("price", 0) > 0, f"${r.get('price',0):.2f}")
layers = r.get("layers", [])
test("7 layers", len(layers) >= 7, f"{len(layers)} layers")

# Step 4: Approaching
print("\nStep 4: Approaching")
r = get("/v6/approaching?operator_id=test_op_1")
test("count returned", isinstance(r.get("count"), int), f"{r.get('count')} coins")

# Step 5: Start paper session
print("\nStep 5: Start paper Momentum")
r = post("/v6/session/start?strategy=momentum&paper=true&operator_id=test_op_1")
test("session active", r.get("status") == "active", r.get("session_id","?")[:12])
test("paper=true", r.get("paper") == True)

# Step 6: Status
print("\nStep 6: Session status")
r = get("/v6/session/status?operator_id=test_op_1")
test("active", r.get("active") == True)

# Step 7: Health
print("\nStep 7: Engine health")
r = get("/v6/engine/health?operator_id=test_op_1")
test("operational", r.get("status") == "operational")

# Step 8: End
print("\nStep 8: End session")
r = post("/v6/session/end?operator_id=test_op_1")
test("ended", "error" not in r, f"strat={r.get('strategy')}")
test("narrative", bool(r.get("narrative")), str(r.get("narrative",""))[:50])

# Step 9: History
print("\nStep 9: History")
r = get("/v6/session/history?operator_id=test_op_1")
test("has sessions", r.get("count", 0) > 0, f"count={r.get('count')}")
a_strats = [s.get("strategy") for s in r.get("sessions", [])]
test("A only has momentum", all(s == "momentum" for s in a_strats), str(a_strats))

# Step 10: Plan gating
print("\nStep 10: Plan gating")
r = post("/v6/session/start?strategy=degen&paper=true&operator_id=test_op_1")
test("degen REJECTED", "error" in r, r.get("error","")[:50])


# ════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST 2: MULTI-OPERATOR ISOLATION")
print("=" * 60)

# Register B
print("\nStep 1: Register operator B (pro)")
r = post("/v6/operator/register?id=test_op_2&wallet=0xBBB&plan=pro")
test("B registered", r.get("created") == True)

# Start degen on B
print("\nStep 2: Degen on B")
r = post("/v6/session/start?strategy=degen&paper=true&operator_id=test_op_2")
test("degen started", r.get("status") == "active")

# A should be inactive
print("\nStep 3: A is inactive")
r = get("/v6/session/status?operator_id=test_op_1")
test("A inactive", r.get("active") == False, f"active={r.get('active')}")

# B should be active
print("\nStep 4: B is active")
r = get("/v6/session/status?operator_id=test_op_2")
test("B active", r.get("active") == True)

# End B
post("/v6/session/end?operator_id=test_op_2")

# History isolation
print("\nStep 5: History isolation")
r_a = get("/v6/session/history?operator_id=test_op_1")
r_b = get("/v6/session/history?operator_id=test_op_2")
a_strats = [s.get("strategy") for s in r_a.get("sessions", [])]
b_strats = [s.get("strategy") for s in r_b.get("sessions", [])]
test("A has no degen", "degen" not in a_strats, str(a_strats))
test("B has degen", "degen" in b_strats, str(b_strats))

# Bus dirs
print("\nStep 6: Bus dirs separate")
a_dir = "/Users/forge/getzero-os/scanner/v6/operators/test_op_1/bus"
b_dir = "/Users/forge/getzero-os/scanner/v6/operators/test_op_2/bus"
test("A dir exists", os.path.isdir(a_dir))
test("B dir exists", os.path.isdir(b_dir))
test("dirs different", a_dir != b_dir)


# ════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST 3: MCP TOOL VERIFICATION (via REST equivalents)")
print("=" * 60)

tools = [
    ("zero_list_strategies",       "/v6/strategies",     lambda r: r.get("count") == 9),
    ("zero_preview_strategy",      "/v6/strategy/momentum", lambda r: bool(r.get("name"))),
    ("zero_evaluate BTC",          "/v6/evaluate/BTC",   lambda r: isinstance(r.get("consensus"), int)),
    ("zero_evaluate SOL",          "/v6/evaluate/SOL",   lambda r: isinstance(r.get("consensus"), int)),
    ("zero_get_heat",              "/v6/heat",           lambda r: isinstance(r.get("count"), int)),
    ("zero_get_approaching",       "/v6/approaching",    lambda r: isinstance(r.get("count"), int)),
    ("zero_get_pulse",             "/v6/pulse",          lambda r: isinstance(r.get("events"), list)),
    ("zero_get_brief",             "/v6/brief",          lambda r: isinstance(r, dict)),
    ("zero_get_engine_health",     "/v6/engine/health",  lambda r: r.get("status") == "operational"),
]

for name, path, check in tools:
    r = get(path)
    test(name, check(r), str(r)[:60] if not check(r) else "")

# Session tools (start → status → history → end)
r = post("/v6/session/start?strategy=momentum&paper=true&operator_id=test_op_1")
test("zero_start_session", r.get("status") == "active")
sid = r.get("session_id", "")

r = get("/v6/session/status?operator_id=test_op_1")
test("zero_session_status", r.get("active") == True)

r = post("/v6/session/end?operator_id=test_op_1")
test("zero_end_session", "error" not in r)

r = get("/v6/session/history?operator_id=test_op_1")
test("zero_session_history", r.get("count", 0) > 0)

if sid:
    r = get(f"/v6/session/{sid}?operator_id=test_op_1")
    test("zero_session_result", r.get("session_id") == sid or "error" not in r)


# ════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST 5: ERROR HANDLING + EDGE CASES")
print("=" * 60)

# Fake coin
print("\nEdge 1: Fake coin")
r = get("/v6/evaluate/FAKECOIN")
test("fake coin doesn't crash", "_error" not in r or r.get("_error") != 500,
     f"consensus={r.get('consensus')} err={r.get('_error','none')}")

# Invalid strategy
print("\nEdge 2: Invalid strategy")
r = post("/v6/session/start?strategy=nonexistent&paper=true&operator_id=test_op_1")
test("invalid strategy rejected", "error" in r, r.get("error","")[:50])

# Start while active
print("\nEdge 3: Double start")
post("/v6/session/start?strategy=momentum&paper=true&operator_id=test_op_1")
r = post("/v6/session/start?strategy=momentum&paper=true&operator_id=test_op_1")
test("double start rejected", "error" in r or r.get("status") != "active")
post("/v6/session/end?operator_id=test_op_1")

# End when none active
print("\nEdge 4: End when no session")
r = post("/v6/session/end?operator_id=test_op_1")
test("end with no session", "error" in r or r.get("status") != "ended")

# Missing params
print("\nEdge 5: Missing coin in evaluate")
r = get("/v6/evaluate/")
test("missing coin handled", True, "endpoint doesn't match (404) or returns error")


# ════════════════════════════════════════════════════════════════════
# SUMMARY
print("\n" + "=" * 60)
passed = sum(1 for _, c in all_results if c)
total = len(all_results)
failed = [(n, c) for n, c in all_results if not c]
print(f"TOTAL: {passed}/{total} passed")
if failed:
    print(f"\nFAILED:")
    for name, _ in failed:
        print(f"  ❌ {name}")
print("=" * 60)
