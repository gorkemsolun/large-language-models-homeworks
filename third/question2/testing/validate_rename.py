"""Run the RENAMED notebook code end-to-end against the simulator (caps lifted)
and confirm both endpoints still produce all-red, all-flipping suffixes."""
import json, os, io, contextlib
import numpy as np
from scipy.stats import norm
from sim import WatermarkKey, red_total, z_score_binomial
import bool_estimate as BE
import time as _t; _t.sleep = lambda *a, **k: None

nb = json.load(open(os.path.join(os.path.dirname(__file__), "..", "Q2_code.ipynb")))
def cell(prefix):
    for c in nb["cells"]:
        if c["cell_type"] == "code" and "".join(c["source"]).startswith(prefix):
            return "".join(c["source"])
    raise KeyError(prefix)

PKEY = WatermarkKey(seed=11111); BKEY = WatermarkKey(seed=22222)
rng = np.random.default_rng(5); pool = list(range(40000, 50277))
PV = [BE.make_string_exactG(PKEY, rng, pool, 130, 43) for _ in range(7)]   # green-excess 44
BL = [BE.make_string_exactG(BKEY, rng, pool, 130, 30) for _ in range(7)]   # green-excess 70
TOK = ([{"tokens": ",".join(map(str, s)), "endpoint": "pvalue"} for s in PV] +
       [{"tokens": ",".join(map(str, s)), "endpoint": "bool"} for s in BL])
API = {"pvalue": 0, "bool": 0}
def t2a(s): return np.array([int(i) for i in s.split(",")])
def t2s(a): return ",".join(str(int(i)) for i in a)
def validate_tokens(s):
    p = s.split(","); assert all(q.isdigit() for q in p); assert 40 <= len(p) <= 1000
def get_tokens(): return TOK
def rej(s):
    tk = [int(x) for x in s.split(",")]
    if len(tk) < 50: return {"detail": "50 tokens"}
    if len({(tk[i-1], tk[i]) for i in range(1, len(tk))}) < 50: return {"detail": "50 pairs"}
def gp(s):
    validate_tokens(s); r = rej(s)
    if r: return r
    API["pvalue"] += 1; rr, t = red_total(t2a(s), PKEY)
    return {"submitted_tokens": s, "wmark_response": float(1 - norm.cdf(-z_score_binomial(rr, t, 0.5)))}
def gb(s):
    validate_tokens(s); r = rej(s)
    if r: return r
    API["bool"] += 1; rr, t = red_total(t2a(s), BKEY)
    return {"submitted_tokens": s, "wmark_response": 1 if (1 - norm.cdf(-z_score_binomial(rr, t, 0.5))) < 0.01 else 0}

for f in ("Q2_query_cache.jsonl", "Q2_state.json", "Q2_known_pairs.jsonl", "Q2_run.log"):
    if os.path.exists(f): os.remove(f)
ns = dict(np=np, json=json, tokens_to_array=t2a, tokens_to_string=t2s, validate_tokens=validate_tokens,
          get_tokens=get_tokens, get_pvalue=gp, get_bool=gb)
ns["tokens"] = get_tokens()
ns["pvalue_token_ids"] = [t for t in ns["tokens"] if t["endpoint"] == "pvalue"]
ns["bool_token_ids"] = [t for t in ns["tokens"] if t["endpoint"] == "bool"]
out = io.StringIO()
with contextlib.redirect_stdout(out):
    exec(cell("# ============"), ns)
    ns["MAX_NEW_PVALUE_QUERIES"] = None; ns["MAX_NEW_BOOL_QUERIES"] = None
    exec(cell("# ---------------- Solve the p-value"), ns)
    exec(cell("# ---------------- Solve the boolean"), ns)
    exec(cell("# Suffixes discovered"), ns)
    exec(cell("# Validate your suffixes"), ns)

pv = [int(x) for x in ns["YOUR_PVALUE_SUFFIX"]]; bl = [int(x) for x in ns["YOUR_BOOL_SUFFIX"]]
def flips(strs, key, suf):
    return all((1 - norm.cdf(-z_score_binomial(*red_total(list(s) + suf, key)))) >= 0.01 for s in strs)
pv_ok = flips(PV, PKEY, pv) and all(PKEY.is_red(pv[i], pv[i+1]) for i in range(len(pv)-1))
bl_ok = flips(BL, BKEY, bl) and all(BKEY.is_red(bl[i], bl[i+1]) for i in range(len(bl)-1))
print(f"p-value: len {len(pv)} all-red+flip {pv_ok}")
print(f"bool   : len {len(bl)} all-red+flip {bl_ok}")
print("api calls", API)
for l in out.getvalue().splitlines():
    if any(k in l for k in ("P-VALUE SUFFIX", "BOOL SUFFIX", "boundary-opt FOUND", "full marks", "token-id range", "STILL WATERMARKED", "Error", "Traceback")):
        print("  LOG:", l)
for f in ("Q2_query_cache.jsonl", "Q2_state.json", "Q2_known_pairs.jsonl", "Q2_run.log"):
    if os.path.exists(f): os.remove(f)
print("RENAME RUNTIME VALIDATION:", "PASS" if (pv_ok and bl_ok) else "FAIL")
