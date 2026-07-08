# DAV run-off — numerical / regime / rigor kit (`src/model/`)

Pure-stdlib estimators for the DAV run-off model (PLANv2 Phases 2–7). No
`numpy`/`pandas`/`sklearn`/`scipy`/`statsmodels` — everything hand-rolled on
`math`, `random`, `statistics`, `itertools`. Each module's `__main__` is a
validation against known ground truth.

**Run all self-tests:** `python run_tests.py`  →  14/14 modules pass.

These are validated on SYNTHETIC data (methodology proof). Deployment sign-off
needs the real DAV account panel (Layer 0, work PC) wired through `splitter` →
`hazard` → `survival`, plus the data-layer macro panel from `../data/`.

## Modules

| File | Role | Validation |
|---|---|---|
| `linalg.py` | OLS / ridge, Gaussian elimination, matrix inverse, classical SE | recovers (1.5, 2.0, −0.7) + σ |
| `hazard.py` | **penalized-logistic discrete-time hazard** (elastic-net, proximal grad), balance-weighted; the PLANv2 6.3 backbone | recovers known coefs; mean_pred≈actual; reliability monotone |
| `signatures.py` | truncated **path signatures** (Chen, time-aug/basepoint/lead-lag) — PLANv2 6.2 nonlinearity | line iterated integrals exact; Chen identity err 2e-16; triangle area +0.5 |
| `survival.py` | **S(t) aggregation** (balance-weighted) + reliability/PIT/Brier calibration | OOT ECE 0.013; PIT KS<crit; S(t) monotone |
| `ecm.py` | penalized **ECM** baseline + Newey–West **HAC SE** + block-bootstrap elasticity CI | rate elast −0.40, infl +0.80, φ<0 recovered, CI covers truth |
| `splitter.py` | **walk-forward + CPCV** (N=8,k=2→28 paths) with purge + embargo=H as τ-predicates | 28 paths; no-leakage assertion passes all paths |
| **Regime (autonomous, look-ahead-safe):** | | |
| `hmm_regime.py` | batch Gaussian **HMM** (Baum–Welch), filtered (causal) posterior | 98% filtered-state accuracy; means/persistence recovered |
| `online_hmm.py` | **online/adaptive HMM** — DYNAMIC transition matrix via exponential forgetting | A_t tracks 0.95→0.60 dynamics shift a static HMM blurs to 0.76; 99.7% acc |
| `structural_breaks.py` | **CUSUM** (mean breaks, online) + **ICSS** variance breaks + **Sansó κ₂** (dependence-robust) | CUSUM finds 80/160; ICSS finds 100/200; κ₂ restores size (FP 0.41→0.06) |
| `explosive.py` | **SADF/GSADF/BSADF** (Phillips–Shi–Yu) right-tailed recursive ADF — bubble/flight detection | RW no-bubble, explosive flagged 5.96≫crit, date-stamped |
| **Inference / rigor:** | | |
| `fracdiff.py` | **fractional differencing** (FFD) + **ADF** — min-d stationarity keeping memory | RW→unit-root, AR→stationary; min d=0.45 retains memory |
| `bootstrap.py` | moving-block, **stationary (Politis–Romano)**, cluster, **HAC**, **BCa** | iid 0.68 vs block/stat/HAC ~0.88; BCa rebalances skew tails |
| `conformal.py` | split + weighted **conformal** prediction bands | 0.90 coverage exact across gaussian/exp/heavy-tail |
| `range_vol.py` | range-based vol (Parkinson/GK/**Rogers-Satchell**/Yang-Zhang) for low-freq | 25–49× efficiency; RS drift-robust (−19% vs Parkinson +155%) |
| `operational_validation.py` | deployment-style **walk-forward OOT backtest** (PIT, conformal, determinism, multi-seed CIs) | deterministic; synthetic book-S(t) MAE ~0.05 |
| `synthetic_panel.py` | ground-truth survival-panel generator | known hazard for the above |
| `run_tests.py` | runs every self-test, PASS/FAIL summary | 14/14 |

## Known limitations (honest, deployment-relevant)

- **Bootstrap coverage caps ~0.88** at n=120 with strong persistence (percentile/HAC
  are first-order); fixed-b HAC or bootstrap-t would close the last gap. BCa fixes
  skew, not dependence.
- **Range-vol discretization bias** (~30–40% low) on few intra-period samples — use
  realized variance from daily balances when available.
- All numbers above are **synthetic-data methodology proofs**, not the product.
- Real sign-off requires the **DAV panel (Layer 0)** + Gate-A finding that the
  frozen OOS (2023–24) is single-regime → regime-stress S(t) is fit-only.
