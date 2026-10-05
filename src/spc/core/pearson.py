"""The Pearson system of curves: a unimodal continuous distribution from mean, standard deviation, skewness and kurtosis.

Draft 10.3.5.2: a Pearson chart is a Shewhart chart whose normal quantiles are replaced by the quantiles of a skewed
distribution; "originally ... standardised Pearson curves, which require an estimate of skewness and kurtosis in
addition to the mean and standard deviation" (Clements 1989, ISO 7870-5, ISO 22514-4).

The curve of mean 0 and variance 1 follows dp/dx = -(x + a) p / (c0 + c1 x + c2 x^2) with
    D = 10 b2 - 12 b1 - 18,  c0 = (4 b2 - 3 b1) / D,  c1 = a = g1 (b2 + 3) / D,  c2 = (2 b2 - 3 b1 - 6) / D   (b1 = g1^2).
The type follows from the roots of the quadratic, and each type is a known distribution:
    normal; I beta (roots on both sides); III gamma (c2 = 0); IV the curve with complex roots, integrated numerically;
    V inverse gamma (double root); VI beta prime (roots on one side).
Only unimodal bell shapes are used (D > 0, positive exponents), as the draft says.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import integrate, optimize, stats

TOL = 1e-9


class PearsonError(ValueError):
    """The skewness and kurtosis do not give a unimodal Pearson curve."""


class Pearson:
    """A standardised Pearson distribution: mean 0, variance 1, skewness g1, kurtosis b2."""

    def __init__(self, g1: float, b2: float, _flipped: bool = False):
        if not (math.isfinite(g1) and math.isfinite(b2)):
            raise PearsonError("skewness and kurtosis must be finite")
        b1 = g1 * g1
        if b2 <= b1 + 1.0 + TOL:
            raise PearsonError("no distribution has this skewness and kurtosis (kurtosis must exceed skewness squared + 1)")
        self.g1, self.b2, self._flipped = float(g1), float(b2), _flipped
        self.type, self._d, self._ppf_fn = "", None, None
        if abs(g1) < 1e-8 and abs(b2 - 3.0) < 1e-8:
            self.type, self._d = "normal", stats.norm()
            return
        d = 10.0 * b2 - 12.0 * b1 - 18.0
        if d <= TOL:
            raise PearsonError("the shape is U- or J-shaped, not unimodal: a Pearson chart does not fit this data")
        c0, a, c2 = (4.0 * b2 - 3.0 * b1) / d, g1 * (b2 + 3.0) / d, (2.0 * b2 - 3.0 * b1 - 6.0) / d
        c1 = a
        self.coef = (c0, c1, c2, a)
        if abs(2.0 * b2 - 3.0 * b1 - 6.0) < 1e-7:
            self.type, self._d = "III", stats.pearson3(g1)
            return
        disc = c1 * c1 - 4.0 * c0 * c2
        if c2 < 0:  # type I: p ~ (x - r1)^A (r2 - x)^B between the roots
            roots = sorted(((-c1 + s * math.sqrt(disc)) / (2.0 * c2)) for s in (1.0, -1.0))
            r1, r2 = roots
            ea = -(r1 + a) / (c2 * (r1 - r2))
            eb = -(r2 + a) / (c2 * (r2 - r1))
            if ea <= 0 or eb <= 0:
                raise PearsonError("the shape is J-shaped, not unimodal: a Pearson chart does not fit this data")
            self.type, self._d = "I", stats.beta(ea + 1.0, eb + 1.0, loc=r1, scale=r2 - r1)
            return
        if disc < -TOL * max(1.0, c1 * c1):  # type IV: numerical
            self.type = "IV"
            self._init_iv(c0, c1, c2, a)
            return
        # types V and VI have the mode on one side of the root(s): work with the right tail (mirror a left tail)
        mode = -a
        if disc <= TOL * max(1.0, c1 * c1):
            r = -c1 / (2.0 * c2)
            if mode < r:
                return self._mirror()
            s = -(r + a) / c2
            shape = 1.0 / c2 - 1.0
            if s <= 0 or shape <= 0:
                raise PearsonError("the shape cannot be represented as a unimodal Pearson curve")
            self.type, self._d = "V", stats.invgamma(shape, loc=r, scale=s)
            return
        r1, r2 = sorted(((-c1 + s * math.sqrt(disc)) / (2.0 * c2)) for s in (1.0, -1.0))
        if mode < r1:
            return self._mirror()
        ea = -(r1 + a) / (c2 * (r1 - r2))
        eb = -(r2 + a) / (c2 * (r2 - r1))
        if eb <= 0 or -ea - eb - 1.0 <= 0:
            raise PearsonError("the shape cannot be represented as a unimodal Pearson curve")
        self.type, self._d = "VI", stats.betaprime(eb + 1.0, -ea - eb - 1.0, loc=r2, scale=r2 - r1)

    def _mirror(self):
        if self._flipped:
            raise PearsonError("the shape cannot be represented as a unimodal Pearson curve")
        other = Pearson(-self.g1, self.b2, _flipped=True)
        self.type, self._d, self._ppf_fn = other.type, other._d, other._ppf_fn
        self._reflect = True
        self._other = other

    # ---- type IV: p(x) ~ ((x - u)^2 + w^2)^(-1/(2 c2)) exp(-k arctan((x - u)/w)); with theta = arctan the integrand is smooth
    def _init_iv(self, c0, c1, c2, a):
        u = -c1 / (2.0 * c2)
        w = math.sqrt(4.0 * c0 * c2 - c1 * c1) / (2.0 * c2)
        k = (a + u) / (c2 * w)
        m = 1.0 / c2 - 2.0
        if m <= 0:
            raise PearsonError("the shape cannot be represented as a unimodal Pearson curve")
        f = lambda th: math.cos(th) ** m * math.exp(-k * th)
        total = integrate.quad(f, -math.pi / 2, math.pi / 2, limit=200)[0]
        self._iv = (u, w, f, total)

    def ppf(self, q):
        q = np.asarray(q, dtype=float)
        if getattr(self, "_reflect", False):
            return -self._other.ppf(1.0 - q)
        if self.type == "IV":
            u, w, f, total = self._iv

            def one(p):
                g = lambda th: integrate.quad(f, -math.pi / 2, th, limit=200)[0] / total - p
                return u + w * math.tan(optimize.brentq(g, -math.pi / 2 + 1e-12, math.pi / 2 - 1e-12, xtol=1e-13))
            return np.vectorize(one, otypes=[float])(q)
        return self._d.ppf(q)

    def cdf(self, x):
        if getattr(self, "_reflect", False):
            return 1.0 - self._other.cdf(-np.asarray(x, dtype=float))
        if self.type == "IV":
            u, w, f, total = self._iv
            return np.vectorize(lambda v: integrate.quad(f, -math.pi / 2, math.atan((v - u) / w), limit=200)[0] / total, otypes=[float])(x)
        return self._d.cdf(x)


def standardised_quantiles(g1: float, b2: float, probs=(0.00135, 0.5, 0.99865)) -> tuple[str, list[float]]:
    """(type, quantiles) of the standardised Pearson curve."""
    dist = Pearson(g1, b2)
    return dist.type, [float(v) for v in np.atleast_1d(dist.ppf(np.asarray(probs)))]
