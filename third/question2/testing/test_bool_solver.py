"""Validate the refactored bool solver (boundary search merged in) from ../Q2_code.ipynb:
 (1) fresh + BOOL_BOUNDARY_OPT=True  -> 40 (all-red, all-flip), 42 banked first
 (2) fresh + BOOL_BOUNDARY_OPT=False -> 42
 (3) resume with bool_suffix=40 (user's state) -> returns 40, 0 new queries
 (4) resume with 42 banked, opt NOT done -> runs boundary search -> 40 (reuses base/42)"""
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

BKEY = WatermarkKey(seed=22222)
_rng = np.random.default_rng(5); _pool = list(range(40000, 50277))
BL = [BE.make_string_exactG(BKEY, _rng, _pool, 130, 30) for _ in range(7)]   # G=70
TOKENS = [{"tokens": ",".join(map(str, s)), "endpoint": "bool"} for s in BL]
API = {"bool": 0}
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
def get_bool(s):
    validate_tokens(s); r = _rej(s)
    if r: return r
    API["bool"] += 1
    rr, t = red_total(t2a(s), BKEY)
    return {"submitted_tokens": s, "wmark_response": 1 if (1 - norm.cdf(-z_score_binomial(rr, t, 0.5))) < 0.01 else 0}
def get_pvalue(s): raise RuntimeError("pvalue unused")
def flips(suf):
    return all((1 - norm.cdf(-z_score_binomial(*red_total(list(s) + suf, BKEY)))) >= 0.01 for s in BL)
def clean():
    for f in ("Q2_query_cache.jsonl", "Q2_state.json", "Q2_known_pairs.jsonl", "Q2_run.log"):
        if os.path.exists(f): os.remove(f)

SOLVER = cell_src("# ---------------- Solve the boolean endpoint")

def run_solver(src=SOLVER):
    ns = dict(np=np, json=json, tokens_to_array=t2a, tokens_to_string=t2s,
              validate_tokens=validate_tokens, get_tokens=get_tokens,
              get_pvalue=get_pvalue, get_bool=get_bool)
    ns["tokens"] = get_tokens()
    q0 = API["bool"]
    with contextlib.redirect_stdout(io.StringIO()):
        exec(cell_src("# ============"), ns)
        ns["MAX_NEW_BOOL_QUERIES"] = None; ns["MAX_NEW_PVALUE_QUERIES"] = None
        ns["bool_token_ids"] = [t for t in ns["tokens"] if t["endpoint"] == "bool"]
        exec(src, ns)
    return list(ns["YOUR_BOOL_SUFFIX_LIST"]), API["bool"] - q0

def chk(suf):
    return dict(L=len(suf), red=all(BKEY.is_red(suf[i], suf[i+1]) for i in range(len(suf)-1)),
                flip=flips(suf), inrange=all(0 <= x < 50277 for x in suf), distinct=len(set(suf)) == len(suf))

# (1) fresh, boundary-opt True
clean(); API["bool"] = 0
s1, q1 = run_solver()
print("(1) fresh opt=True :", chk(s1), "newQ", q1, "| state bool_opt_done", json.load(open("Q2_state.json")).get("bool_opt_done"))

# (2) fresh, boundary-opt False
clean(); API["bool"] = 0
s2, q2 = run_solver(SOLVER.replace("BOOL_BOUNDARY_OPT = True", "BOOL_BOUNDARY_OPT = False"))
print("(2) fresh opt=False:", chk(s2), "newQ", q2)

# (3) resume from (1)'s state (bool_suffix=40): re-run, expect 40 with 0 new queries
clean(); API["bool"] = 0
run_solver()                              # rebuild state with the 40 (fresh)
API["bool"] = 0
s3, q3 = run_solver()                     # re-run -> should resume
print("(3) resume (had 40):", chk(s3), "newQ", q3, "(expect 0)")

# (4) 42 banked, opt NOT done -> boundary search should run and reach 40
clean(); API["bool"] = 0
run_solver(SOLVER.replace("BOOL_BOUNDARY_OPT = True", "BOOL_BOUNDARY_OPT = False"))   # -> 42 + bool_opt_done
st = json.load(open("Q2_state.json")); st.pop("bool_opt_done", None)                 # simulate interrupted before opt
json.dump(st, open("Q2_state.json", "w"))
API["bool"] = 0
s4, q4 = run_solver()                     # opt=True, resumes 42 body, searches -> 40
print("(4) resume 42->opt :", chk(s4), "newQ", q4)

ok = (chk(s1)["L"] == 40 and chk(s1)["red"] and chk(s1)["flip"]
      and chk(s2)["L"] == 42 and chk(s2)["flip"]
      and chk(s3)["L"] == 40 and q3 == 0
      and chk(s4)["L"] == 40 and chk(s4)["red"] and chk(s4)["flip"])
print("\nALL PATHS OK" if ok else "*** FAILURE ***")
clean()
