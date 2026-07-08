"""Time-based splitters with purge + embargo (pure stdlib) -- PLANv2 2.2 / 4b.

Folds are PREDICATES ON DECISION TIME tau (calendar month), never integer row
slices, because each row carries a label window [tau, tau+H] that must not overlap
the test block (PLANv2 4b obligation 3).

  walk_forward    : anchored expanding-origin OOT (the headline protocol)
  cpcv            : combinatorial purged CV -- N super-blocks choose k as test
                    (N=8,k=2 -> 28 paths), with purge + embargo = H

Leakage rule: a TRAIN row at month m is dropped if its label window [m, m+H]
overlaps any TEST month, OR if m lies within `embargo` months after a test block
(forward leakage of slow-moving macro state). The self-test asserts zero overlap.
"""
from __future__ import annotations

import itertools


def walk_forward(months, H, min_train=24, step=6, val_len=12):
    """Yield (train_months, test_months) anchored expanding-origin folds.
    months = sorted list of calendar-month ints (e.g. y*12+m)."""
    months = sorted(months)
    folds = []
    o = min_train
    while o + val_len <= len(months):
        cutoff = months[o - 1]
        test = months[o:o + val_len]
        test_set = set(test)
        # train = months <= cutoff whose label window doesn't reach a test month
        tmin = min(test)
        train = [m for m in months[:o] if m + H < tmin]   # purge: label must end before test
        folds.append((train, test))
        o += step
    return folds


def _purge_embargo(train_months, test_months, H, embargo):
    """Drop train months whose label window overlaps test, or within embargo after."""
    test_set = set(test_months)
    tmin, tmax = min(test_months), max(test_months)
    keep = []
    for m in train_months:
        # label window [m, m+H] overlaps the test span?
        if not (m + H < tmin or m > tmax):
            continue
        # embargo: drop train months within `embargo` after the test block
        if tmax < m <= tmax + embargo:
            continue
        keep.append(m)
    return keep


def cpcv(months, n_blocks=8, k=2, H=1, embargo=None):
    """Combinatorial purged CV. Partition months into n_blocks contiguous super-
    blocks; every size-k combination of blocks is a test set. Returns list of
    (train_months, test_months, test_block_ids)."""
    months = sorted(months)
    embargo = H if embargo is None else embargo
    n = len(months)
    bounds = [months[i * n // n_blocks: (i + 1) * n // n_blocks] for i in range(n_blocks)]
    folds = []
    for combo in itertools.combinations(range(n_blocks), k):
        test = sorted(m for b in combo for m in bounds[b])
        test_set = set(test)
        train_all = [m for m in months if m not in test_set]
        # purge+embargo around EACH contiguous test block separately
        train = train_all
        for b in combo:
            blk = bounds[b]
            if blk:
                train = _purge_embargo(train, blk, H, embargo)
        folds.append((train, test, combo))
    return folds


def _contiguous_runs(months):
    s = sorted(months)
    runs, lo, prev = [], s[0], s[0]
    for t in s[1:]:
        if t == prev + 1:
            prev = t
        else:
            runs.append((lo, prev))
            lo = prev = t
    runs.append((lo, prev))
    return runs


def assert_no_leakage(folds, H, embargo, with_blocks=False):
    """Verify no train label-window contains a test month, and embargo respected.
    Correct for non-contiguous CPCV test sets: checks label overlap against the
    test SET (not its min-max envelope) and embargo per contiguous test block."""
    for fold in folds:
        train, test = fold[0], fold[1]
        if not test:
            continue
        tset = set(test)
        runs = _contiguous_runs(test)
        for m in train:
            assert m not in tset, f"train month {m} is in test"
            # label window [m, m+H] must not contain ANY test month
            assert not any(m <= t <= m + H for t in test), \
                f"label window [{m},{m+H}] overlaps a test month"
            # embargo: m must not fall within `embargo` after any test block end
            for _, b_hi in runs:
                assert not (b_hi < m <= b_hi + embargo), \
                    f"train {m} within embargo after block end {b_hi}"
    return True


if __name__ == "__main__":
    months = list(range(2015 * 12, 2015 * 12 + 120))   # 120 monthly ints
    H = 12

    wf = walk_forward(months, H, min_train=36, step=12, val_len=12)
    print(f"walk-forward: {len(wf)} folds (H={H})")
    for tr, te in wf[:3]:
        print(f"  train n={len(tr)} [..{tr[-1]%12+1:02d}/{tr[-1]//12}]  "
              f"test {te[0]%12+1:02d}/{te[0]//12}..{te[-1]%12+1:02d}/{te[-1]//12}  "
              f"gap(purge)={te[0]-tr[-1]} mo")
    assert_no_leakage(wf, H, embargo=H)
    print("  no-leakage assertion: PASS")

    folds = cpcv(months, n_blocks=8, k=2, H=H, embargo=H)
    print(f"\nCPCV: N=8 k=2 -> {len(folds)} paths (expect 28)")
    f0 = folds[0]
    print(f"  path 0: test blocks {f0[2]}  test_n={len(f0[1])}  train_n={len(f0[0])}")
    purged = len(months) - len(f0[1]) - len(f0[0])
    print(f"  purged+embargoed rows in path 0: {purged}")
    assert_no_leakage(folds, H, embargo=H, with_blocks=True)
    print("  no-leakage assertion over all 28 paths: PASS")

    # show purge actually removes the boundary months
    tr, te, combo = folds[0]
    tset = set(te)
    boundary_clean = all((m + H < min(te) or m > max(te)) for m in tr)
    print(f"  every train label-window clears the test span: {boundary_clean}")
