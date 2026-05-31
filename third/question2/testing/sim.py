"""Local simulator + offline validation of the Q2 watermark-breaking attacks.

This mirrors the EXACT detection used by the ETH watermark API:
  - detection counts UNIQUE consecutive token pairs (duplicates skipped),
  - z = (red - total/2) / sqrt(total * 0.25),
  - p_value = 1 - norm.cdf(-z)  ==  norm.cdf(z),
  - watermarked  <=>  p_value < 0.01  <=>  z < ppf(0.01) ~ -2.326.

It also enforces BOTH real server gates (discovered from real-API tests):
  - a query needs >= 50 tokens, AND
  - a query needs >= 50 DISTINCT consecutive pairs ("too repetitive" otherwise).

A pair (a, b) is "red" iff token b is in the red list seeded by previous token a.
We model that exactly as the API claims: under the null each token is red w.p. 0.5,
and redness depends only on (a, b). We realise it with a keyed hash.
"""
import hashlib
import numpy as np
from scipy.stats import norm

VOCAB = 50277  # valid ids in [0, 50277)
THRESH_Z = norm.ppf(0.01)  # ~ -2.3263
MIN_TOKENS = 50            # server gate 1
MIN_PAIRS = 50            # server gate 2 (distinct consecutive pairs)


# ----------------------------------------------------------------------------
# Watermark oracle (SECRET in reality; here simulated with a keyed hash)
# ----------------------------------------------------------------------------
class WatermarkKey:
    def __init__(self, seed: int):
        self.seed = seed.to_bytes(8, "little")

    def is_red(self, a: int, b: int) -> bool:
        """Whether pair (a, b) is red: depends only on (a, b). ~50% red."""
        h = hashlib.sha256(self.seed + int(a).to_bytes(4, "little") + int(b).to_bytes(4, "little")).digest()
        return (h[0] & 1) == 1


def unique_pairs(tokens):
    s = set()
    for i in range(1, len(tokens)):
        s.add((int(tokens[i - 1]), int(tokens[i])))
    return s


def red_total(tokens, key: WatermarkKey):
    up = unique_pairs(tokens)
    total = len(up)
    red = sum(1 for (a, b) in up if key.is_red(a, b))
    return red, total


def z_score_binomial(positives, total, p=0.5):
    return (positives - total * p) / (total * p * (1 - p)) ** 0.5


def _check_gates(tokens):
    """Mimic the real server's 400 rejections (raise, like a failed query)."""
    if len(tokens) < MIN_TOKENS:
        raise ValueError("reject: You must provide at least 50 tokens for watermark detection.")
    if len(unique_pairs(tokens)) < MIN_PAIRS:
        raise ValueError("reject: too repetitive; need >= 50 distinct consecutive pairs.")


def detect_pvalue(tokens, key: WatermarkKey) -> float:
    _check_gates(tokens)
    red, total = red_total(tokens, key)
    z = z_score_binomial(red, total, 0.5)
    return float(1 - norm.cdf(-z))


def detect_bool(tokens, key: WatermarkKey) -> int:
    return 1 if detect_pvalue(tokens, key) < 0.01 else 0


def Rstar(N):
    """Largest red count with bool==1 for N pairs.  R*(50)=16, R*(51)=R*(52)=17."""
    return max((R for R in range(N + 1) if z_score_binomial(R, N) < THRESH_Z), default=-1)


# ----------------------------------------------------------------------------
# Build realistic watermarked sample strings (green-heavy => detected)
# ----------------------------------------------------------------------------
def make_watermarked_string(key, rng, length, green_prob, tok_lo=0, tok_hi=40000):
    """Greedy green-promoting generation, like the watermarked model output."""
    toks = [int(rng.integers(tok_lo, tok_hi))]
    while len(toks) < length:
        prev = toks[-1]
        if rng.random() < green_prob:
            for _ in range(64):                       # pick a green successor (red == False)
                cand = int(rng.integers(tok_lo, tok_hi))
                if not key.is_red(prev, cand):
                    toks.append(cand)
                    break
            else:
                toks.append(int(rng.integers(tok_lo, tok_hi)))
        else:
            toks.append(int(rng.integers(tok_lo, tok_hi)))
    return np.array(toks, dtype=int)


# ============================================================================
# ATTACK PRIMITIVES (these only call the provided detection oracle functions)
# ============================================================================
FREE_LO, FREE_HI = 49000, 50277   # token id pool for filler/base/chain (avoid string toks)


def recover_red_count(tokens, pvalue_fn):
    """Recover exact integer red-pair count from p-value + local unique-pair total."""
    p = pvalue_fn(tokens)
    total = len(unique_pairs(tokens))
    if total == 0:
        return 0, 0
    p_clipped = min(max(p, 1e-15), 1 - 1e-15)         # z = ppf(p); clip to avoid +-inf
    z = norm.ppf(p_clipped)
    red = (z * (total ** 0.5) + total) / 2.0
    return int(round(red)), total


# ---------------- shared greedy red-path builder -----------------------------
def build_red_chain(edge_is_red, candidates, target_len):
    """Greedily build a simple path whose every consecutive edge is red, using a
    generic red-edge oracle `edge_is_red(a, b)`. Distinct nodes => distinct pairs."""
    chain = [candidates[0]]
    remaining = list(candidates[1:])
    while len(chain) < target_len and remaining:
        cur = chain[-1]
        nxt = None
        for i, w in enumerate(remaining):
            if edge_is_red(cur, w):
                nxt = i
                break
        if nxt is None:
            break
        chain.append(remaining.pop(nxt))
    return chain


# ---------------- p-value endpoint: red-edge oracle via count delta ----------
PV_FILLER = MIN_PAIRS + 2          # filler+[a] -> 52 distinct pairs (clears both gates)
def make_pvalue_edge_oracle(pvalue_fn, rng, used_tokens):
    """Return (edge_is_red, candidate_pool, get_qcount). Each edge (a,b) is tested
    on a constant fresh filler so p stays ~0.5: is_red(a,b) <=> p(filler+[a,b]) up."""
    pool = [t for t in range(FREE_LO, FREE_HI) if t not in used_tokens]
    rng.shuffle(pool)
    pool = list(pool)
    filler = pool[:PV_FILLER]
    candidates = pool[PV_FILLER:]
    base_red_cache = {}
    nq = [0]

    def edge_is_red(a, b):
        if a not in base_red_cache:
            r, _ = recover_red_count(filler + [a], pvalue_fn); nq[0] += 1
            base_red_cache[a] = r
        r2, _ = recover_red_count(filler + [a, b], pvalue_fn); nq[0] += 1
        return r2 == base_red_cache[a] + 1

    return edge_is_red, candidates, (lambda: nq[0])


# ---------------- bool endpoint: red-edge oracle via a balanced base B --------
def make_bool_edge_oracle(bool_fn, rng, used_tokens, n_candidates):
    """Return (edge_is_red, candidates, get_qcount, info).

    The >=50-distinct-pair gate rules out tiny cycles, so we anchor at the
    threshold with a FULL-SIZE base B: a 50-distinct-pair path with EXACTLY 17
    reds (R*(51)=R*(52)=17), confirmed by MIXED bool over B+[a] (only R(B)=17
    yields both 0 and 1 -> certain). Then:
      * B+[a]   (51 pairs): bool==1 <=> (B_end,a) GREEN  -> "green-connected" a.
      * B+[a,b] (52 pairs, a green-connected): R = 17 + red(a,b)
            -> bool==0 <=> (a,b) RED.  One query, both directions."""
    nq = [0]
    def Q(toks):
        nq[0] += 1
        return bool_fn(toks)

    pool0 = [t for t in range(FREE_LO, FREE_HI) if t not in used_tokens]
    B, gc_seed = None, []
    for _ in range(20000):
        cand = [int(x) for x in rng.choice(pool0, size=51, replace=False)]  # 50 pairs
        if Q(cand) == 1:                              # bool(B)==1 => R(B)<=16; want 17
            continue
        rest = [t for t in pool0 if t not in cand]
        cs = [int(x) for x in rng.choice(rest, size=6, replace=False)]
        res = [Q(cand + [c]) for c in cs]             # B+[c] = 51 pairs
        if 0 in res and 1 in res:                     # mixed => R(B)=17 (certain)
            B = cand
            gc_seed = [cs[i] for i, r in enumerate(res) if r == 1]
            break
    if B is None:
        raise RuntimeError("could not bootstrap a balanced base")
    B_end = B[-1]

    candidates = list(gc_seed)
    rest = [t for t in pool0 if t not in B and t not in candidates]
    rng.shuffle(rest)
    for a in rest:
        if len(candidates) >= n_candidates:
            break
        if Q(B + [a]) == 1:                           # (B_end, a) green
            candidates.append(a)

    def edge_is_red(a, b):
        return Q(B + [a, b]) == 0                      # 52 pairs; bool0 <=> (a,b) red

    return edge_is_red, candidates, (lambda: nq[0]), dict(B_end=B_end, base_pairs=len(B) - 1)


# ---------------- universal-suffix length minimisation -----------------------
def minimal_suffix_len(strings, chain, detect_fn, passes):
    """Smallest prefix length L of `chain` s.t. appending chain[:L] makes every
    string pass (`passes(value)` True). Binary search (monotone in L)."""
    lo, hi = 1, len(chain)
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        suf = chain[:mid]
        if all(passes(detect_fn(list(s) + suf)) for s in strings):
            best = mid; hi = mid - 1
        else:
            lo = mid + 1
    return best


# ============================================================================
# VALIDATION
# ============================================================================
def main():
    print(f"Rstar: 50->{Rstar(50)}  51->{Rstar(51)}  52->{Rstar(52)}")
    rng = np.random.default_rng(0)

    for endpoint, green_prob in [("pvalue", 0.80), ("bool", 0.80)]:
        key = WatermarkKey(seed=1234 if endpoint == "pvalue" else 9876)
        strings = [make_watermarked_string(key, rng, length=int(rng.integers(80, 110)),
                                            green_prob=green_prob) for _ in range(7)]
        string_toks = set(int(t) for s in strings for t in s)

        zs = [red_total(s, key) for s in strings]
        worst = max(t - 2 * r for r, t in zs)
        print(f"\n===== endpoint={endpoint} =====")
        print(f"  strings: lens={[len(s) for s in strings]}, worst green_excess={worst}")

        if endpoint == "pvalue":
            pv = lambda toks: detect_pvalue(toks, key)
            edge_is_red, candidates, qcount = make_pvalue_edge_oracle(pv, rng, string_toks)
            chain = build_red_chain(edge_is_red, candidates, target_len=130)
            passes, detect_fn = (lambda p: p >= 0.01), pv
        else:
            bl = lambda toks: detect_bool(toks, key)
            edge_is_red, candidates, qcount, info = make_bool_edge_oracle(
                bl, rng, string_toks, n_candidates=160)
            print(f"  balanced base: {info}, green-connected candidates={len(candidates)}")
            chain = build_red_chain(edge_is_red, candidates, target_len=130)
            passes, detect_fn = (lambda b: b == 0), bl

        allred = all(key.is_red(chain[i], chain[i + 1]) for i in range(len(chain) - 1))
        L = minimal_suffix_len(strings, chain, detect_fn, passes)
        print(f"  chain len={len(chain)}, all_edges_red={allred}, discovery queries~{qcount()}")
        print(f"  MIN universal suffix length = {L}")
        if L:
            vals = [detect_fn(list(s) + chain[:L]) for s in strings]
            if endpoint == "pvalue":
                print(f"  per-string p-values: min={min(vals):.4f} (all>=0.01: {all(v>=0.01 for v in vals)})")
            else:
                print(f"  per-string bool: {vals} (all 0: {all(v==0 for v in vals)})")


if __name__ == "__main__":
    main()
