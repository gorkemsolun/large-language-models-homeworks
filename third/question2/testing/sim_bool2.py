"""Prototype + validate the redesigned boolean discovery under the real gates:
   every query needs >=50 tokens AND >=50 DISTINCT consecutive pairs."""
import numpy as np
from scipy.stats import norm
from sim import WatermarkKey, red_total, z_score_binomial, make_watermarked_string, unique_pairs

THRESH_Z = norm.ppf(0.01)
QCOUNT = {"n": 0}


def Rstar(N):
    return max((R for R in range(N + 1) if z_score_binomial(R, N) < THRESH_Z), default=-1)


def bool_api(tokens, key):
    """Mimic the real server: reject <50 tokens or <50 distinct pairs, else 0/1."""
    if len(tokens) < 50:
        return {"detail": "You must provide at least 50 tokens for watermark detection."}
    up = unique_pairs(tokens)
    if len(up) < 50:
        return {"detail": "The passage is too repetitive. You must provide a sequence with at "
                          "least 50 distinct consecutive pairs of tokens."}
    QCOUNT["n"] += 1
    r = sum(1 for (a, b) in up if key.is_red(a, b))
    z = z_score_binomial(r, len(up))
    return {"wmark_response": 1 if (1 - norm.cdf(-z)) < 0.01 else 0}


def bl(tokens, key):
    resp = bool_api(tokens, key)
    if "wmark_response" not in resp:
        raise RuntimeError(resp["detail"])
    return resp["wmark_response"]


FREE_LO, FREE_HI = 49000, 50277


def bootstrap_base(key, rng, used, max_bases=4000):
    """Find a 50-distinct-pair path B with EXACTLY 17 reds (R*(51)=R*(52)=17),
    confirmed by mixed bool over B+[a]: mixed <=> R(B)=17 (certain)."""
    pool = [t for t in range(FREE_LO, FREE_HI) if t not in used]
    for _ in range(max_bases):
        B = list(rng.choice(pool, size=51, replace=False))     # 51 tokens -> 50 pairs
        cands = [int(x) for x in rng.choice([p for p in pool if p not in B], size=6, replace=False)]
        results = [bl(B + [c], key) for c in cands]            # B+[a] = 51 pairs
        if 0 in results and 1 in results:                      # mixed => R(B)=17 (certain)
            # green-connected among the tested cands: bool==1
            gc = [cands[i] for i, r in enumerate(results) if r == 1]
            return [int(x) for x in B], gc
    raise RuntimeError("bootstrap failed")


def build_bool_suffix(key, rng, strings, target_len=140):
    used = set(int(t) for s in strings for t in s)
    B, gc_seed = bootstrap_base(key, rng, used)
    B_end = B[-1]
    pool = [t for t in range(FREE_LO, FREE_HI) if t not in used and t not in B]
    rng.shuffle(pool)

    # green-connected nodes: (B_end, a) green  <=>  bl(B+[a]) == 1
    GC = list(gc_seed)
    pi = 0
    def more_gc(n):
        nonlocal pi
        while len([g for g in GC]) < n and pi < len(pool):
            a = pool[pi]; pi += 1
            if a in B or a in GC:
                continue
            if bl(B + [a], key) == 1:
                GC.append(a)
    more_gc(target_len + 20)

    # red-edge oracle for a in GC, any b not in B: bl(B+[a,b]) == 0  <=>  (a,b) red
    def edge_is_red(a, b):
        return bl(B + [a, b], key) == 0

    # greedy red path among GC nodes
    remaining = [g for g in GC]
    chain = [remaining.pop(0)]
    while len(chain) < target_len and remaining:
        cur = chain[-1]; hit = None
        for i, w in enumerate(remaining):
            if edge_is_red(cur, w):
                hit = i; break
        if hit is None:
            # need more GC candidates
            before = len(GC); more_gc(len(GC) + 20)
            remaining += [g for g in GC[before:]]
            if len(GC) == before:
                break
            continue
        chain.append(remaining.pop(hit))
    return chain, B


def minimal_prefix(chain, strings, key):
    def passes(suf):
        for s in strings:
            r, t = red_total(list(s) + suf, key)
            if (1 - norm.cdf(-z_score_binomial(r, t))) < 0.01:
                return False
        return True
    lo, hi, best = 1, len(chain), None
    while lo <= hi:
        mid = (lo + hi) // 2
        if passes(chain[:mid]):
            best = mid; hi = mid - 1
        else:
            lo = mid + 1
    return best


def main():
    print("Rstar: 50->%d  51->%d  52->%d" % (Rstar(50), Rstar(51), Rstar(52)))
    for gp, seed in [(0.46, 0), (0.50, 1), (0.54, 2)]:
        key = WatermarkKey(seed=1000 + seed)
        rng = np.random.default_rng(seed)
        strings = [make_watermarked_string(key, rng, length=131, green_prob=gp) for _ in range(7)]
        worstG = max(red_total(s, key)[1] - 2 * red_total(s, key)[0] for s in strings)
        QCOUNT["n"] = 0
        chain, B = build_bool_suffix(key, rng, strings)
        allred = all(key.is_red(chain[i], chain[i + 1]) for i in range(len(chain) - 1))
        L = minimal_prefix(chain, strings, key)
        ok = L is not None and all(
            (1 - norm.cdf(-z_score_binomial(*red_total(list(s) + chain[:L], key)))) >= 0.01
            for s in strings)
        print(f"  gp={gp} worstG={worstG}: chainLen={len(chain)} allEdgesRed={allred} "
              f"minSuffix={L} allFlip={ok} boolQueries={QCOUNT['n']}")


if __name__ == "__main__":
    main()
