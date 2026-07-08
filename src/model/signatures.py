"""Truncated path signatures (pure stdlib) -- PLANv2 6.2 nonlinearity.

The signature of a path is a fixed, deterministic feature map: the collection of
iterated integrals up to depth N. For a piecewise-linear path it is computed by
Chen's identity -- the signature of a concatenation is the tensor product of the
segment signatures -- and the signature of a single linear segment with increment
dx is exp_tensor(dx) (truncated). No iisignature, no numpy.

Design choices REQUIRED here (PLANv2 6.2), because a raw signature is
reparametrization-invariant and throws away exactly what we need:
  - time augmentation : append t as a channel (the continuous positional encoding;
    also makes the signature recover absolute timing / ordering)
  - basepoint         : prepend the start point so level (not just increments) shows
  - lead-lag          : duplicate channels with a 1-step lag so depth-2 terms pick
    up quadratic variation / volatility

Layout: flat dict keyed by the multi-index tuple (e.g. (0,), (1,), (0,1), ...).
"""
from __future__ import annotations

import itertools
import math


def _keys(dim, depth):
    out = []
    for d in range(1, depth + 1):
        out.extend(itertools.product(range(dim), repeat=d))
    return out


def _seg_signature(dx, depth):
    """Signature of one linear segment with increment vector dx, truncated at depth.
    Level d term for multi-index (i1..id) = prod(dx)/d!  (tensor exp of dx)."""
    dim = len(dx)
    sig = {}
    for d in range(1, depth + 1):
        inv = 1.0 / math.factorial(d)
        for idx in itertools.product(range(dim), repeat=d):
            v = inv
            for i in idx:
                v *= dx[i]
            sig[idx] = v
    return sig


def _chen(a, b, dim, depth):
    """Chen product: signature of path1 then path2. (1 + a) (x) (1 + b), truncated.
    Term for word w = sum over splits w = u v of a[u]*b[v] (empty word -> 1)."""
    out = {}
    for w in _keys(dim, depth):
        L = len(w)
        s = 0.0
        for k in range(L + 1):                 # split point: u=w[:k], v=w[k:]
            u, v = w[:k], w[k:]
            au = 1.0 if k == 0 else a.get(u, 0.0)
            bv = 1.0 if k == L else b.get(v, 0.0)
            s += au * bv
        out[w] = s
    return out


def signature(path, depth=2):
    """Signature (truncated at `depth`) of a piecewise-linear path = list of points
    (each a tuple/list of channel values). Returns flat dict keyed by multi-index."""
    dim = len(path[0])
    sig = None
    for p0, p1 in zip(path, path[1:]):
        dx = [p1[j] - p0[j] for j in range(dim)]
        seg = _seg_signature(dx, depth)
        sig = seg if sig is None else _chen(sig, seg, dim, depth)
    if sig is None:                            # single point: empty signature
        sig = {k: 0.0 for k in _keys(dim, depth)}
    return sig


def signature_vector(path, depth=2):
    sig = signature(path, depth)
    keys = _keys(len(path[0]), depth)
    return [sig[k] for k in keys], keys


# ---------------- path transforms (PLANv2 6.2: mandatory, not polish) ----------
def time_augment(path, t0=0.0, dt=1.0):
    return [[t0 + i * dt] + list(p) for i, p in enumerate(path)]


def add_basepoint(path):
    return [list(path[0])] + [list(p) for p in path]


def lead_lag(path):
    """Lead-lag transform: each original channel appears as (lead, lag) so depth-2
    area terms capture quadratic variation. Doubles channel count."""
    out = []
    for i in range(len(path)):
        lead = path[i]
        lag = path[i - 1] if i > 0 else path[0]
        out.append(list(lead) + list(lag))
    return out


if __name__ == "__main__":
    # 1) signature of a straight line in 2D: increment (a,b).
    #    depth-1 = (a,b); depth-2 (i,j) = a_i a_j / 2; antisymmetric area (0,1)-(1,0)=0
    a, b = 2.0, 3.0
    line = [[0.0, 0.0], [a, b]]
    sig, keys = signature_vector(line, depth=2)
    d = dict(zip(keys, sig))
    print("straight-line signature (increment 2,3):")
    print(f"  (0,)={d[(0,)]:.3f} (1,)={d[(1,)]:.3f}  [expect 2.000, 3.000]")
    print(f"  (0,0)={d[(0,0)]:.3f} [expect a^2/2=2.0]  (1,1)={d[(1,1)]:.3f} [expect b^2/2=4.5]")
    print(f"  (0,1)={d[(0,1)]:.3f} (1,0)={d[(1,0)]:.3f}  area=(0,1)-(1,0)={d[(0,1)]-d[(1,0)]:.3f} [expect 0 for a line]")

    # 2) Chen identity: sig(path) == chen(sig(first half), sig(second half))
    path = [[0, 0], [1, 0.5], [1.5, 2.0], [3.0, 1.0]]
    full = signature(path, 3)
    s1 = signature(path[:2], 3)
    s2 = signature(path[1:], 3)
    chen = _chen(s1, s2, 2, 3)
    err = max(abs(full[k] - chen[k]) for k in full)
    print(f"\nChen identity max error (full vs concat of halves), depth 3: {err:.2e}  [expect ~0]")

    # 3) area term detects rotation/volatility: triangle has nonzero signed area
    tri = [[0, 0], [1, 0], [1, 1], [0, 0]]
    st = signature(tri, 2)
    print(f"\ntriangle signed area (0,1)-(1,0))/2 = {(st[(0,1)]-st[(1,0)])/2:.3f}  [expect +0.5]")

    # 4) transforms wire up and grow channels as designed
    raw = [[1.0], [2.0], [1.5]]
    aug = lead_lag(time_augment(add_basepoint(raw)))
    print(f"\ntransforms: raw 1ch -> basepoint+time-aug+lead-lag = {len(aug[0])} channels, "
          f"{len(aug)} points")
    v, k = signature_vector(aug, depth=2)
    print(f"  depth-2 signature length for {len(aug[0])} channels = {len(v)} "
          f"(= d + d^2 = {len(aug[0])+len(aug[0])**2})")
