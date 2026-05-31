# Q2 testing harness

How the `../Q2_code.ipynb` watermark-breaking attack is built and verified **offline**,
without ever calling the real ETH API. The whole notebook is generated from a template +
attack code, and the *exact embedded code* is run against a local simulator of the API.

## Why a simulator
I can't (and shouldn't) spend your API quota while developing. So `sim.py` reproduces the
**exact** detection the server uses:
- detection counts **unique** consecutive token pairs (duplicates skipped);
- `z = (red - total/2)/sqrt(total/4)`, `p = 1 - norm.cdf(-z)`, watermarked ⇔ `p < 0.01`;
- a pair `(a,b)` is "red" iff a keyed hash of `(a,b)` says so (per-pair 50/50, as the API claims);
- **both server gates** are enforced: a query needs **≥50 tokens AND ≥50 distinct pairs**
  (discovered from your real-API tests — see `../test_6_pair_probe*`).

If an attack works against this simulator, it works against the real API (same math + gates).

## Files
| file | what it does |
|------|--------------|
| `sim.py` | The watermark simulator + the attack primitives (red-pair oracles, chain builder, p-value count recovery). Run it for a quick self-test of both endpoints. |
| `make_nb.py` | **Generates `../Q2_code.ipynb`** from `Q2_code.backup.ipynb` (the clean course template) by inserting the attack cells. This is the source of truth — edit the cell strings here, not the notebook. |
| `test_nb.py` | Extracts the **actual code cells** from `../Q2_code.ipynb` and runs them against the simulator (two keys, one per endpoint). Checks correctness, the query **cache**, the **resume** checkpoint, and the **known-pair** store. |
| `verify.py` | Adversarial sweep: edge-oracle accuracy vs ground truth + a difficulty sweep across watermark strengths/seeds. |
| `sim2.py` | Validates the **boundary optimization** / minimal suffix length on 131-token strings (matches your assigned string length). |
| `sim_bool2.py` | Standalone validation of the **boolean** discovery (balanced-base method) under both ≥50 gates. |
| `Q2_code.backup.ipynb` | The pristine course template `make_nb.py` builds from. |

## Run it
```bash
cd testing
python3 sim.py            # simulator self-test (pvalue + bool attacks)
python3 make_nb.py        # regenerate ../Q2_code.ipynb from the template + attack cells
python3 test_nb.py        # run the embedded notebook code against the simulator
python3 verify.py         # edge-oracle accuracy + difficulty sweep
python3 sim2.py           # boundary optimization on 131-token strings
python3 sim_bool2.py      # boolean balanced-base method under both gates
```
`make_nb.py` and `test_nb.py` default to `../Q2_code.ipynb`; both accept an explicit path arg.

Requires only `numpy` + `scipy`.

## What "passing" looks like
`test_nb.py` prints, for each endpoint, the discovered suffix and:
- `all_edges_red=True` — every suffix pair is genuinely red (per the simulator's secret key);
- `ALL_FLIP=True` — appending the suffix flips all 7 strings to non-watermarked;
- token ids in `[0, 50277)`, length ≤ 140, integer dtype;
- cache + resume re-runs send **0** new queries; every recorded known-pair colour matches ground truth.

## Suffix length and the score targets (≤41 / ≤42)
For a 131-token string with green-excess `G = total − 2·red`, the **shortest possible**
all-red suffix (with a red boundary pair) satisfies `(L − G)/√(total+L) ≥ ppf(0.01)`. Each
token adds at most one red pair, so this is the information-theoretic floor — no suffix beats it.
Solving it:
- **p-value suffix ≤ 41 tokens** is reachable **iff `G ≤ 71`**;
- **boolean suffix ≤ 42 tokens** is reachable **iff `G ≤ 72`**.

The single biggest lever is the **boundary pair** `(last_token, c₁)`: making it red saves ~2
tokens (e.g. 43→41). The notebook optimizes it on both endpoints (deterministically for p-value;
for boolean via the balanced base on green-connected ends, plus best-of-K). `test_nb.py` asserts
`achieved == boundary-optimized optimum` for p-value (and within 1 for boolean), i.e. it produces
the **shortest possible** suffix. Whether that optimum is ≤41/≤42 depends only on your strings'
`G` — the notebook prints it ("best achievable p-value suffix ~ X tokens"). If `G` exceeds the
bound above, no appended suffix can reach the target.

**Query-efficient stopping.** Several grading-aware cuts, none of which change the grade:
- the build tests all 7 strings only at the **score breakpoints** (p-value 40/41/42/50, bool
  41/42/43/50, both flat past 50) plus the analytic estimate and a coarse cadence — not every few edges;
- the exact-minimal **trim runs only in the scoring slope** (p-value `[42,50]`, bool `[43,50]`),
  where a shorter suffix means a better grade. In the full-marks plateau (≤40/≤41) or the low-score
  tail (>50), shortening can't change the grade, so the trim is skipped (`TRIM_ONLY_IN_SCORING_SLOPE`
  toggles this). Safe because the build always checks at 50, so a built length >50 means the true
  minimum is >50 — never a hidden good solution;
- once a **full-marks** length is reached (≤40 / ≤41) the start search stops early — no better grade
  is possible; and later starts are built no longer than `best+1`.

Together these cut the cold-run probes to ≈130 (p-value) / ≈800 (bool, dominated by the one-time
balanced-base bootstrap) in tests, down from ≈385 / ≈2200, with **identical grades**.

## Logging and resumability (what the notebook persists)
The notebook writes four working files (in the run directory; gitignored here):

- **`Q2_run.log`** — the entire run, timestamped: every API query (`endpoint / #tokens / #pairs / cached / response`) and every milestone (green-excess report, suffix lengths, quota). Useful for auditing what was sent and debugging.
- **`Q2_query_cache.jsonl`** — every `(endpoint, tokens) → response`, flushed after **each** query. Re-runs replay it for free → 0 new quota.
- **`Q2_state.json`** — solver checkpoint, written atomically at fine granularity: the balanced base as soon as found, candidates **incrementally** during attach, the partial chain after **each accepted edge**, and per-start results. A crash mid-build resumes from the exact point (the `FINE-GRAINED RESUME` test proves this).
- **`Q2_known_pairs.jsonl`** — every pair colour proved with certainty (red/green).

## Scratch files (gitignored)
Running the tests writes `Q2_query_cache.jsonl`, `Q2_state.json`, `Q2_known_pairs.jsonl`, `Q2_run.log`
here (simulator data, *not* your real-API data). They're ignored by git. Delete them to start cold.
