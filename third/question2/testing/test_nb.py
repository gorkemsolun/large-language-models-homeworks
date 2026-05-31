"""Execute the ACTUAL attack code embedded in Q2_code.ipynb against the local
watermark simulator (two independent keys, one per endpoint)."""
import json, sys, os
import numpy as np
from sim import WatermarkKey, red_total, z_score_binomial, make_watermarked_string
from scipy.stats import norm

# Default: test ../Q2_code.ipynb. Override with:  python3 test_nb.py <notebook.ipynb>
_HERE = os.path.dirname(os.path.abspath(__file__))
NB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_HERE, "..", "Q2_code.ipynb")
nb = json.load(open(NB))


def cell_src(prefix):
    for c in nb["cells"]:
        if c["cell_type"] == "code" and "".join(c["source"]).startswith(prefix):
            return "".join(c["source"])
    raise KeyError(prefix)


# ---- simulated API ---------------------------------------------------------
PKEY = WatermarkKey(seed=11111)
BKEY = WatermarkKey(seed=22222)
rng = np.random.default_rng(3)

# build 7 watermarked strings per endpoint (131 tokens, green-excess in the range
# where the score targets <=41 / <=42 are reachable, so the test demonstrates them).
def mk(key, gp):
    return make_watermarked_string(key, rng, length=131, green_prob=gp)

_pv_strs = [mk(PKEY, 0.34) for _ in range(7)]
_bl_strs = [mk(BKEY, 0.34) for _ in range(7)]

_TOKENS = ([{"tokens": ",".join(map(str, s.tolist())), "endpoint": "pvalue"} for s in _pv_strs] +
           [{"tokens": ",".join(map(str, s.tolist())), "endpoint": "bool"}   for s in _bl_strs])

API_CALLS = {"pvalue": 0, "bool": 0, "bootstrap_fail_guard": 0}


def tokens_to_array(s):  return np.array([int(i) for i in s.split(",")])
def tokens_to_string(a): return ",".join([str(int(i)) for i in a])

SERVER_MIN_TOKENS = 50            # real server: "at least 50 tokens for watermark detection"

def validate_tokens(s):
    assert isinstance(s, str)
    assert len(s) > 0
    parts = s.split(",")
    for p in parts:
        assert p.isdigit(), f"non-int entry: {p!r}"
    assert not (len(parts) < 40 or len(parts) > 1000), f"len {len(parts)} out of [40,1000]"

def get_tokens():  return _TOKENS

def _server_reject(tokens_str):
    """Mimic the real API's 400 rejections (no wmark_response): both the >=50 token
    gate and the >=50 distinct-consecutive-pair gate."""
    toks = [int(x) for x in tokens_str.split(",")]
    if len(toks) < SERVER_MIN_TOKENS:
        return {"detail": "You must provide at least 50 tokens for watermark detection."}
    if len({(toks[i - 1], toks[i]) for i in range(1, len(toks))}) < SERVER_MIN_TOKENS:
        return {"detail": "The passage is too repetitive. You must provide a sequence with at "
                          "least 50 distinct consecutive pairs of tokens."}
    return None

def get_pvalue(tokens_str):
    validate_tokens(tokens_str)
    rej = _server_reject(tokens_str)
    if rej is not None:
        return rej
    API_CALLS["pvalue"] += 1
    toks = tokens_to_array(tokens_str)
    r, t = red_total(toks, PKEY)
    z = z_score_binomial(r, t, 0.5)
    return {"submitted_tokens": tokens_str, "wmark_response": float(1 - norm.cdf(-z))}

def get_bool(tokens_str):
    validate_tokens(tokens_str)
    rej = _server_reject(tokens_str)
    if rej is not None:
        return rej
    API_CALLS["bool"] += 1
    toks = tokens_to_array(tokens_str)
    r, t = red_total(toks, BKEY)
    z = z_score_binomial(r, t, 0.5)
    p = float(1 - norm.cdf(-z))
    return {"submitted_tokens": tokens_str, "wmark_response": 1 if p < 0.01 else 0}


# monkeypatch time.sleep so the test is fast (no real rate-limit waits)
import time as _time
_time.sleep = lambda *a, **k: None

import os
for _f in ("Q2_query_cache.jsonl", "Q2_state.json", "Q2_known_pairs.jsonl", "Q2_run.log"):
    if os.path.exists(_f):
        os.remove(_f)                 # start cold (no cache, no checkpoint, no known pairs)

CELLS = ["# ============",            # ATTACK_HELPERS
         "# ---------------- Solve the p-value",
         "# ---------------- Precondition for the boolean",
         "# ---------------- Solve the boolean",
         "# Suffixes discovered",     # SET_SUFFIXES (range guard)
         "# Validate your suffixes against all 7 strings"]  # cached validation cell


def fresh_ns():
    ns = dict(np=np, requests=None, json=json,
              tokens_to_array=tokens_to_array, tokens_to_string=tokens_to_string,
              validate_tokens=validate_tokens, get_tokens=get_tokens,
              get_pvalue=get_pvalue, get_bool=get_bool)
    ns["tokens"] = get_tokens()
    ns["pvalue_token_ids"] = [t for t in ns["tokens"] if t["endpoint"] == "pvalue"]
    ns["bool_token_ids"]   = [t for t in ns["tokens"] if t["endpoint"] == "bool"]
    return ns


def run(ns, label):
    print(f"\n############### {label} ###############")
    for prefix in CELLS:
        exec(cell_src(prefix), ns)


# ---- COLD run (empty cache): records the real query cost -------------------
ns = fresh_ns()
run(ns, "COLD RUN (empty cache)")
cold_calls = dict(API_CALLS)

# ---- WARM run (fresh namespace, reloads cache from disk) -------------------
API_CALLS.update(pvalue=0, bool=0)    # reset the simulated-API counters
ns2 = fresh_ns()
run(ns2, "WARM RE-RUN (cache on disk)")
warm_new = ns2["_API_STATS"]
print(f"\ncold sim API calls: {cold_calls}")
print(f"warm run new queries via _call: new_pvalue={warm_new['new_pvalue']} "
      f"new_bool={warm_new['new_bool']} cache_hits={warm_new['cache_hits']}")
print(f"warm run sim-API counter (should be all 0, incl. the now-cached diagnostic): {dict(API_CALLS)}")
assert warm_new["new_pvalue"] == 0 and warm_new["new_bool"] == 0, "cache did not prevent re-querying!"
assert API_CALLS["pvalue"] == 0 and API_CALLS["bool"] == 0, "a real query leaked on the warm re-run!"
assert "pvalue_suffix" in json.load(open("Q2_state.json")), "checkpoint not written"
print("CACHE PERSISTENCE OK ✔  (warm re-run sent 0 queries of any kind)")

# ---- PARTIAL RESUME: drop bool_suffix (keep gadget+candidates), expect rebuild
import copy
st = json.load(open("Q2_state.json"))
assert {"bool_base", "bool_candidates", "bool_suffix", "pvalue_suffix"} <= set(st), st.keys()
cold_bool_suffix = list(st["bool_suffix"])
del st["bool_suffix"]                  # simulate a crash after bootstrap, before the chain finished
json.dump(st, open("Q2_state.json", "w"))

API_CALLS.update(pvalue=0, bool=0)
ns3 = fresh_ns()
run(ns3, "PARTIAL RESUME (gadget+candidates kept, bool_suffix dropped)")
assert ns3["_API_STATS"]["new_pvalue"] == 0 and ns3["_API_STATS"]["new_bool"] == 0, "partial resume re-queried!"
assert API_CALLS["pvalue"] == 0 and API_CALLS["bool"] == 0, "partial resume leaked a real query!"
assert [int(x) for x in ns3["YOUR_BOOL_SUFFIX"]] == cold_bool_suffix, "partial-resume bool suffix differs!"
print("PARTIAL RESUME OK ✔  (gadget reused, chain rebuilt from cache, 0 new queries, identical suffix)")

# ---- final independent verification (use the WARM-run suffixes) ------------
print("\n================= INDEPENDENT VERIFICATION =================")
pv_suf = ns2["YOUR_PVALUE_SUFFIX"]; bl_suf = ns2["YOUR_BOOL_SUFFIX"]
assert [int(x) for x in pv_suf] == [int(x) for x in ns["YOUR_PVALUE_SUFFIX"]], "warm != cold (pvalue)"
assert [int(x) for x in bl_suf] == [int(x) for x in ns["YOUR_BOOL_SUFFIX"]], "warm != cold (bool)"
print("warm-run suffixes identical to cold-run ✔")

def check(strs, key, suf, kind):
    suf = [int(x) for x in suf]
    allred = all(key.is_red(suf[i], suf[i + 1]) for i in range(len(suf) - 1))
    ok = True
    for s in strs:
        seq = s.tolist() + suf
        r, t = red_total(seq, key)
        z = z_score_binomial(r, t, 0.5)
        p = float(1 - norm.cdf(-z))
        passed = (p >= 0.01)
        ok &= passed
    print(f"  {kind}: len={len(suf)}  all_edges_red={allred}  ALL_FLIP={ok}  dtype={suf and type(suf[0]).__name__}")
    assert len(suf) <= 140 and allred and ok, f"{kind} FAILED"

check(_pv_strs, PKEY, pv_suf, "pvalue")
check(_bl_strs, BKEY, bl_suf, "bool")

# ---- grade-optimality: achieved length earns the SAME grade as the optimum -----
# With the conditional trim, the suffix may be longer than the exact minimum when
# both sit in a full-marks PLATEAU (e.g. submit 40 instead of 35, both 15 pts) --
# that is intentional (saves queries, identical grade). So we assert the GRADE
# matches the boundary-optimized minimum's grade, not the exact length.
def b1_min(strs, key):
    worst = 0
    for s in strs:
        r, t = red_total(s, key); G = t - 2 * r
        L = 1
        while (L - G) / (t + L) ** 0.5 < norm.ppf(0.01):
            L += 1
        worst = max(worst, L)
    return worst

def grade(ep, x):                                      # Table 1 of the assignment
    if x is None or x > 140:
        return 0.0
    if ep == "pvalue":
        return 15.0 if x <= 40 else 14.0 if x == 41 else (8 + (50 - x) / 2 if x <= 50 else 3 + (140 - x) / 18)
    return 30.0 if x <= 41 else 29.0 if x == 42 else (13 + (50 - x) if x <= 50 else 3 + (140 - x) / 9)

opt_pv, opt_bl = b1_min(_pv_strs, PKEY), b1_min(_bl_strs, BKEY)
Lp, Lb = len(pv_suf), len(bl_suf)
print("\n--- grade vs boundary-optimized theoretical minimum ---")
print(f"  pvalue: achieved {Lp} (grade {grade('pvalue', Lp)}), optimum {opt_pv} (grade {grade('pvalue', opt_pv)})")
print(f"  bool:   achieved {Lb} (grade {grade('bool', Lb)}), optimum {opt_bl} (grade {grade('bool', opt_bl)})")
assert grade("pvalue", Lp) == grade("pvalue", opt_pv), f"pvalue grade below optimum: {Lp} vs {opt_pv}"
assert grade("bool", Lb) == grade("bool", opt_bl), f"bool grade below optimum: {Lb} vs {opt_bl}"
assert Lp <= 41 and Lb <= 42, "targets not met (G out of reachable range in this test)"
print("OPTIMALITY OK ✔  (same grade as the boundary-optimized minimum; <=41 / <=42 met)")

# ---- fine-grained resume: grow_red_chain continues from a saved per-edge partial
# Prove the partial-chain checkpoint actually resumes (not just rebuilds): seed a
# NON-greedy partial and confirm the rebuilt chain keeps it instead of greedy-picking.
G = ns  # cold-run namespace has the build functions + bound bool oracle
grow, ckpt, STATE_g = G["grow_red_chain"], G["checkpoint"], G["STATE"]
irb, cb, bstr, blf = G["is_red_bl"], G["cand_bl"], G["bool_strings"], G["bl"]
st0 = cb[0]
succ = [w for w in cb if w != st0 and irb(st0, w)]
assert len(succ) >= 2, "need >=2 red successors for the resume test"
ckpt(bool_partial=[st0, succ[1]])                       # seed a non-greedy (succ[1]) partial
resumed_chain, _ = grow(irb, cb, st0, bstr, blf, (lambda b: b == 0), hard_cap=8, progress_key="bool")
assert resumed_chain[1] == succ[1], f"per-edge resume ignored the partial: {resumed_chain[1]} != {succ[1]}"
STATE_g.pop("bool_partial", None); STATE_g.pop("bool_starts", None); ckpt()
print("FINE-GRAINED RESUME OK ✔  (grow_red_chain continued from the saved per-edge partial)")

# ---- known-pair store: every recorded colour must match ground truth -------
print("\n--- known-pair store (100%-sure check) ---")
recs = [json.loads(l) for l in open("Q2_known_pairs.jsonl") if l.strip()]
key_of = {"pvalue": PKEY, "bool": BKEY}
n_red = {"pvalue": 0, "bool": 0}; n_grn = {"pvalue": 0, "bool": 0}
wrong = 0
for r in recs:
    truth = key_of[r["ep"]].is_red(r["a"], r["b"])
    if truth != r["red"]:
        wrong += 1
    (n_red if r["red"] else n_grn)[r["ep"]] += 1
for ep in ("pvalue", "bool"):
    print(f"  {ep}: {n_red[ep]} red, {n_grn[ep]} green recorded")
    assert n_red[ep] > 0 and n_grn[ep] > 0, f"{ep}: expected both red and green facts"
assert wrong == 0, f"{wrong} recorded pair colours DISAGREE with ground truth!"
print(f"  all {len(recs)} recorded pair colours match the secret key ✔ (truly 100% sure)")
print("\nALL CHECKS PASSED ✔  (embedded notebook code is correct)")
