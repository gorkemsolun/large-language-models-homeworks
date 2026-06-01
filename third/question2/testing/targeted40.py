"""Validate the targeted-40 boolean search (chase <=41 => full marks) at G=70.
Strategy: keep the existing 42-token red chain as a fixed BODY, and search for a
first token c1 that is RED after ALL 7 string-ends, so [c1]+body[:39] flips every
string at length 40. Green-connected ends are verified via the base; the rest are
confirmed with 7 real-string checks per surviving candidate. Reuses base+GC (free
on a real re-run). Measures success rate, query cost, and that the 42 is preserved."""
import numpy as np
from scipy.stats import norm
from sim import WatermarkKey, z_score_binomial, unique_pairs, red_total
import bool_estimate as BE

TZ = norm.ppf(0.01)
Q = {"n": 0}


def make_bl(key):
    def bl(tokens):
        if len(tokens) < 50 or len(unique_pairs(tokens)) < 50:
            raise RuntimeError("gate")
        Q["n"] += 1
        r = sum(1 for (a, b) in unique_pairs(tokens) if key.is_red(a, b))
        return 1 if (1 - norm.cdf(-z_score_binomial(r, len(unique_pairs(tokens))))) < 0.01 else 0
    return bl


def flips_at(strings, key, suf):
    for s in strings:
        r, t = red_total(list(s) + suf, key)
        if (1 - norm.cdf(-z_score_binomial(r, t))) >= 0.01:
            continue            # this string flipped (p >= 0.01); check the next
        return False            # p < 0.01 => still watermarked => suffix fails
    return True                 # all 7 flipped


def build_body(bl, B, GC, length):
    """Greedy 39-token red chain (green-connected sources): the reusable body."""
    edge_red = lambda a, b: bl(B + [a, b]) == 0
    remaining = list(GC); chain = [remaining.pop(0)]
    while len(chain) < length and remaining:
        cur = chain[-1]; hit = None
        for i, w in enumerate(remaining):
            if edge_red(cur, w):
                hit = i; break
        if hit is None:
            break
        chain.append(remaining.pop(hit))
    return chain


def targeted_40(bl, key, B, GC, strings, cap, expand_gc=None):
    """Return (suffix40 or None, phase-query dict). Never exceeds `cap` bl calls
    beyond what's already cached in this run."""
    B_end = B[-1]; q0 = Q["n"]; ph = {}
    def tick(name): ph[name] = Q["n"] - q0 - sum(ph.values())
    str_ends = [s[-1] for s in strings]
    ge = [e for e in str_ends if bl(B + [e]) == 1]            # green-connected ends
    nge = len(str_ends) - len(ge)
    tick("ge_detect")
    body = build_body(bl, B, GC, 39)
    tick("body")
    bstart = body[0]
    GCs = list(GC)
    gi = 0
    found = None
    checked = 0
    while Q["n"] - q0 < cap:
        # get next candidate (expand GC pool if exhausted)
        if gi >= len(GCs):
            if expand_gc is None:
                break
            GCs += expand_gc(40)
            if gi >= len(GCs):
                break
        c1 = GCs[gi]; gi += 1
        if c1 in body:
            continue
        # cheap filter: c1 red after every green-connected end
        if not all(bl(B + [e, c1]) == 0 for e in ge):
            continue
        # connection: (c1, body_start) red
        if bl(B + [c1, bstart]) != 0:
            continue
        cand = [c1] + body                                    # 40 tokens
        checked += 1
        # confirm the non-green-connected ends via real-string checks (early-stop)
        if flips_at(strings, key, cand):                      # all 7 strings -> bool 0
            found = cand; break
    tick("search")
    ph["ge"] = len(ge); ph["nge"] = nge; ph["candidates_realchecked"] = checked
    return found, ph


def main():
    print("targeted-40 search (reuse 42 body, find c1 red after all 7 ends)\n")
    print(f"{'seed':>4}{'ge':>4}{'nge':>5}{'found40':>9}{'allRed':>7}{'allFlip':>8}{'newQ':>7}  phases")
    succ = 0
    for seed in range(8):
        key = WatermarkKey(seed=5000 + seed)
        rng = np.random.default_rng(200 + seed)
        pool = list(range(40000, 50277))
        strings = [BE.make_string_exactG(key, rng, pool, 130, 30) for _ in range(7)]   # G=70
        used = set(t for s in strings for t in s)
        free = [t for t in range(40000, 50277) if t not in used]
        rng.shuffle(free)
        bl = make_bl(key)
        Q["n"] = 0
        # base + initial GC (this is the cached/free part on a real re-run)
        B, gc_seed = BE.bootstrap_base(bl, rng, free)
        it = [0]
        def collect(n):
            got = []
            while len(got) < n and it[0] < len(free):
                a = free[it[0]]; it[0] += 1
                if a in B or a in gc_seed or a in got:
                    continue
                if bl(B + [a]) == 1:
                    got.append(a)
            return got
        GC = list(gc_seed) + collect(110)
        base_q = Q["n"]                                       # everything so far = cached on re-run
        found, ph = targeted_40(bl, key, B, GC, strings, cap=4000, expand_gc=collect)
        new_q = Q["n"] - base_q
        if found:
            succ += 1
            allred = all(key.is_red(found[i], found[i + 1]) for i in range(len(found) - 1))
            flip = flips_at(strings, key, found)
            print(f"{seed:>4}{ph['ge']:>4}{ph['nge']:>5}{'YES(40)':>9}{str(allred):>7}{str(flip):>8}{new_q:>7}  {ph}")
        else:
            print(f"{seed:>4}{ph['ge']:>4}{ph['nge']:>5}{'no->keep42':>9}{'-':>7}{'-':>8}{new_q:>7}  {ph}")
    print(f"\nreached 40 in {succ}/8 seeds")


if __name__ == "__main__":
    main()
