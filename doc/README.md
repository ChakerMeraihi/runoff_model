# DAV run-off — theory companion

A 43-page theory book explaining *why* the DAV run-off model is built the way it is, from
first principles. Companion to the code in `../` and the build plan in `../PLANv2.md`.
Same pandoc + xelatex toolchain and house style as the trading book in `Documents/Python/book/`.

## Build

```
.\build.ps1            # full book  -> dav_runoff.pdf
.\build.ps1 ch03       # single chapter preview -> _preview.pdf
```

Needs `pandoc` and `xelatex` (MiKTeX) on PATH. Math note: pandoc 3.10's xelatex template
auto-loads `unicode-math`, under which `\boldsymbol`/`\bm` break and an isolated `$\sim$`
math group renders the tilde in the text font. `meta.yaml` remaps `\boldsymbol`$\to$`\symbf`;
write "approximately N" as `$\sim N$` (number inside the math group), never `$\sim$N`.

## Contents

| ch | title | covers |
|---|---|---|
| 1 | The Problem | non-maturing deposits, IRRBB, $B=A\cdot r$ decomposition, WAL/duration, the three difficulties |
| 2 | The Data | account-month survival panel, censoring/truncation, event-time/PIT, Gate A |
| 3 | The Core | discrete-time hazard, $S=\prod(1-h)$, pooled balance-weighted logistic, calibration |
| 4 | The Readout | elastic net, coordinate descent, 1-SE rule, why no early stopping |
| 5 | Nonlinearity | path signatures, pre-transforms, the honest trade vs a network |
| 6 | Erosion | retention $r(t)$, combined run-off $B(t)$, where Huber belongs |
| 7 | Baselines | convention core/volatile, ECM cointegration/elasticity/half-life, HAC |
| 8 | Regimes | HMM filtering/smoothing, online dynamic HMM, CTMC matrix-exp stressed survival |
| 9 | Breaks | CUSUM, ICSS + Sansó, SADF; diagnostic vs monitoring use |
| 10 | Uncertainty | block/stationary bootstrap, BCa, conformal, Monte-Carlo stress |
| 11 | Validation | walk-forward, CPCV, purge/embargo, nested selection, calibration scorecard |
| 12 | The System | frozen-artifact contract, two cadences, operations, honest limits |
