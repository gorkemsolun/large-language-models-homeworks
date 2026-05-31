"""Validate boundary-optimized minimal suffix on 131-token strings."""
import numpy as np
from scipy.stats import norm
from sim import (WatermarkKey, red_total, z_score_binomial, make_watermarked_string,
                 make_pvalue_edge_oracle, make_bool_edge_oracle, unique_pairs, recover_red_count)

THRESH = norm.ppf(0.01)


def detect_pvalue(toks, key):
    r, t = red_total(toks, key)
    return float(1 - norm.cdf(-z_score_binomial(r, t))) if t else 1.0

def detect_bool(toks, key):
    return 1 if detect_pvalue(toks, key) < 0.01 else 0


# ---- chain builders (same logic that will go in the notebook) --------------
def grow_red_chain(edge_is_red, candidates, start, strings, detect_fn, passes, check_every=6, hard_cap=140):
    remaining = [c for c in candidates if c != start]
    chain = [start]; last = 0
    while len(chain) < hard_cap and remaining:
        cur = chain[-1]; hit = None
        for i, w in enumerate(remaining):
            if edge_is_red(cur, w):
                hit = i; break
        if hit is None:
            break
        chain.append(remaining.pop(hit))
        if len(chain) - last >= check_every:
            last = len(chain)
            if all(passes(detect_fn(list(s) + chain)) for s in strings):
                break
    return chain

def minimal_prefix(chain, strings, detect_fn, passes):
    lo, hi, best = 1, len(chain), None
    while lo <= hi:
        mid = (lo + hi) // 2
        if all(passes(detect_fn(list(s) + chain[:mid])) for s in strings):
            best = mid; hi = mid - 1
        else:
            lo = mid + 1
    return best

def best_suffix(edge_is_red, candidates, strings, detect_fn, passes, starts, hard_cap=140):
    best_chain, best_len = None, None
    per = []
    for st in starts:
        chain = grow_red_chain(edge_is_red, candidates, st, strings, detect_fn, passes, hard_cap=hard_cap)
        L = minimal_prefix(chain, strings, detect_fn, passes)
        per.append(L)
        if L is not None and (best_len is None or L < best_len):
            best_len, best_chain = L, chain[:L]
    return best_chain, best_len, per


def gen_strings(key, rng, gp, n=7, length=131):
    return [make_watermarked_string(key, rng, length=length, green_prob=gp) for _ in range(n)]


print(f"{'ep':>6} {'gp':>5} {'worstG':>6} {'baseZ':>7} | {'b=0 min':>7} {'bestK min':>9}  reach<=tgt")
for gp in [0.44, 0.50, 0.54, 0.58]:
    # ---------- p-value ----------
    key = WatermarkKey(seed=7)
    rng = np.random.default_rng(int(gp * 1000))
    strs = gen_strings(key, rng, gp)
    stoks = set(int(t) for s in strs for t in s)
    info = []
    for s in strs:
        R, T = red_total(s, key); info.append((T - 2 * R, T, R, int(s[-1])))
    worstG = max(g for g, *_ in info)
    baseZ = min(z_score_binomial(R, T) for g, T, R, e in info)

    oracle, cands, qc = make_pvalue_edge_oracle(lambda t: detect_pvalue(t, key), rng, stoks)
    detect = lambda t: detect_pvalue(t, key); passes = lambda p: p >= 0.01

    # (1) plain single chain (arbitrary start -> boundary random)
    _, L0, _ = best_suffix(oracle, cands, strs, detect, passes, starts=cands[:1])
    # (2) boundary-aware: pick starts red-after the binding strings
    #     binding set = strings whose b=0 requirement within 2 of the max
    def req_L(G, T, b):
        L = 1
        while not (L - 2 + 2 * b - G >= THRESH * (T + L) ** 0.5):  # THRESH negative
            L += 1
            if L > 400: break
        return L
    reqs = [req_L(g, T, 0) for g, T, R, e in info]
    mx = max(reqs)
    binding_ends = [info[i][3] for i in range(len(info)) if reqs[i] >= mx - 2]
    # find candidate starts red after ALL binding ends
    good = []
    for c in cands[200:600]:
        if all(oracle(e, c) for e in binding_ends):
            good.append(c)
        if len(good) >= 4:
            break
    starts = good if good else cands[:4]
    _, Lb, per = best_suffix(oracle, cands, strs, detect, passes, starts=starts)
    print(f"{'pvalue':>6} {gp:>5} {worstG:>6} {baseZ:>7.2f} | {L0:>7} {Lb:>9}  "
          f"(<=41: {Lb is not None and Lb <= 41}) bindingEnds={len(binding_ends)} perStart={per}")

    # ---------- bool (best-of-K random starts) ----------
    key = WatermarkKey(seed=99)
    rng = np.random.default_rng(int(gp * 1000) + 1)
    strs = gen_strings(key, rng, gp)
    stoks = set(int(t) for s in strs for t in s)
    worstG = max(red_total(s, key)[1] - 2 * red_total(s, key)[0] for s in strs)
    baseZ = min(z_score_binomial(*red_total(s, key)) for s in strs)
    boracle, bcands, bqc, gad = make_bool_edge_oracle(lambda t: detect_bool(t, key), rng, stoks, 170)
    detectb = lambda t: detect_bool(t, key); passesb = lambda b: b == 0
    _, Lb0, _ = best_suffix(boracle, bcands, strs, detectb, passesb, starts=bcands[:1])
    K = 5
    _, LbK, perb = best_suffix(boracle, bcands, strs, detectb, passesb, starts=bcands[:K])
    print(f"{'bool':>6} {gp:>5} {worstG:>6} {baseZ:>7.2f} | {Lb0:>7} {LbK:>9}  "
          f"(<=42: {LbK is not None and LbK <= 42}) perStart={perb}")
