import json, sys, os

# Defaults: build ../Q2_code.ipynb from the clean template next to this script.
# Override with:  python3 make_nb.py <template.ipynb> <out.ipynb>
_HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_HERE, "Q2_code.backup.ipynb")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(_HERE, "..", "Q2_code.ipynb")
nb = json.load(open(TEMPLATE))


_idc = [0]
def _nid():
    _idc[0] += 1
    return f"q2attack{_idc[0]:02d}"

def code(src):
    return {"cell_type": "code", "id": _nid(), "metadata": {}, "execution_count": None,
            "outputs": [], "source": src if isinstance(src, list) else src.splitlines(keepends=True)}


def md(src):
    return {"cell_type": "markdown", "id": _nid(), "metadata": {},
            "source": src if isinstance(src, list) else src.splitlines(keepends=True)}


ATTACK_INTRO = r'''# Breaking the watermark (attack)

**Detection.** The API counts the **unique** consecutive token pairs `(s_{t-1}, s_t)`
(duplicates skipped). With `red` = #unique pairs whose 2nd token is in the red list
seeded by the 1st token and `total` = #unique pairs,

```
z = (red - total/2) / sqrt(total * 0.5 * 0.5)
p = 1 - norm.cdf(-z)            #  == norm.cdf(z)
watermarked  <=>  p < 0.01  <=>  z < norm.ppf(0.01) ~= -2.326
```

Watermarked text is **green-heavy** (`red` small) so `z` is very negative.

**Attack.** Append a suffix whose every internal pair `(c_i, c_{i+1})` is **red**.
Appending `L` tokens adds `L` new unique pairs (the boundary pair
`(last_string_token, c_1)` plus `L-1` internal pairs). If the internal pairs are red,
`2*red - total` moves from `-G` to `-G + L - 2 + 2b`, where `G = total - 2*red` is the
string's **green-excess** and `b in {0,1}` is whether the boundary pair is red. So

```
minimal L  ~=  G - 2.326*sqrt(total + L) + (2 - 2b).
```

Because the watermark key is fixed within a subtask, a red pair is red for **every**
string, so one red chain is a **universal** suffix. We take suffix tokens from ids in
no string, so the suffix never reuses an existing pair.

This means: **the shortest possible suffix is fixed by your strings' green-excess `G`**
(reported below). The two levers we control are (i) making every internal pair red
(optimal — 1 red pair per token is the information-theoretic max) and (ii) the boundary
pair: choosing `c_1` so `(last_token, c_1)` is red on the binding (largest-`G`) string
saves ~1-2 tokens — the "tedious last point". We do both.

**Server gates.** Every query must have **>=50 tokens AND >=50 distinct consecutive
pairs** ("too repetitive" otherwise). So all probes below are all-distinct-token paths.

Red-pair discovery per endpoint:
* **p-value** — append to a 52-distinct-token filler (keeps `p ~ 0.5`): pair `(a,b)` is
  red **iff the p-value goes up**. We also recover each string's exact `red`/`total`/`G`.
* **boolean** — only 0/1, so we anchor at the threshold with a reusable **balanced base
  `B`**: a 50-distinct-pair path with exactly 17 reds (`R*(51)=R*(52)=17`). Then
  `B+[a]` (51 pairs) is `bool=1` iff `(B_end,a)` is green (certifies "green-connected"
  candidates), and for those, `B+[a,b]` (52 pairs) has `R=17+red(a,b)`, so `bool=0` iff
  `(a,b)` is red — one query certifies any edge.
'''

ATTACK_HELPERS = r'''# ============================================================================
# Attack primitives
# ============================================================================
import time, random, datetime
from scipy.stats import norm

VOCAB = 50277                      # valid token ids are [0, 50277): i.e. 0 .. 50276
MIN_TOKENS = 50                    # server rule 1: ">= 50 tokens for watermark detection"
MIN_PAIRS = 50                     # server rule 2: ">= 50 DISTINCT consecutive pairs"
                                   #   ("too repetitive" otherwise). This rule rules out any
                                   #   small-unique-pair probe; every probe needs >= 50
                                   #   *distinct* pairs, not just >= 50 tokens.
THRESHOLD_Z = norm.ppf(0.01)       # ~ -2.3263
random.seed(0)

def Rstar(N):
    """Largest red-pair count R for which N pairs are still flagged watermarked
    (bool==1): bool==1 <=> z=(R-N/2)/sqrt(N/4) < ppf(0.01).  R*(50)=16, R*(51)=R*(52)=17."""
    return max((R for R in range(N + 1) if (R - N / 2) / (N * 0.25) ** 0.5 < THRESHOLD_Z), default=-1)


# ---- logging: append the entire run to a timestamped log file ---------------
LOG_PATH = "Q2_run.log"
_log_fh = open(LOG_PATH, "a")
def log(msg, echo=True):
    """Append msg to Q2_run.log with a timestamp on EVERY content line (flushed);
    also print it if echo. Per-line stamping keeps any leading/embedded newline
    tidy (blank lines stay blank, content lines always carry a timestamp)."""
    stamp = f"[{datetime.datetime.now().isoformat(timespec='seconds')}] "
    _log_fh.write("\n".join((stamp + ln) if ln else "" for ln in str(msg).split("\n")) + "\n")
    _log_fh.flush()
    if echo:
        print(msg)
log("================ run start ================", echo=False)


# ---- persistent query cache: never re-spend quota on a repeated query -------
# Every (endpoint, token-string) -> response is appended to a JSONL log and
# reloaded on startup. Re-running the notebook (after a crash / rate-limit / to
# tweak the build logic) replays cached answers for FREE and only sends queries
# that are genuinely new. The build is seeded (random.seed(0) below) so a clean
# re-run reissues the exact same probes -> 100% cache hits, 0 quota spent.
CACHE_PATH = "Q2_query_cache.jsonl"   # Colab tip: set to a path under your mounted
                                      # Drive folder to persist across runtimes.
_CACHE = {}
_API_STATS = {"new_pvalue": 0, "new_bool": 0, "cache_hits": 0}

# Optional per-session live-query caps. Cached responses do not count.
# Set either value to 100 (or another integer) before a live run if you want the
# notebook to stop, with cache/state already flushed, instead of spending more
# new queries in the current kernel session. Leave as None to run to completion.
MAX_NEW_PVALUE_QUERIES = None
MAX_NEW_BOOL_QUERIES = None

def _query_limit(endpoint):
    return MAX_NEW_PVALUE_QUERIES if endpoint == "pvalue" else MAX_NEW_BOOL_QUERIES

def _load_cache():
    import os
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    _CACHE[(rec["endpoint"], rec["tokens"])] = rec["resp"]
                except Exception:
                    pass
    log(f"query cache: loaded {len(_CACHE)} stored results from {CACHE_PATH}")

_load_cache()
_cache_fh = open(CACHE_PATH, "a")     # append handle kept open for the session

def _cache_put(endpoint, tokens_str, resp):
    _CACHE[(endpoint, tokens_str)] = resp
    _cache_fh.write(json.dumps({"endpoint": endpoint, "tokens": tokens_str, "resp": resp}) + "\n")
    _cache_fh.flush()                 # crash-safe: each answer hits disk immediately


# ---- API wrappers: cached + logged, return the `wmark_response` value --------
def _call(fn, tokens_list):
    s = tokens_to_string(np.asarray(tokens_list, dtype=int))
    endpoint = "pvalue" if fn is get_pvalue else "bool"
    ntok = len(tokens_list)
    npairs = len({(int(tokens_list[i - 1]), int(tokens_list[i])) for i in range(1, ntok)})
    hit = _CACHE.get((endpoint, s))
    if hit is not None:               # cached (note: 0/0.0 are valid, not misses)
        _API_STATS["cache_hits"] += 1
        log(f"q  cache {endpoint:6s} ntok={ntok:4d} npairs={npairs:4d} -> {hit}", echo=False)
        return hit

    limit = _query_limit(endpoint)
    if limit is not None and _API_STATS["new_" + endpoint] >= int(limit):
        save_state()
        _cache_fh.flush()
        _pairs_fh.flush()
        log(f"STOP: reached {endpoint} live-query cap ({limit}). "
            "Progress is saved in Q2_query_cache.jsonl, Q2_known_pairs.jsonl, and Q2_state.json.")
        raise RuntimeError(
            f"Stopped after {limit} new {endpoint} queries in this session. "
            "Restart/rerun later to continue from saved cache/state, or raise the cap."
        )

    try:
        resp = fn(s)
        if isinstance(resp, dict) and "wmark_response" in resp:
            val = resp["wmark_response"]
            _cache_put(endpoint, s, val)
            _API_STATS["new_" + endpoint] += 1
            log(f"q  NEW#{_API_STATS['new_' + endpoint]:<4d} {endpoint:6s} "
                f"ntok={ntok:4d} npairs={npairs:4d} -> {val}", echo=False)
            return val
    except Exception as e:
        resp = repr(e)

    save_state()
    _cache_fh.flush()
    _pairs_fh.flush()
    log(f"ERROR: {endpoint} API call failed; progress is saved. Last response: {resp}")
    raise RuntimeError(f"{endpoint} API call failed. Last response: {resp}")

def pv(tokens_list):  return float(_call(get_pvalue, tokens_list))   # p-value endpoint
def bl(tokens_list):  return int(_call(get_bool,    tokens_list))    # boolean endpoint

def print_quota_usage():
    log(f"this session: {_API_STATS['new_pvalue']} new p-value + "
        f"{_API_STATS['new_bool']} new bool queries sent; "
        f"{_API_STATS['cache_hits']} served from cache "
        f"({len(_CACHE)} total stored).")


# ---- run checkpoint: persist solver progress so phases resume after a crash --
# The query cache above already makes every individual query free on a re-run.
# This additionally records finished artifacts (balanced base, candidates, the
# discovered suffixes) so a restarted run SKIPS completed phases and never loses
# a found answer. Delete Q2_state.json to force a clean recompute.
STATE_PATH = "Q2_state.json"

def _load_state():
    import os
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

STATE = _load_state()

def save_state():
    import os
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(STATE, f)
    os.replace(tmp, STATE_PATH)        # atomic: never leaves a half-written file

def checkpoint(**kw):
    """Update STATE and persist atomically -- called at fine granularity (after
    each attached candidate / each chain edge / each finished start) so a crash
    loses nothing and a resume continues from the exact point."""
    STATE.update(kw)
    save_state()

if STATE:
    log(f"checkpoint: resumed run state with keys {sorted(STATE)}")


# ---- known-pair store: record EVERY pair colour proved with certainty --------
# Both endpoints reveal pair colours with 100% certainty (p-value: p goes up/down;
# bool: the balanced base B isolates a single edge, and B+[a] proves GREEN
# in-edges (B_end,a)). We persist red AND green facts, namespaced by endpoint
# (the two endpoints use DIFFERENT watermark keys, so a pair's colour differs
# between them). This lets the oracles answer a known pair with ZERO queries --
# even across runs where the base B changed (the query cache is keyed by probe
# string and would miss those; this is keyed by the pair).
# NOTE: tied to your API key/token set; delete the file if you switch keys.
PAIRS_PATH = "Q2_known_pairs.jsonl"
KNOWN_PAIRS = {}                          # (endpoint, a, b) -> True (red) / False (green)

def _load_pairs():
    import os
    if os.path.exists(PAIRS_PATH):
        with open(PAIRS_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    KNOWN_PAIRS[(r["ep"], r["a"], r["b"])] = r["red"]
                except Exception:
                    pass
    nred = sum(1 for v in KNOWN_PAIRS.values() if v)
    log(f"known pairs: loaded {len(KNOWN_PAIRS)} ({nred} red, {len(KNOWN_PAIRS) - nred} green) from {PAIRS_PATH}")

_load_pairs()
_pairs_fh = open(PAIRS_PATH, "a")

def known_pair(endpoint, a, b):
    return KNOWN_PAIRS.get((endpoint, int(a), int(b)))     # True / False / None

def mark_pair(endpoint, a, b, is_red):
    """Record a CERTAIN pair colour (idempotent, crash-safe append)."""
    key = (endpoint, int(a), int(b))
    if key in KNOWN_PAIRS:
        return
    KNOWN_PAIRS[key] = bool(is_red)
    _pairs_fh.write(json.dumps({"ep": endpoint, "a": int(a), "b": int(b), "red": bool(is_red)}) + "\n")
    _pairs_fh.flush()

def print_known_pairs():
    for ep in ("pvalue", "bool"):
        reds = sum(1 for (e, a, b), v in KNOWN_PAIRS.items() if e == ep and v)
        grns = sum(1 for (e, a, b), v in KNOWN_PAIRS.items() if e == ep and not v)
        log(f"  known {ep} pairs: {reds} red, {grns} green")


def unique_pairs(tokens):
    return {(int(tokens[i - 1]), int(tokens[i])) for i in range(1, len(tokens))}


# ---- free token pool: ids that appear in NO string of EITHER endpoint -------
_all_string_tokens = set()
for _t in tokens:
    _all_string_tokens |= set(int(x) for x in tokens_to_array(_t["tokens"]))
FREE = [i for i in range(VOCAB) if i not in _all_string_tokens]
random.shuffle(FREE)
log(f"free token pool size = {len(FREE)} (excluded {len(_all_string_tokens)} string tokens)")


# ---- exact red count of a sequence from its p-value (p == norm.cdf(z)) ------
def recover_red_count(tokens_list):
    p = pv(tokens_list)
    total = len(unique_pairs(tokens_list))
    if total == 0:
        return 0, 0
    pc = min(max(p, 1e-15), 1 - 1e-15)
    z = norm.ppf(pc)
    return int(round((z * total ** 0.5 + total) / 2.0)), total


def theoretical_min_L(G, T, b):
    """Smallest suffix length flipping a string with green-excess G, total T and
    boundary-pair colour b (1=red). z_new = (L-2+2b-G)/sqrt(T+L) >= THRESHOLD_Z."""
    L = 1
    while not (L - 2 + 2 * b - G >= THRESHOLD_Z * (T + L) ** 0.5):
        L += 1
        if L > VOCAB:
            break
    return L


# ---- chain building ---------------------------------------------------------
def grading_check_lengths(endpoint, est=None, cap=140):
    """Lengths at which to test all 7 strings during the build. Taken from the
    assignment's SCORE breakpoints (p-value 40/41/42/50, bool 41/42/43/50; both
    flatten past 50) plus the analytic minimum (when known) and a coarse cadence.
    This is FAR fewer probes than testing every few edges. The trim afterwards
    still recovers the exact minimal length, so this never changes the score --
    it only controls how many (string x suffix) queries the build spends."""
    pts = {40, 41, 42, 50} if endpoint == "pvalue" else {41, 42, 43, 50}
    if est:                                            # tight lower bound is known
        pts |= set(range(max(1, est), est + 3))        # expect to pass at the analytic min
    pts |= set(range(24, 50, 8))                       # low anchors -> small minima stop early
    pts |= set(range(50, cap + 1, 10))                 # >50: score ~flat -> sparse
    pts.add(cap)
    return sorted(p for p in pts if 1 <= p <= cap)

# Only run the exact-minimal trim (minimal_prefix) when the suffix length lands in
# the SCORING SLOPE where shorter actually means a better grade (p-value 42..50,
# bool 43..50). In the full-marks plateau (<=41 / <=42) or the low-score tail (>50)
# the trim cannot improve the grade, so we skip it and save those string-queries.
# Set this False to always trim to the exact minimum (more queries, same grade).
TRIM_ONLY_IN_SCORING_SLOPE = True
def scoring_slope(endpoint):
    return (42, 50) if endpoint == "pvalue" else (43, 50)

def grow_red_chain(edge_is_red, candidates, start, strings, detect_fn, passes,
                   check_lengths=None, hard_cap=140, progress_key=None):
    """Greedily grow a simple path of red edges from `start`; rejected candidates
    are kept (a green successor of one node may be red for the next). Test all
    `strings` only when the chain length hits one of `check_lengths` (the grading
    boundaries), and stop once they all pass. Returns (chain, flipped) where
    `flipped` is True iff a check confirmed all strings flip at len(chain).

    If `progress_key` is given, the partial chain is checkpointed to STATE after
    EVERY accepted edge, and a matching partial (same `start`) is resumed -- so an
    interrupted build continues from the exact token it stopped at."""
    chain = [start]
    if progress_key:                                  # resume the in-progress chain
        part = STATE.get(progress_key + "_partial")
        if part and part[0] == start:
            chain = list(part)
            log(f"    resumed partial {progress_key} chain at length {len(chain)}", echo=False)
    remaining = [c for c in candidates if c not in chain]
    checkset = set(check_lengths or ())
    flipped = False
    while len(chain) < hard_cap and remaining:
        cur = chain[-1]; hit = None
        for i, w in enumerate(remaining):
            if edge_is_red(cur, w):
                hit = i; break
        if hit is None:
            break
        chain.append(remaining.pop(hit))
        if progress_key:
            checkpoint(**{progress_key + "_partial": chain})   # save after each edge
        if len(chain) in checkset:                    # check only at grading boundaries
            if all(passes(detect_fn(list(s) + chain)) for s in strings):
                flipped = True
                break
    return chain, flipped

def minimal_prefix(chain, strings, detect_fn, passes, hi_cap=None):
    """Smallest prefix length L of `chain` that flips all strings (monotone in L).
    Tests the cap first: if `chain[:hi_cap]` doesn't already flip, returns None in
    one check (lets best_suffix cheaply reject a start that can't beat the best)."""
    top = len(chain) if hi_cap is None else min(len(chain), hi_cap)
    if top < 1 or not all(passes(detect_fn(list(s) + chain[:top])) for s in strings):
        return None
    lo, hi, best = 1, top, top
    while lo <= hi:
        mid = (lo + hi) // 2
        if all(passes(detect_fn(list(s) + chain[:mid])) for s in strings):
            best = mid; hi = mid - 1
        else:
            lo = mid + 1
    return best

def best_suffix(edge_is_red, candidates, strings, detect_fn, passes, starts,
                check_lengths=None, trim_range=None, full_marks_at=None,
                hard_cap=140, progress_key=None):
    """Build a red chain from each start token and keep the globally shortest suffix.

    The exact-minimal trim (minimal_prefix) is run ONLY when the built flip length
    falls in `trim_range` (the scoring slope) -- elsewhere shortening cannot improve
    the grade, so we keep the built length and skip the trim queries. Other savings:
    the build checks only at `check_lengths` (grading boundaries); once a best length
    is known, later starts are built no longer than best+1; and once a FULL-MARKS
    length (`full_marks_at`) is reached we stop trying more starts (no better grade
    is possible).

    Successful starts are checkpointed per start; a resume reuses the chain and (in
    the trim range) re-runs minimal_prefix against the CURRENT cache."""
    done = (STATE.get(progress_key + "_starts") if progress_key else None) or {}
    best_chain, best_len = None, None
    for k, st in enumerate(starts):
        cap = hard_cap if best_len is None else min(hard_cap, best_len + 1)
        if str(st) in done:                                  # built on a prior run
            chain, flipped = done[str(st)]["chain"], True
        else:
            chain, flipped = grow_red_chain(edge_is_red, candidates, st, strings, detect_fn,
                                            passes, check_lengths=check_lengths, hard_cap=cap,
                                            progress_key=progress_key)
            if progress_key and flipped:                     # only checkpoint working starts
                done[str(st)] = {"chain": chain}
                checkpoint(**{progress_key + "_starts": done})
        if not flipped:
            log(f"    start {k + 1}/{len(starts)}: no flipping suffix within cap {cap}")
            continue
        L_built = len(chain)
        if trim_range and trim_range[0] <= L_built <= trim_range[1]:
            hi_cap = None if best_len is None else best_len - 1   # only accept strictly shorter
            L = minimal_prefix(chain, strings, detect_fn, passes, hi_cap=hi_cap)
            note = f"trimmed -> {L}" if L is not None else "not shorter than best"
        else:                                                # plateau or low-score tail: no trim
            L = L_built if (best_len is None or L_built < best_len) else None
            note = f"flips at {L_built} (trim skipped: outside scoring slope)"
        log(f"    start {k + 1}/{len(starts)}: built {L_built} -> {note}")
        if L is not None and (best_len is None or L < best_len):
            best_len, best_chain = L, chain[:L]
        if full_marks_at is not None and best_len is not None and best_len <= full_marks_at:
            log(f"    full marks at length {best_len} (<= {full_marks_at}); stopping start search")
            break
    return best_chain, best_len


# ---- p-value red-edge oracle: red(a,b) <=> p(filler+[a,b]) > p(filler+[a]) ---
# filler = 52 distinct tokens => filler+[a] has 52 distinct pairs, filler+[a,b] 53;
# both clear BOTH gates (>=50 tokens AND >=50 distinct pairs).
FILLER_LEN = MIN_PAIRS + 2
def make_pvalue_oracle():
    filler = FREE[:FILLER_LEN]
    base_p = {}
    def is_red(a, b):
        k = known_pair("pvalue", a, b)            # answer for free if already proved
        if k is not None:
            return k
        if a not in base_p:
            base_p[a] = pv(filler + [a])
        r = pv(filler + [a, b]) > base_p[a]        # red <=> p strictly increases
        mark_pair("pvalue", a, b, r)
        return r
    return is_red, FREE[FILLER_LEN:]


# ---- boolean red-edge oracle via a reusable balanced base B -----------------
# The >=50-distinct-pair gate rules out tiny repeated cycles, so we anchor at the
# detection threshold with a FULL-SIZE base instead. B is a 50-distinct-pair path
# with EXACTLY 17 reds (R*(51)=R*(52)=17). Then, appending tokens to B:
#   * B+[a]   (51 pairs): R = 17 + red(B_end,a)  -> bool==1 <=> (B_end,a) GREEN.
#       => certifies "green-connected" candidates a (a has a green in-edge from B_end).
#   * B+[a,b] (52 pairs) for a green-connected: R = 17 + red(a,b)
#       -> bool==0 <=> (a,b) RED.  One query certifies any edge, both directions.
# Every probe is an all-distinct path of >=53 tokens / >=51 distinct pairs -> accepted.
def bootstrap_base(max_bases=8000):
    """Find a 50-pair path B (51 distinct tokens) with EXACTLY 17 reds, and some
    green-connected seeds. Confirmed by MIXED bool over B+[a]: mixed <=> R(B)=17
    (R(B)=16 => all bool1; R(B)>=18 => all bool0), so this is certain."""
    free = [t for t in FREE]
    for _ in range(max_bases):
        B = random.sample(free, 51)                       # 51 tokens -> 50 distinct pairs
        if bl(B) == 1:                                     # bool(B)==1 => R(B)<=16, want 17
            continue
        rest = [t for t in free if t not in B]
        cands = random.sample(rest, 6)
        res = [bl(B + [c]) for c in cands]                 # B+[c] = 51 pairs
        if 0 in res and 1 in res:                          # mixed => R(B)=17 (certain)
            for c, r in zip(cands, res):
                mark_pair("bool", B[-1], c, r == 0)        # bool0 <=> (B_end,c) red
            gc = [c for c, r in zip(cands, res) if r == 1]
            return B, gc
    raise RuntimeError("could not bootstrap a balanced base (try rerunning)")

def bool_edge_oracle(B):
    """Red-edge oracle, rebuilt from a (possibly resumed) base B. Valid for `a`
    that is green-connected (B_end -> a green); we only ever call it for such a."""
    B_end = B[-1]
    def is_red(a, b):
        k = known_pair("bool", a, b)               # answer for free if already proved
        if k is not None:
            return k
        r = bl(B + [a, b]) == 0                     # 52 pairs; bool0 <=> (a,b) red
        mark_pair("bool", a, b, r)
        return r
    return is_red

def is_green_connected(B, a):
    """True iff (B_end, a) is green, i.e. bl(B+[a])==1 (B has R=17, so 51-pair probe)."""
    k = known_pair("bool", B[-1], a)
    if k is not None:
        return not k                                # known red => not green-connected
    g = bl(B + [a]) == 1
    mark_pair("bool", B[-1], a, not g)
    return g

def make_bool_oracle(n_candidates=150, B=None, gc=None):
    """Bootstrap (or resume) the base B, collect `n_candidates` green-connected
    nodes, and return (edge_is_red, candidates, B). The base is checkpointed as
    soon as it is found, and the candidate list is checkpointed incrementally, so
    a crash mid-bootstrap or mid-attach resumes without redoing finished work."""
    if B is None:
        B, gc = bootstrap_base()
        checkpoint(bool_base=list(B))                  # persist the (expensive) base at once
    candidates = list(gc or [])
    for a in FREE:
        if len(candidates) >= n_candidates:
            break
        if a in B or a in candidates:
            continue
        if is_green_connected(B, a):
            candidates.append(a)
            checkpoint(bool_candidates=list(candidates))   # persist after each attach
    return bool_edge_oracle(B), candidates, B
'''

PV_SOLVE = r'''# ---------------- Solve the p-value endpoint (boundary-optimized) -----------
pv_strings = [tokens_to_array(t["tokens"]).tolist() for t in pvalue_token_ids]

log("==== p-value endpoint ====", echo=False)
if "pvalue_suffix" in STATE:                          # resume: phase already finished
    if STATE.pop("pv_partial", None) is not None or STATE.pop("pv_starts", None) is not None:
        save_state()                                  # drop any stale transient progress
    YOUR_PVALUE_SUFFIX_LIST = STATE["pvalue_suffix"]
    Lp = len(YOUR_PVALUE_SUFFIX_LIST)
    log(f"resumed p-value suffix from checkpoint (length {Lp})")
else:
    is_red_pv, cand_pv = make_pvalue_oracle()

    # (a) exact green-excess of each raw string + theoretical minimal suffix length.
    log("raw p-value strings:")
    pv_meta = []
    for s in pv_strings:
        R, T = recover_red_count(s); G = T - 2 * R
        pv_meta.append((s, R, T, G, int(s[-1])))
        log(f"  red={R:3d}  total={T:3d}  green_excess={G:3d}  min L (boundary red)={theoretical_min_L(G, T, 1)}")
    worst_minL = max(theoretical_min_L(G, T, 1) for _, R, T, G, e in pv_meta)
    log(f"==> best achievable p-value suffix ~ {worst_minL} tokens  "
        f"({'<=41 reachable' if worst_minL <= 41 else 'G too high for <=41 -> aim for partial credit'})")

    # (b) Boundary optimisation -- THE lever that hits the score target. Making the
    #     boundary pair (last_token, c_1) RED saves ~2 tokens on a string. The binding
    #     set = strings whose b=0 requirement is within 2 of the max (any of them could
    #     become the limiter once boundaries flip). Find c_1 RED after ALL of them.
    reqs = [(theoretical_min_L(G, T, 0), e) for _, R, T, G, e in pv_meta]
    mx = max(r for r, e in reqs)
    binding_ends = [e for r, e in reqs if r >= mx - 2]
    log(f"binding strings: {len(binding_ends)} (optimizing the boundary pair on these)")

    starts, best_c, best_cov = [], cand_pv[0], -1
    for c in cand_pv[:500]:
        cov = 0
        for e in binding_ends:                           # short-circuit: stop at first green
            if is_red_pv(e, c):
                cov += 1
            else:
                break
        if cov > best_cov:
            best_cov, best_c = cov, c
        if cov == len(binding_ends):                     # boundary red for EVERY binding string
            starts.append(c)
            if len(starts) >= 3:
                break
    if not starts:                                       # best partial coverage (never a random b=0)
        starts = [best_c]
        log(f"WARNING: boundary only partially optimized ({best_cov}/{len(binding_ends)})")
    log(f"using {len(starts)} boundary-optimized start token(s) (full coverage of {len(binding_ends)} binding ends)")

    pv_cands = [c for c in cand_pv if c not in starts]
    YOUR_PVALUE_SUFFIX_LIST, Lp = best_suffix(
        is_red_pv, pv_cands, pv_strings, pv, lambda p: p >= 0.01, starts=starts,
        check_lengths=grading_check_lengths("pvalue", worst_minL),
        trim_range=(scoring_slope("pvalue") if TRIM_ONLY_IN_SCORING_SLOPE else (1, 140)),
        full_marks_at=40, hard_cap=140, progress_key="pv")
    assert Lp is not None, "p-value suffix did not flip all strings; raise hard_cap / pool size."
    STATE.pop("pv_starts", None); STATE.pop("pv_partial", None)   # transient progress -> drop
    checkpoint(pvalue_suffix=YOUR_PVALUE_SUFFIX_LIST)             # checkpoint the finished phase

ps = [pv(s + YOUR_PVALUE_SUFFIX_LIST) for s in pv_strings]   # cached -> free on re-run
log(f"\nP-VALUE SUFFIX LENGTH = {Lp}   per-string p-values: {[round(x, 4) for x in ps]}")
log(f"all >= 0.01: {all(x >= 0.01 for x in ps)} | target <=41: {Lp <= 41} | full marks <=40: {Lp <= 40}")
print_quota_usage(); print_known_pairs()
'''

BOOL_DIAG = r'''# ---------------- Precondition for the boolean attack -----------------------
# The boolean attack uses all-distinct-token probes (>=51 distinct pairs), which
# satisfy BOTH server gates (>=50 tokens AND >=50 distinct pairs). Verify here and
# HARD-ASSERT a well-formed response, so a gate rejection fails loudly right now
# instead of deep inside the bootstrap later.
_probe = FREE[:53]                                     # 53 distinct tokens -> 52 distinct pairs
_probe_str = tokens_to_string(np.array(_probe, dtype=int))
log(f"probe: tokens = {len(_probe)} | distinct pairs = {len(unique_pairs(_probe))}")

_cached = _CACHE.get(("bool", _probe_str))
if _cached is not None:
    _API_STATS["cache_hits"] += 1
    log(f"cached bool response = {_cached} (probe shape already confirmed accepted)")
else:
    _raw = get_bool(_probe_str)                        # single raw call -> exact response
    log(f"raw get_bool response: {_raw}")
    assert isinstance(_raw, dict) and "wmark_response" in _raw and _raw["wmark_response"] in (0, 1), (
        "Boolean API did not return a valid 'wmark_response' for a 53-token / 52-distinct-pair "
        f"probe (got: {_raw}). A gate rejection here ('at least 50 tokens' or 'at least 50 "
        "distinct consecutive pairs') means the probe shape is wrong -- STOP before the solver.")
    _cache_put("bool", _probe_str, _raw["wmark_response"])   # cache + reuse on re-runs
    _API_STATS["new_bool"] += 1                              # account it in the stats
    log(f"OK: all-distinct probes accepted (wmark_response = {_raw['wmark_response']}) "
        "-- safe to run the boolean solver.")
print_quota_usage()
'''

BOOL_SOLVE = r'''# ---------------- Solve the boolean endpoint (best-of-K starts) -------------
log("==== boolean endpoint ====", echo=False)
bool_strings = [tokens_to_array(t["tokens"]).tolist() for t in bool_token_ids]
if "bool_suffix" in STATE:                            # resume: phase already finished
    if STATE.pop("bool_partial", None) is not None or STATE.pop("bool_starts", None) is not None:
        save_state()                                  # drop any stale transient progress
    YOUR_BOOL_SUFFIX_LIST = STATE["bool_suffix"]
    Lb = len(YOUR_BOOL_SUFFIX_LIST)
    log(f"resumed bool suffix from checkpoint (length {Lb})")
else:
    if "bool_base" in STATE:                          # resume base (+ any partial candidates)
        bool_B = STATE["bool_base"]; cand_bl = STATE.get("bool_candidates", [])
        is_red_bl, cand_bl, bool_B = make_bool_oracle(B=bool_B, gc=cand_bl, n_candidates=160)
        log(f"resumed balanced base + {len(cand_bl)} green-connected candidates from checkpoint")
    else:
        is_red_bl, cand_bl, bool_B = make_bool_oracle(n_candidates=160)
    log(f"balanced base end: {bool_B[-1]} | green-connected candidates: {len(cand_bl)}")

    # Boundary optimisation. The boundary pair (last_token, c1) being RED saves ~2
    # tokens. With the balanced base we can DIRECTLY test a boundary pair (e, c1) for
    # any string-end e that is "green-connected" (B_end->e green): is_red_bl(e, c1) is
    # then valid. So we pick start tokens c1 that are RED after all green-connected
    # ends (deterministic b=1 there), and also keep some plain starts so the
    # best-of-K trim still covers any non-green-connected binding string.
    str_ends = [s[-1] for s in bool_strings]
    ge = [e for e in str_ends if is_green_connected(bool_B, e)]
    log(f"green-connected string ends: {len(ge)}/{len(str_ends)} (boundary directly testable on these)")
    bstarts = []
    for c in cand_bl:
        if ge and all(is_red_bl(e, c) for e in ge):      # boundary red after every testable end
            bstarts.append(c)
            if len(bstarts) >= 4:
                break
    extra = [c for c in cand_bl if c not in bstarts]
    starts = (bstarts + extra)[:10]                       # boundary-aware first, then best-of-K
    log(f"using {len(bstarts)} boundary-optimized + {len(starts) - len(bstarts)} fallback start token(s)")
    YOUR_BOOL_SUFFIX_LIST, Lb = best_suffix(
        is_red_bl, cand_bl, bool_strings, bl, lambda b: b == 0, starts=starts,
        check_lengths=grading_check_lengths("bool"),
        trim_range=(scoring_slope("bool") if TRIM_ONLY_IN_SCORING_SLOPE else (1, 140)),
        full_marks_at=41, hard_cap=140, progress_key="bool")
    assert Lb is not None, "bool suffix did not flip all strings; raise n_candidates (more candidates)."
    STATE.pop("bool_starts", None); STATE.pop("bool_partial", None)   # transient progress -> drop
    checkpoint(bool_suffix=YOUR_BOOL_SUFFIX_LIST)      # checkpoint the finished phase

bs = [bl(s + YOUR_BOOL_SUFFIX_LIST) for s in bool_strings]   # cached -> free on re-run
log(f"\nBOOL SUFFIX LENGTH = {Lb}   per-string bool: {bs}")
log(f"all 0: {all(b == 0 for b in bs)} | target <=42: {Lb <= 42} | full marks <=41: {Lb <= 41}")
print_quota_usage(); print_known_pairs()
'''

SET_SUFFIXES = r'''# Suffixes discovered by the attack cells above.
YOUR_PVALUE_SUFFIX = np.array(YOUR_PVALUE_SUFFIX_LIST, dtype=int)
YOUR_BOOL_SUFFIX   = np.array(YOUR_BOOL_SUFFIX_LIST,   dtype=int)

assert isinstance(YOUR_PVALUE_SUFFIX, np.ndarray) and isinstance(YOUR_PVALUE_SUFFIX[0], np.int64)
assert isinstance(YOUR_BOOL_SUFFIX,   np.ndarray) and isinstance(YOUR_BOOL_SUFFIX[0],   np.int64)
assert len(YOUR_PVALUE_SUFFIX) <= 140 and len(YOUR_BOOL_SUFFIX) <= 140
# Token-range guard: valid ids are [0, 50277). The local checker does NOT verify
# this, so an out-of-range id would pass locally but fail in grading.
for _name, _arr in [("pvalue", YOUR_PVALUE_SUFFIX), ("bool", YOUR_BOOL_SUFFIX)]:
    assert all(0 <= int(x) < 50277 for x in _arr), f"{_name} suffix has a token id outside [0, 50277)"
log(f"pvalue suffix length: {len(YOUR_PVALUE_SUFFIX)} | bool suffix length: {len(YOUR_BOOL_SUFFIX)}")
log(f"token-id range OK: pvalue [{int(YOUR_PVALUE_SUFFIX.min())}, {int(YOUR_PVALUE_SUFFIX.max())}], "
    f"bool [{int(YOUR_BOOL_SUFFIX.min())}, {int(YOUR_BOOL_SUFFIX.max())}]")
'''

# cached replacement for the template's "Validate your suffixes" cell
VALIDATE_CELL = r'''# Validate your suffixes against all 7 strings for each endpoint.
# Uses the cached pv()/bl() wrappers, so on a re-run (and even on the first run,
# since the solver cells already queried these exact string+suffix combinations)
# this costs ZERO new API queries.
pv_strings_v   = [tokens_to_array(t["tokens"]).tolist() for t in pvalue_token_ids]
bool_strings_v = [tokens_to_array(t["tokens"]).tolist() for t in bool_token_ids]

log("=== p-value suffix vs all 7 strings (need p >= 0.01) ===")
for i, s in enumerate(pv_strings_v):
    p = pv(s + [int(x) for x in YOUR_PVALUE_SUFFIX])
    log(f"  string {i + 1}/7: p = {p:.4f}   {'OK' if p >= 0.01 else 'STILL WATERMARKED'}")

log("\n=== bool suffix vs all 7 strings (need bool == 0) ===")
for i, s in enumerate(bool_strings_v):
    b = bl(s + [int(x) for x in YOUR_BOOL_SUFFIX])
    log(f"  string {i + 1}/7: bool = {b}   {'OK' if b == 0 else 'STILL WATERMARKED'}")

print_quota_usage()
'''

# local replacement for the Colab/Drive get_solution_path cell
EXPORT_SETUP = r'''# Local submission folder (replaces the Colab/Drive + git-clone template above).
import re
from pathlib import Path

# The number on your Legi (Student ID card), format 'dd-ddd-ddd'.
STUDENT_ID = "00-000-000"
assert re.match(r"^\d{2}-\d{3}-\d{3}$", STUDENT_ID), "Student ID should have the format 'dd-ddd-ddd'"

# Same folder name the grader expects; zip THIS folder for submission.
SOLUTIONS_PATH = Path(f"./llm_assignment_3_submission-{STUDENT_ID}")
SOLUTIONS_PATH.mkdir(parents=True, exist_ok=True)
log(f"saving solution files to: {SOLUTIONS_PATH.resolve()}")
'''

# ---------------------------------------------------------------- assemble
cells = nb["cells"]


def src_of(c):
    return "".join(c["source"])


# (a) comment out the two tutorial cells that spend a query every run
for c in cells:
    if c["cell_type"] == "code" and src_of(c).strip() in (
            'get_pvalue(pvalue_token_ids[0]["tokens"])',
            'get_bool(bool_token_ids[0]["tokens"])'):
        c["source"] = ["# (disabled to save API quota — this was a one-off tutorial call;\n",
                       "#  re-enable manually if you want to sanity-check the raw API once)\n",
                       "# " + src_of(c).strip() + "\n"]

# (b) drop the Colab git-clone / rm -rf llm_lab cell entirely (local work)
cells = [c for c in cells if not (c["cell_type"] == "code" and "git clone" in src_of(c))]

# (c) localise the "save your results" prose
for c in cells:
    if c["cell_type"] == "markdown" and src_of(c).startswith("To save your results"):
        c["source"] = ["Set your `STUDENT_ID` below. The files are written to a local\n",
                       "`llm_assignment_3_submission-<STUDENT_ID>/` folder — zip that folder for Moodle.\n"]

# (d) replace the get_solution_path cell with the local path setup
for i, c in enumerate(cells):
    if c["cell_type"] == "code" and "get_solution_path" in src_of(c):
        cells[i] = code(EXPORT_SETUP)
        break

# (e) replace the template validation cell with the cached version
for i, c in enumerate(cells):
    if c["cell_type"] == "code" and src_of(c).startswith("# Validate your suffixes against all 7 strings"):
        cells[i] = code(VALIDATE_CELL)
        break

# (f) insert the attack block before the "# Export your solution" markdown
new_block = [md(ATTACK_INTRO), code(ATTACK_HELPERS),
             md("### Solve the p-value endpoint"), code(PV_SOLVE),
             md("### Solve the boolean endpoint"), code(BOOL_DIAG), code(BOOL_SOLVE)]
ins = next(i for i, c in enumerate(cells)
           if c["cell_type"] == "markdown" and src_of(c).startswith("# Export your solution"))
cells[ins:ins] = new_block

# (g) replace the "Set your suffixes here" NotImplementedError cell
for i, c in enumerate(cells):
    if c["cell_type"] == "code" and src_of(c).startswith("# Set your suffixes here"):
        cells[i] = code(SET_SUFFIXES)
        break

nb["cells"] = cells
json.dump(nb, open(OUT, "w"), indent=1)
print("wrote", OUT, "with", len(cells), "cells")
