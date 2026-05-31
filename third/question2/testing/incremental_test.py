"""Does running Q2_code in 200-new-query increments waste any queries vs one run?
Simulates the workflow: each 'run' = fresh namespace (re-seeds random, like Run-All)
that reloads the on-disk cache/state, does <=CAP new bool queries, then the cap
raises RuntimeError. Repeat until the bool phase finishes. Compare total NEW bool
queries to a single uncapped run. Equal total => no detriment."""
import json, os, io, contextlib
import numpy as np
from scipy.stats import norm
from sim import WatermarkKey, red_total, z_score_binomial
import time as _t; _t.sleep = lambda *a, **k: None

NB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Q2_code.ipynb")
nb = json.load(open(NB))
def cell_src(prefix):
    for c in nb["cells"]:
        if c["cell_type"] == "code" and "".join(c["source"]).startswith(prefix):
            return "".join(c["source"])
    raise KeyError(prefix)

BKEY = WatermarkKey(seed=22222)
def make_string_exactG(key, rng, pool, total=130, reds=30):     # G = 130-2*30 = 70 (real value)
    toks = [int(rng.choice(pool))]; seen = {toks[0]}; nred = 0
    for i in range(total):
        want = nred < reds and (rng.random() < (reds - nred) / (total - i))
        for _ in range(400):
            c = int(rng.choice(pool))
            if c in seen: continue
            if key.is_red(toks[-1], c) == want:
                toks.append(c); seen.add(c); nred += int(want); break
    return toks
_rng = np.random.default_rng(5)
_pool = list(range(40000, 50277))
BL = [make_string_exactG(BKEY, _rng, _pool) for _ in range(7)]
TOKENS = [{"tokens": ",".join(map(str, s)), "endpoint": "bool"} for s in BL]
print("bool strings green-excess:", sorted(red_total(s, BKEY)[1] - 2 * red_total(s, BKEY)[0] for s in BL))

API = {"bool": 0}
def t2a(s): return np.array([int(i) for i in s.split(",")])
def t2s(a): return ",".join(str(int(i)) for i in a)
def validate_tokens(s):
    parts = s.split(","); assert all(p.isdigit() for p in parts); assert 40 <= len(parts) <= 1000
def get_tokens(): return TOKENS
def _reject(s):
    tk = [int(x) for x in s.split(",")]
    if len(tk) < 50: return {"detail": "at least 50 tokens"}
    if len({(tk[i-1], tk[i]) for i in range(1, len(tk))}) < 50: return {"detail": "at least 50 distinct pairs"}
    return None
def get_bool(s):
    validate_tokens(s); r = _reject(s)
    if r: return r
    API["bool"] += 1
    rr, t = red_total(t2a(s), BKEY)
    return {"submitted_tokens": s, "wmark_response": 1 if (1 - norm.cdf(-z_score_binomial(rr, t, 0.5))) < 0.01 else 0}
def get_pvalue(s): raise RuntimeError("pvalue unused here")

def clean():
    for f in ("Q2_query_cache.jsonl", "Q2_state.json", "Q2_known_pairs.jsonl", "Q2_run.log"):
        if os.path.exists(f): os.remove(f)

def one_run(cap):
    ns = dict(np=np, json=json, tokens_to_array=t2a, tokens_to_string=t2s,
              validate_tokens=validate_tokens, get_tokens=get_tokens,
              get_pvalue=get_pvalue, get_bool=get_bool)
    ns["tokens"] = get_tokens()                             # normally set by cell 3
    with contextlib.redirect_stdout(io.StringIO()):
        exec(cell_src("# ============"), ns)                 # helpers: re-seeds random.seed(0)
        ns["MAX_NEW_BOOL_QUERIES"] = cap
        ns["MAX_NEW_PVALUE_QUERIES"] = None
        ns["bool_token_ids"] = [t for t in ns["tokens"] if t["endpoint"] == "bool"]
        try:
            exec(cell_src("# ---------------- Solve the boolean endpoint"), ns)
            return True
        except RuntimeError as e:
            if "Stopped after" in str(e):
                return False
            raise

def check():
    suf = json.load(open("Q2_state.json"))["bool_suffix"]
    allred = all(BKEY.is_red(suf[i], suf[i + 1]) for i in range(len(suf) - 1))
    flip = all((1 - norm.cdf(-z_score_binomial(*red_total(list(s) + suf, BKEY)))) >= 0.01 for s in BL)
    return len(suf), allred, flip

# ---- incremental: cap=200 new bool queries per run, re-seed each run --------
print("\n=== INCREMENTAL (cap = 200 new bool queries / run) ===")
clean(); API["bool"] = 0; runs = 0; done = False
while not done and runs < 40:
    runs += 1; before = API["bool"]
    done = one_run(200)
    print(f"  run {runs:2d}: +{API['bool'] - before:3d} new bool queries  (cumulative {API['bool']})  finished={done}")
inc_total = API["bool"]; L, ar, fl = check()
print(f"  => {runs} runs, suffix len {L}, allEdgesRed={ar}, allFlip={fl}, TOTAL new bool queries={inc_total}")

# ---- single uncapped run (fresh) --------------------------------------------
print("\n=== SINGLE UNCAPPED RUN ===")
clean(); API["bool"] = 0
done = one_run(None)
L2, ar2, fl2 = check()
single = API["bool"]
print(f"  => suffix len {L2}, allEdgesRed={ar2}, allFlip={fl2}, TOTAL new bool queries={single}")

print(f"\nWASTE from running in 200-query increments: {inc_total - single} extra queries "
      f"({100*(inc_total-single)/max(single,1):+.1f}%)   same suffix length: {L==L2}")
