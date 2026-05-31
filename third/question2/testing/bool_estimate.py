"""Calibrated bool query estimate: strings with EXACTLY red=30/total=130/G=70
(the hidden-model statistics the p-value task revealed), comparing the boolean
solver WITH vs WITHOUT the boundary-pair optimization. Faithful replica of the
notebook's balanced-base method, with per-phase bool-query counting."""
import numpy as np
from scipy.stats import norm
from sim import WatermarkKey, z_score_binomial, unique_pairs, red_total

TZ = norm.ppf(0.01)
PHASE = {"name": "?", "c": {}}


def Rstar(N):
    return max((R for R in range(N + 1) if z_score_binomial(R, N) < TZ), default=-1)


def make_bool_api(key):
    def bool_api(tokens):
        if len(tokens) < 50:
            raise RuntimeError(">=50 tokens")
        up = unique_pairs(tokens)
        if len(up) < 50:
            raise RuntimeError(">=50 distinct pairs")
        PHASE["c"][PHASE["name"]] = PHASE["c"].get(PHASE["name"], 0) + 1
        r = sum(1 for (a, b) in up if key.is_red(a, b))
        return 1 if (1 - norm.cdf(-z_score_binomial(r, len(up)))) < 0.01 else 0
    return bool_api


def make_string_exactG(key, rng, pool, total=130, reds=30):
    """A path of total+1 DISTINCT tokens with EXACTLY `reds` red consecutive pairs
    (so green_excess = total-2*reds). Mirrors the real strings (130 pairs, 30 red)."""
    toks = [int(rng.choice(pool))]
    seen = {toks[0]}
    nred = 0
    for i in range(total):
        want_red = nred < reds and (rng.random() < (reds - nred) / (total - i))
        for _ in range(400):
            c = int(rng.choice(pool))
            if c in seen:
                continue
            if key.is_red(toks[-1], c) == want_red:
                toks.append(c); seen.add(c); nred += int(want_red); break
        else:
            raise RuntimeError("string build stuck")
    return toks


# ---------- balanced-base bool oracle (faithful to the notebook) -------------
def bootstrap_base(bl, rng, pool, max_bases=20000):
    PHASE["name"] = "bootstrap"
    for _ in range(max_bases):
        B = [int(x) for x in rng.choice(pool, size=51, replace=False)]
        if bl(B) == 1:                                   # R(B)<=16, skip
            continue
        rest = [p for p in pool if p not in B]
        cands = [int(x) for x in rng.choice(rest, size=6, replace=False)]
        res = [bl(B + [c]) for c in cands]
        if 0 in res and 1 in res:                        # mixed => R(B)=17
            gc = [c for c, r in zip(cands, res) if r == 1]
            return B, gc
    raise RuntimeError("bootstrap failed")


def collect_gc(bl, B, rng, pool, gc_seed, n):
    PHASE["name"] = "collectGC"
    GC = list(gc_seed)
    for a in pool:
        if len(GC) >= n:
            break
        if a in B or a in GC:
            continue
        if bl(B + [a]) == 1:                              # (B_end,a) green
            GC.append(a)
    return GC


def grading_check_lengths(cap=140):
    pts = {41, 42, 43, 50}
    pts |= set(range(24, 50, 8)) | set(range(50, cap + 1, 10)) | {cap}
    return sorted(p for p in pts if 1 <= p <= cap)


def grow_chain(bl, B, start, strings, key, check_lengths, hard_cap=140):
    """Greedy red path; bool0 of B+[a,b] => (a,b) red. Check all strings only at
    grading boundaries; stop at first all-flip."""
    edge_red = lambda a, b: bl(B + [a, b]) == 0
    def flips(suf):
        for s in strings:
            r, t = red_total(list(s) + suf, key)
            if (1 - norm.cdf(-z_score_binomial(r, t))) >= 0.01:
                continue
            return False
        return True
    # `flips` must NOT call the API (string detection is local in sim); on the
    # real API this is bl(string+suffix). We count those separately as "checks".
    remaining = [c for c in start[1] if c != start[0]]
    chain = [start[0]]
    checkset = set(check_lengths)
    flipped = False
    while len(chain) < hard_cap and remaining:
        cur = chain[-1]; hit = None
        for i, w in enumerate(remaining):
            if edge_red(cur, w):
                hit = i; break
        if hit is None:
            break
        chain.append(remaining.pop(hit))
        if len(chain) in checkset:
            PHASE["c"]["checks"] = PHASE["c"].get("checks", 0) + 7    # 7 bl(string+suffix)
            if flips(chain):
                flipped = True; break
    return chain, flipped


def run(key, strings, pool, rng, boundary_opt, n_candidates, K):
    PHASE["c"] = {}
    bl = make_bool_api(key)
    B, gc_seed = bootstrap_base(bl, rng, pool)
    GC = collect_gc(bl, B, rng, pool, gc_seed, n_candidates)
    cl = grading_check_lengths()

    PHASE["name"] = "boundary"
    starts = []
    if boundary_opt:
        str_ends = [s[-1] for s in strings]
        ge = [e for e in str_ends if (key.is_red(B[-1], e) == False)]   # green-connected ends
        # find up to 4 starts red after ALL green-connected ends
        bstarts = []
        for c in GC:
            ok = True
            for e in ge:
                if not (bl(B + [e, c]) == 0):     # (e,c) red?  (e is green-connected)
                    ok = False; break
            if ok and ge:
                bstarts.append(c)
                if len(bstarts) >= 4:
                    break
        extra = [c for c in GC if c not in bstarts]
        starts = (bstarts + extra)[:K]
    else:
        starts = GC[:K]                            # plain green-connected starts, no search

    PHASE["name"] = "build"
    best_len, best = None, None
    for st in starts:
        cap = 140 if best_len is None else min(140, best_len)   # no point building longer
        chain, flipped = grow_chain(bl, B, (st, GC), strings, key, cl, hard_cap=cap)
        if not flipped:
            continue
        L = len(chain)
        # trim only in scoring slope (43..50); 42/41 kept as-is
        if 43 <= L <= 50:
            lo, hi = 1, L
            def flips_at(m):
                PHASE["c"]["checks"] = PHASE["c"].get("checks", 0) + 7
                for s in strings:
                    r, t = red_total(list(s) + chain[:m], key)
                    if (1 - norm.cdf(-z_score_binomial(r, t))) >= 0.01:
                        continue
                    return False
                return True
            best_m = L
            while lo <= hi:
                mid = (lo + hi) // 2
                if flips_at(mid):
                    best_m = mid; hi = mid - 1
                else:
                    lo = mid + 1
            L = best_m
        if best_len is None or L < best_len:
            best_len, best = L, chain[:L]
        if best_len <= 41:                          # full marks, stop
            break
    allred = all(key.is_red(best[i], best[i + 1]) for i in range(len(best) - 1))
    return best_len, allred, dict(PHASE["c"]), sum(PHASE["c"].values())


def main():
    print(f"Rstar(51)=Rstar(52)={Rstar(51)} (base needs R=17)\n")
    print(f"{'mode':<14}{'G':>3}{'seed':>5}{'suffix':>7}{'allRed':>7} | "
          f"{'boot':>5}{'gc':>4}{'bnd':>5}{'build':>6}{'checks':>7}{'TOTAL':>7}")
    for G in (68, 70, 72):
        reds = (130 - G) // 2
        for seed in (0, 1, 2):
            key = WatermarkKey(seed=4000 + seed * 9 + G)
            rng = np.random.default_rng(100 + seed + G)
            pool = list(range(40000, 50277))
            strings = [make_string_exactG(key, rng, pool, 130, reds) for _ in range(7)]
            used = set(t for s in strings for t in s)
            free = [t for t in range(40000, 50277) if t not in used]
            for mode, bo, ncand, K in [("no-opt(K=3)", False, 110, 3),
                                       ("with-opt(K=10)", True, 160, 10)]:
                rng2 = np.random.default_rng(7000 + seed + G)
                rng2.shuffle(free)
                L, allred, ph, tot = run(key, strings, free, rng2, bo, ncand, K)
                print(f"{mode:<14}{G:>3}{seed:>5}{L:>7}{str(allred):>7} | "
                      f"{ph.get('bootstrap',0):>5}{ph.get('collectGC',0):>4}"
                      f"{ph.get('boundary',0):>5}{ph.get('build',0):>6}"
                      f"{ph.get('checks',0):>7}{tot:>7}")
        print()


if __name__ == "__main__":
    main()
