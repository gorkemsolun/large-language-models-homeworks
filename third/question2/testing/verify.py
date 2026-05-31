"""Adversarial verification of the Q2 attack primitives."""
import numpy as np
from sim import (WatermarkKey, detect_pvalue, detect_bool, red_total, z_score_binomial,
                 make_watermarked_string, make_pvalue_edge_oracle, make_bool_edge_oracle,
                 build_red_chain, minimal_suffix_len)

# ---- 1) edge-oracle accuracy vs ground truth ----
print("=== edge-oracle accuracy (vs secret key) ===")
for ep in ["pvalue", "bool"]:
    key = WatermarkKey(seed=42 if ep == "pvalue" else 24)
    rng = np.random.default_rng(7)
    used = set()
    if ep == "pvalue":
        oracle, cands, qc = make_pvalue_edge_oracle(lambda t: detect_pvalue(t, key), rng, used)
    else:
        oracle, cands, qc, gad = make_bool_edge_oracle(lambda t: detect_bool(t, key), rng, used, 120)
    # sample pairs from candidate pool, compare oracle vs truth
    wrong = 0; n = 0
    for i in range(0, (len(cands) // 2) * 2, 2):
        a, b = cands[i], cands[i + 1]
        pred = oracle(a, b); truth = key.is_red(a, b)
        wrong += (pred != truth); n += 1
    print(f"  {ep}: {n} pairs tested, mismatches={wrong}, queries~{qc()}")

# ---- 2) difficulty sweep + full attack correctness ----
print("\n=== difficulty sweep (min suffix length, all-strings-flip, all-edges-red) ===")
for ep in ["pvalue", "bool"]:
    for gp in [0.55, 0.65, 0.75, 0.85]:
        for seed in [0, 1, 2]:
            key = WatermarkKey(seed=1000 * (ep == "bool") + seed)
            rng = np.random.default_rng(100 + seed)
            strings = [make_watermarked_string(key, rng, length=int(rng.integers(60, 130)),
                                               green_prob=gp) for _ in range(7)]
            stoks = set(int(t) for s in strings for t in s)
            if ep == "pvalue":
                oracle, cands, qc = make_pvalue_edge_oracle(lambda t: detect_pvalue(t, key), rng, stoks)
                detect, passes = (lambda t: detect_pvalue(t, key)), (lambda v: v >= 0.01)
            else:
                oracle, cands, qc, gad = make_bool_edge_oracle(lambda t: detect_bool(t, key), rng, stoks, 150)
                detect, passes = (lambda t: detect_bool(t, key)), (lambda v: v == 0)
            chain = build_red_chain(oracle, cands, target_len=140)
            allred = all(key.is_red(chain[i], chain[i + 1]) for i in range(len(chain) - 1))
            L = minimal_suffix_len(strings, chain, detect, passes)
            ok = L is not None and all(passes(detect(list(s) + chain[:L])) for s in strings)
            worst_z = min(z_score_binomial(*reversed(red_total(s, key)[:2][::-1])) for s in strings)
            wz = min(z_score_binomial(red_total(s, key)[0], red_total(s, key)[1]) for s in strings)
            print(f"  {ep} gp={gp} seed={seed}: worst_z={wz:6.2f}  minLen={L}  "
                  f"allEdgesRed={allred}  allFlip={ok}  q~{qc()}")
print("\nDONE")
