"""Validate the renamed p-value solver in the simulator: fresh -> 40 (full marks,
all flip), and resume from checkpoint -> 0 new queries."""
import json, os, io, contextlib
import numpy as np
from scipy.stats import norm
from sim import WatermarkKey, red_total, z_score_binomial
import bool_estimate as BE
import time as _t; _t.sleep = lambda *a, **k: None

NB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Q2_code.ipynb")
nb = json.load(open(NB))
def cell_src(prefix):
    for c in nb["cells"]:
        if c["cell_type"] == "code" and "".join(c["source"]).startswith(prefix):
            return "".join(c["source"])
    raise KeyError(prefix)

PKEY = WatermarkKey(seed=11111)
rng = np.random.default_rng(9); pool = list(range(40000, 50277))
PV = [BE.make_string_exactG(PKEY, rng, pool, 130, 30) for _ in range(7)]   # G=70
TOKENS = [{"tokens": ",".join(map(str, s)), "endpoint": "pvalue"} for s in PV]
API = {"pvalue": 0}
def t2a(s): return np.array([int(i) for i in s.split(",")])
def t2s(a): return ",".join(str(int(i)) for i in a)
def validate_tokens(s):
    parts = s.split(","); assert all(p.isdigit() for p in parts); assert 40 <= len(parts) <= 1000
def get_tokens(): return TOKENS
def _rej(s):
    tk = [int(x) for x in s.split(",")]
    if len(tk) < 50: return {"detail": "50 tokens"}
    if len({(tk[i-1], tk[i]) for i in range(1, len(tk))}) < 50: return {"detail": "50 pairs"}
    return None
def get_pvalue(s):
    validate_tokens(s); r = _rej(s)
    if r: return r
    API["pvalue"] += 1
    rr, t = red_total(t2a(s), PKEY)
    return {"submitted_tokens": s, "wmark_response": float(1 - norm.cdf(-z_score_binomial(rr, t, 0.5)))}
def get_bool(s): raise RuntimeError("bool unused")

def clean():
    for f in ("Q2_query_cache.jsonl", "Q2_state.json", "Q2_known_pairs.jsonl", "Q2_run.log"):
        if os.path.exists(f): os.remove(f)

def run():
    ns = dict(np=np, json=json, tokens_to_array=t2a, tokens_to_string=t2s,
              validate_tokens=validate_tokens, get_tokens=get_tokens,
              get_pvalue=get_pvalue, get_bool=get_bool)
    ns["tokens"] = get_tokens()
    q0 = API["pvalue"]
    with contextlib.redirect_stdout(io.StringIO()):
        exec(cell_src("# ============"), ns)
        ns["MAX_NEW_PVALUE_QUERIES"] = None; ns["MAX_NEW_BOOL_QUERIES"] = None
        ns["pvalue_token_ids"] = [t for t in ns["tokens"] if t["endpoint"] == "pvalue"]
        exec(cell_src("# ---------------- Solve the p-value endpoint"), ns)
    return list(ns["YOUR_PVALUE_SUFFIX_LIST"]), API["pvalue"] - q0

def flips(suf):
    return all((1 - norm.cdf(-z_score_binomial(*red_total(list(s) + suf, PKEY)))) >= 0.01 for s in PV)

clean(); API["pvalue"] = 0
s1, q1 = run()
print("fresh :", dict(L=len(s1), red=all(PKEY.is_red(s1[i], s1[i+1]) for i in range(len(s1)-1)),
                       flip=flips(s1), inrange=all(0 <= x < 50277 for x in s1)), "newQ", q1)
API["pvalue"] = 0
s2, q2 = run()
print("resume:", dict(L=len(s2)), "newQ", q2, "(expect 0)")
ok = len(s1) == 40 and flips(s1) and all(PKEY.is_red(s1[i], s1[i+1]) for i in range(len(s1)-1)) and q2 == 0
print("\nP-VALUE SOLVER OK" if ok else "*** FAILURE ***")
clean()
