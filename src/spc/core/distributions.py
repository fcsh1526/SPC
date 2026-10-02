"""Distribution fitting for the non-normal methods .G and .Z (AIAG-VDA SPC draft 7.8, ISO 22514-2).

The fitted distribution gives the quantiles X0.135 %, X50 %, X99.865 % (.G) and the shares outside the
specification (.Z, PPM). Choosing the distribution is a decision of the user. `fit_candidates` ranks
the families by AIC to help, and gives the Anderson-Darling statistic as a description of the fit.
Neither is a pass/fail test: the parameters are estimated from the same data, so the usual
p-values do not apply. The program never picks a distribution without saying so in the result.

Families:  normal, lognormal, weibull, gamma (each with a location shift when the data allow it),
           johnson_su, box_cox, mixture (2 to 5 normal components, EM), empirical (n >= 2000, .G only)
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

import numpy as np
from scipy import stats
from scipy.optimize import brentq
from scipy.special import logsumexp

FAMILIES = ("normal", "lognormal", "weibull", "gamma", "johnson_su", "box_cox", "mixture", "empirical")
EMPIRICAL_MIN_N = 2000
MAX_COMPONENTS = 5
P_QUANTILES = (0.00135, 0.5, 0.99865)


class FitError(ValueError):
    """The family cannot be fitted to these data."""


# ---------------------------------------------------------------------------- distribution objects

class ScipyDistribution:
    def __init__(self, family: str, frozen, params: tuple, k: int):
        self.family, self._d, self.params, self.k = family, frozen, tuple(float(p) for p in params), k

    def pdf(self, x):
        return self._d.pdf(x)

    def cdf(self, x):
        return self._d.cdf(x)

    def sf(self, x):
        return self._d.sf(x)

    def ppf(self, p):
        return self._d.ppf(p)

    def logpdf(self, x):
        return self._d.logpdf(x)

    def describe(self) -> dict:
        names = {"normal": ("mean", "sd"), "lognormal": ("shape", "location", "scale"),
                 "weibull": ("shape", "location", "scale"), "gamma": ("shape", "location", "scale"),
                 "johnson_su": ("a", "b", "location", "scale")}[self.family]
        return dict(zip(names, self.params))


class BoxCoxNormal:
    """x + shift is normal after the Box-Cox transform with exponent lam.

    For lam > 0 the transform has a lower bound (-1/lam), for lam < 0 an upper bound. The normal
    distribution is cut there and rescaled, so this is a proper distribution and its quantiles exist.
    """

    family, k = "box_cox", 3

    def __init__(self, lam: float, shift: float, mu: float, sd: float):
        self.lam, self.shift, self.mu, self.sd = lam, shift, mu, sd
        bound = (-1.0 / lam - mu) / sd if abs(lam) >= 1e-12 else None
        self._u_lo = bound if (bound is not None and lam > 0) else -np.inf
        self._u_hi = bound if (bound is not None and lam < 0) else np.inf
        self._f_lo, self._f_hi = stats.norm.cdf(self._u_lo), stats.norm.cdf(self._u_hi)
        self._mass = self._f_hi - self._f_lo
        if not self._mass > 1e-12:
            raise FitError("box_cox: the transform leaves no probability")

    def _u(self, x):
        z = np.asarray(x, dtype=float) + self.shift
        with np.errstate(all="ignore"):
            t = np.log(z) if abs(self.lam) < 1e-12 else (z**self.lam - 1.0) / self.lam
        return np.where(z > 0, (t - self.mu) / self.sd, -np.inf)

    def cdf(self, x):
        return np.clip((stats.norm.cdf(self._u(x)) - self._f_lo) / self._mass, 0.0, 1.0)

    def sf(self, x):
        return np.clip((self._f_hi - stats.norm.cdf(self._u(x))) / self._mass, 0.0, 1.0)

    def pdf(self, x):
        z = np.asarray(x, dtype=float) + self.shift
        with np.errstate(all="ignore"):
            d = stats.norm.pdf(self._u(x)) / (self.sd * self._mass) * np.where(z > 0, z, np.nan) ** (self.lam - 1.0)
        return np.where(z > 0, d, 0.0)

    def logpdf(self, x):
        with np.errstate(all="ignore"):
            return np.log(self.pdf(x))

    def ppf(self, p):
        u = stats.norm.ppf(self._f_lo + np.asarray(p, dtype=float) * self._mass)
        y = self.mu + self.sd * u
        with np.errstate(all="ignore"):
            z = np.exp(y) if abs(self.lam) < 1e-12 else np.maximum(self.lam * y + 1.0, 0.0) ** (1.0 / self.lam)
        return z - self.shift

    def describe(self) -> dict:
        return {"lambda": self.lam, "shift": self.shift, "mean_transformed": self.mu, "sd_transformed": self.sd}


class GaussianMixture:
    family = "mixture"

    def __init__(self, weights, means, sds):
        self.w, self.m, self.s = (np.asarray(a, dtype=float) for a in (weights, means, sds))
        self.k = 3 * len(self.w) - 1

    def pdf(self, x):
        x = np.asarray(x, dtype=float)[..., None]
        return (self.w * stats.norm.pdf(x, self.m, self.s)).sum(axis=-1)

    def logpdf(self, x):
        x = np.asarray(x, dtype=float)[..., None]
        return logsumexp(np.log(self.w) + stats.norm.logpdf(x, self.m, self.s), axis=-1)

    def cdf(self, x):
        x = np.asarray(x, dtype=float)[..., None]
        return (self.w * stats.norm.cdf(x, self.m, self.s)).sum(axis=-1)

    def sf(self, x):
        x = np.asarray(x, dtype=float)[..., None]
        return (self.w * stats.norm.sf(x, self.m, self.s)).sum(axis=-1)

    def ppf(self, p):
        lo, hi = float((self.m - 9 * self.s).min()), float((self.m + 9 * self.s).max())
        out = np.array([brentq(lambda v, q=q: float(self.cdf(v)) - q, lo, hi, xtol=1e-12 * (hi - lo)) for q in np.atleast_1d(p)])
        return out if np.ndim(p) else float(out[0])

    def describe(self) -> dict:
        return {"components": len(self.w), "weights": self.w.tolist(), "means": self.m.tolist(), "sds": self.s.tolist()}


class Empirical:
    family, k = "empirical", 0

    def __init__(self, x):
        self.x = np.sort(np.asarray(x, dtype=float))

    def cdf(self, v):
        return np.searchsorted(self.x, v, side="right") / self.x.size

    def sf(self, v):
        return 1.0 - self.cdf(v)

    def ppf(self, p):
        return np.quantile(self.x, p)

    def pdf(self, v):  # kernel estimate, only for drawing
        return stats.gaussian_kde(self.x)(v)

    def describe(self) -> dict:
        return {"n": int(self.x.size)}


# ---------------------------------------------------------------------------- fitting

def _fit_scipy(family: str, x: np.ndarray, start=None):
    lo, spread = float(x.min()), float(np.ptp(x))
    if family == "normal":
        return ScipyDistribution("normal", stats.norm(x.mean(), x.std(ddof=0)), (x.mean(), x.std(ddof=0)), 2)
    law = {"lognormal": stats.lognorm, "weibull": stats.weibull_min, "gamma": stats.gamma, "johnson_su": stats.johnsonsu}[family]
    best = None
    attempts = [{}]
    if family != "johnson_su" and lo > 0:
        attempts.append({"floc": 0.0})
    for kw in attempts:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                args = law.fit(x, *(start if (start is not None and not kw) else ()), **kw)
            frozen = law(*args)
            ll = float(np.sum(frozen.logpdf(x)))
        except Exception:
            continue
        loc = args[-2]
        if not math.isfinite(ll) or (family != "johnson_su" and not loc < lo - 1e-9 * max(spread, 1e-300) and "floc" not in kw):
            continue  # likelihood runs away to the smallest value: not a usable shifted fit
        k = len(args) - (1 if "floc" in kw else 0)
        aic = 2 * k - 2 * ll
        if best is None or aic < best[0]:
            best = (aic, ScipyDistribution(family, frozen, args, k))
    if best is None:
        raise FitError(f"{family} could not be fitted")
    return best[1]


def _fit_box_cox(x: np.ndarray) -> BoxCoxNormal:
    shift = 0.0 if x.min() > 0 else float(-x.min() + 1e-3 * max(np.ptp(x), 1e-12))
    try:
        y, lam = stats.boxcox(x + shift)
    except Exception as exc:
        raise FitError(f"box_cox could not be fitted: {exc}") from None
    sd = float(y.std(ddof=0))
    if not sd > 0:
        raise FitError("box_cox: no spread after the transform")
    return BoxCoxNormal(float(lam), shift, float(y.mean()), sd)


def _fit_mixture_k(x: np.ndarray, k: int, iters: int = 500) -> GaussianMixture:
    xs = np.sort(x)
    chunks = np.array_split(xs, k)
    m = np.array([c.mean() for c in chunks])
    floor = 1e-6 * float(x.var()) + 1e-300
    s = np.array([max(c.std(), math.sqrt(floor)) for c in chunks])
    w = np.full(k, 1.0 / k)
    last = -np.inf
    for _ in range(iters):
        logp = np.log(w) + stats.norm.logpdf(x[:, None], m, s)
        total = logsumexp(logp, axis=1)
        ll = float(total.sum())
        r = np.exp(logp - total[:, None])
        nk = r.sum(axis=0) + 1e-12
        w, m = nk / x.size, (r * x[:, None]).sum(axis=0) / nk
        s = np.sqrt(np.maximum((r * (x[:, None] - m) ** 2).sum(axis=0) / nk, floor))
        if abs(ll - last) < 1e-9 * max(1.0, abs(ll)):
            break
        last = ll
    order = np.argsort(m)
    return GaussianMixture(w[order], m[order], s[order])


def _fit_mixture(x: np.ndarray, components: int | None = None) -> GaussianMixture:
    if x.size < 30:
        raise FitError("a mixture needs at least 30 values")
    ks = [components] if components else range(2, min(MAX_COMPONENTS, x.size // 15) + 1)
    best = None
    for k in ks:
        g = _fit_mixture_k(x, k)
        bic = -2 * float(np.sum(g.logpdf(x))) + g.k * math.log(x.size)
        if best is None or bic < best[0]:
            best = (bic, g)
    if best is None:
        raise FitError("a mixture needs more values")
    return best[1]


def fit(x, family: str, *, start=None, components: int | None = None):
    """Fit one family. `start` and `components` let a bootstrap repeat the original fit quickly and in the same form."""
    x = np.asarray(x, dtype=float).ravel()
    if x.size < 5 or not np.all(np.isfinite(x)) or not np.ptp(x) > 0:
        raise FitError("need at least 5 finite values that are not all equal")
    if family == "box_cox":
        return _fit_box_cox(x)
    if family == "mixture":
        return _fit_mixture(x, components)
    if family == "empirical":
        if x.size < EMPIRICAL_MIN_N:
            raise FitError(f"the empirical method needs at least {EMPIRICAL_MIN_N} values")
        return Empirical(x)
    if family in ("normal", "lognormal", "weibull", "gamma", "johnson_su"):
        return _fit_scipy(family, x, start)
    raise ValueError(f"unknown distribution {family!r}; choose from {FAMILIES}")


def quantiles(dist) -> tuple[float, float, float]:
    q = tuple(float(v) for v in np.asarray(dist.ppf(np.array(P_QUANTILES))))
    if not all(math.isfinite(v) for v in q) or not q[0] < q[1] < q[2]:
        raise FitError("the quantiles of this distribution are not usable")
    return q


def loglik(dist, x) -> float:
    with np.errstate(all="ignore"):
        return float(np.sum(dist.logpdf(np.asarray(x, dtype=float))))


def anderson_darling(dist, x) -> float:
    xs = np.sort(np.asarray(x, dtype=float))
    n = xs.size
    f = np.clip(np.asarray(dist.cdf(xs), dtype=float), 1e-12, 1 - 1e-12)
    i = np.arange(1, n + 1)
    return float(-n - np.mean((2 * i - 1) * (np.log(f) + np.log(1 - f[::-1]))))


@dataclass(frozen=True)
class Candidate:
    family: str
    ok: bool
    k: int | None = None
    loglik: float | None = None
    aic: float | None = None
    delta_aic: float | None = None
    ad: float | None = None
    reason: str = ""
    dist: object = field(default=None, repr=False, compare=False)


def fit_candidates(x, families=("normal", "lognormal", "weibull", "gamma", "johnson_su", "box_cox", "mixture")) -> list[Candidate]:
    """Fit each family and rank by AIC (smaller is better). Families that fail stay in the list with the reason."""
    x = np.asarray(x, dtype=float).ravel()
    out: list[Candidate] = []
    for fam in families:
        try:
            d = fit(x, fam)
            quantiles(d)
            ll = loglik(d, x)
            if not math.isfinite(ll):
                raise FitError("likelihood is not finite")
            out.append(Candidate(fam, True, d.k, ll, 2 * d.k - 2 * ll, None, anderson_darling(d, x), "", d))
        except (FitError, ValueError, FloatingPointError, np.linalg.LinAlgError) as exc:
            out.append(Candidate(fam, False, reason=str(exc)))
    best = min((c.aic for c in out if c.ok), default=None)
    ranked = sorted((c for c in out if c.ok), key=lambda c: c.aic) + [c for c in out if not c.ok]
    return [Candidate(**{**c.__dict__, "delta_aic": None if best is None or not c.ok else c.aic - best}) for c in ranked]


def choose_automatically(cands: list[Candidate]) -> Candidate:
    """Smallest AIC. A normal distribution within 2 AIC units of the best wins: that is no real difference,
    and the simpler model is easier to defend."""
    usable = [c for c in cands if c.ok]
    if not usable:
        raise FitError("no distribution could be fitted")
    best = usable[0]
    normal = next((c for c in usable if c.family == "normal"), None)
    return normal if normal is not None and normal.aic - best.aic < 2.0 else best


# ---------------------------------------------------------------------------- bootstrap interval

def refit(dist, x):
    """The same family again on new data, started from the old parameters. Same number of mixture components."""
    if isinstance(dist, ScipyDistribution) and dist.family != "normal":
        return fit(x, dist.family, start=dist.params[:-2])
    if isinstance(dist, GaussianMixture):
        return fit(x, "mixture", components=len(dist.w))
    return fit(x, dist.family)


@dataclass(frozen=True)
class BootstrapInterval:
    requested: int
    succeeded: int
    ci_p: tuple[float, float] | None
    ci_pk: tuple[float, float] | None


def bootstrap_interval(x, dist, index_fn, n_boot: int, confidence: float, seed: int) -> BootstrapInterval:
    """Percentile interval of the indices. The data are resampled with replacement, the same family is
    fitted again each time and `index_fn(fitted)` gives the indices. The seed makes it repeatable:
    the same data, parameters and seed give the same interval."""
    x = np.asarray(x, dtype=float).ravel()
    rng = np.random.default_rng(seed)
    p_vals: list[float] = []
    pk_vals: list[float] = []
    for _ in range(n_boot):
        sample = rng.choice(x, size=x.size, replace=True)
        try:
            idx = index_fn(refit(dist, sample))
        except (FitError, ValueError, FloatingPointError, np.linalg.LinAlgError):
            continue
        if not math.isfinite(idx.pk):
            continue
        pk_vals.append(idx.pk)
        if idx.p is not None and math.isfinite(idx.p):
            p_vals.append(idx.p)
    a = 1.0 - confidence

    def interval(v):
        if len(v) < max(20, int(0.8 * n_boot)):
            return None  # too many failed refits: no interval is better than a wrong one
        lo, hi = np.quantile(v, [a / 2, 1 - a / 2])
        return float(lo), float(hi)

    return BootstrapInterval(n_boot, len(pk_vals), interval(p_vals), interval(pk_vals))
