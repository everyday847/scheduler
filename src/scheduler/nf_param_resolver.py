"""Resolve NF derived-parameter inputs (nf_parameters) into concrete solver_options bands.

The NF fairness bands are a central value +/- tolerance derived from semantic inputs:
  service_band = ncc_weeks * density
  nf_day_band  = service_band * nf_fraction (+/- nf_tolerance)
Changing an input (e.g. ncc_weeks) moves the dependent bands automatically. Explicit raw
bands in solver_options always override the derived ones (back-compat / manual control).
"""
from __future__ import annotations

import math

_DEFAULT_FRACTION = 1.0 / 3.0


def _as_pair(v):
    if isinstance(v, (int, float)):
        return (float(v), float(v))
    return (float(v[0]), float(v[1]))


def resolve_nf_parameters(nf_parameters, solver_options, fellow_groups, rules):
    opts = dict(solver_options or {})
    if not nf_parameters:
        return opts
    nf_band = dict(opts.get("nf_nf_day_band") or {})
    svc_band = dict(opts.get("nf_service_day_band") or {})
    for group, params in nf_parameters.items():
        weeks = params.get("ncc_weeks")
        w_lo, w_hi = int(weeks[0]), int(weeks[1])
        d_lo, d_hi = _as_pair(params["density"])
        frac = float(params.get("nf_fraction", _DEFAULT_FRACTION))
        tol = int(params.get("nf_tolerance", 0))
        s_lo = math.ceil(w_lo * d_lo)
        s_hi = math.floor(w_hi * d_hi)
        n_lo = max(0, round(s_lo * frac) - tol)
        n_hi = round(s_hi * frac) + tol
        svc_band.setdefault(group, [s_lo, s_hi])
        nf_band.setdefault(group, [n_lo, n_hi])
    if nf_band:
        opts["nf_nf_day_band"] = nf_band
    if svc_band:
        opts["nf_service_day_band"] = svc_band
    return opts
