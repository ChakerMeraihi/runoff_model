# Path Signatures (Nonlinearity)

(3.3) is linear in $\mathbf z$. Path *shape* may carry hazard signal. Stdlib, thin data $\Rightarrow$ use a
fixed nonlinear feature map (signature) + the linear readout (Ch. 4), not a learned net.

## Definition

**Definition 5.1.** For a path $X:[0,T]\to\mathbb R^d$, the signature is the family of iterated
integrals
$$
\mathrm{Sig}(X)=\big(1,(S^i)_i,(S^{ij})_{i,j},\dots\big),\qquad
S^{i_1\cdots i_k}=\!\!\int_{0<t_1<\cdots<t_k<T}\!\! dX^{i_1}_{t_1}\cdots dX^{i_k}_{t_k}.
\tag{5.1}
$$
Truncate at depth $N$: $\mathrm{Sig}^N$. Level $k$ = order-$k$ interactions of increments
(depth 1: net increment; depth 2: signed areas / covariations; …). Dimension
$$
\dim\mathrm{Sig}^N=\frac{d^{N+1}-1}{d-1}\quad(\text{exponential in }N).
\tag{5.2}
$$
This blow-up makes the Ch. 4 penalty mandatory. Compute by iterated *sums* ($\sim 50$ lines);
extend in time by **Chen's identity** (signature of concatenation = tensor product) $\Rightarrow$ causal
per-$\tau$ updates are cheap.

## Mandatory pre-transforms

Raw signature is reparametrization-invariant and drops level/absolute-time/quadratic-variation —
exactly what we need. Break the invariances:

- **time augmentation:** append channel $\tau$ (and seasoning) $\Rightarrow$ absolute time = continuous
  positional encoding;
- **basepoint:** prepend start value $\Rightarrow$ absolute level;
- **lead-lag:** embed vs 1-lag copy $\Rightarrow$ depth-2 terms capture quadratic variation (volatility).

Channels: $\log(\text{balance})$, money-market rate, inflation, (seasoning). Standardize fold-local;
sweep depth $N\in\{2,3\}$.

## The trade (honest)

Signature = *fixed* features + *linear* weights; a net learns both. More rigid — an asset here, not
superiority.

- **Lost:** learned adaptive representation; selective attention (a signature *summarizes* the path,
  cannot spotlight one event).
- **Kept:** depth-2 cross-channel interactions; positional info ($\tau$, seasoning channels);
  interaction-order control (depth); variable-length $\to$ fixed-vector.
- **Costs:** hard truncation past $N$; exponential dimension ($\Rightarrow$ penalty); invariances must be
  engineered back; worse interpretability (ablation/PDP mitigate, Ch. 8/11).
- **Why loss is small here:** $T\approx120$ cannot fund a learned high-capacity model OOT;
  bottleneck is data/signal, not architecture. A sharp pattern above depth $N$ and below noise floor
  is unverifiable on 120 months $\Rightarrow$ claiming a net would catch it violates the honesty rule.

## Empirical verdict (synthetic)

Tested through the production selector (grid tunes L1):
$$
\text{raw (4)}=0.1710,\quad \text{raw+oracle-path (5)}=0.1710\ (\text{tie}),\quad
\text{raw+sig (46)}=0.1720\ (-0.6\%).
$$
Key: the **oracle** one-step balance-drop feature also ties raw $\Rightarrow$ current $\log(\text{balance})$
already absorbs path info $\Rightarrow$ the synthetic is Markovian-enough $\Rightarrow$ test is **uninformative, not
negative**. Signatures = auto-re-tested candidate, default off, decided on the real panel (Ch. 11).
