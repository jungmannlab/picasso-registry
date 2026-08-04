"""Cohort-matching policy for the A2 three-axis descriptor (register C12,
ratified 2026-08-04). Pure functions/data — no DB — so the semantics are
unit-testable independently of the ``/cohort`` route that applies them.

The descriptor has three orthogonal axes:

* **Axis 1 — Sample type** (the taxonomy tree). Always the *ranking* axis:
  results are ordered by tree distance, falling back *up* the tree.
* **Axis 2 — Target** (1..n per run, per channel). Matched on target-set
  overlap.
* **Axis 3 — Acquisition** (modality / dimensionality / buffer, single-valued
  per run). Modality gates comparability.

**How much must match is metric-dependent** (A2): localization-quality metrics
(groups A/B — NeNA, precision, drift) are physics and generalize at *modality +
broad sample class* (the taxonomy root), so target/fine identity are
ranking-only. Structure/biology (group D) and kinetics/counting (group C) are
target-specific and must *additionally* match on target. That maps each metric
to a required-axis depth:

* ``"broad"`` — require modality (+ same broad class, i.e. the taxonomy root,
  which the route already enforces). Target optional.
* ``"fine"`` — additionally require a target match.

Dimensionality and buffer are always optional refining filters (applied when
supplied); only modality (both depths) and target (fine) are *required*.
"""

from __future__ import annotations

from .schemas import MatchDepth

# Metric names whose cohort must match at target level ("fine"): group D
# (structure/biology) + group C (kinetics/counting/damage — imager/target
# dependent). Everything else (groups A/B localization quality, and any
# novel/unknown metric) defaults to the broader modality-level cohort.
TARGET_LEVEL_METRICS: frozenset[str] = frozenset(
    {
        # C — kinetics / counting / damage
        "sbr",
        "background",
        "dark_time_s",
        "bright_time_s",
        "k_on",
        "k_off",
        "binding_freq_hz",
        "events_per_site",
        "damage_decay_rate",
        "duty_cycle",
        "density_locs_um2",
        "qpaint_count",
        "labeling_efficiency",
        # D — downstream structure / biology
        "n_clusters",
        "mean_cluster_size",
        "nnd_median_nm",
        "spinna_oligomer_fractions",
        "spinna_fit_quality",
        "g5m_n_molecules",
        "g5m_false_pos_est",
        "registration_error_nm",
    }
)


def resolve_match_depth(
    metric: str | None, match_depth: MatchDepth | None
) -> MatchDepth | None:
    """Decide which axes a cohort query must match.

    Precedence: an explicit ``match_depth`` wins; otherwise it is derived from
    ``metric`` via :data:`TARGET_LEVEL_METRICS`. With neither supplied the
    result is ``None`` — the taxon-only S0B-1 behaviour (no axis-2/3
    requirement), preserved for backward compatibility.
    """
    if match_depth is not None:
        return match_depth
    if metric is None:
        return None
    return "fine" if metric in TARGET_LEVEL_METRICS else "broad"
