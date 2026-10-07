import json
import math

import numpy as np
import pytest
from scipy import stats
from scipy.integrate import trapezoid

from spc.core.capability import geometric_indices, zscore_indices
from spc.core.capability.indices import Z_MAX
from spc.core.distributions import (
    BoxCoxNormal, Empirical, FitError, GaussianMixture, bootstrap_interval, choose_automatically, fit, fit_candidates,
    quantiles,
)
from spc.data import Dataset
from spc.report import generate
from spc.service import AnalysisRequest, analyze


def lognormal_sample(n=125, seed=3):
    return 9 + np.random.default_rng(seed).lognormal(0, 0.4, n)


def req(**kw):
    base = dict(stage="preliminary", lsl=8.5, usl=13.0, characteristic_class="major")
    base.update(kw)
    return AnalysisRequest(**base)


# ------------------------------------------------------------------ fitting

def test_fits_recover_the_parameters_of_large_samples():
    rng = np.random.default_rng(7)
    ln = fit(5 + rng.lognormal(0.3, 0.5, 20000), "lognormal")
    assert ln.params[0] == pytest.approx(0.5, abs=0.03) and ln.params[1] == pytest.approx(5.0, abs=0.3)
    wb = fit(rng.weibull(2.0, 20000) * 3.0, "weibull")
    assert wb.params[0] == pytest.approx(2.0, abs=0.06) and wb.params[2] == pytest.approx(3.0, abs=0.1)
    gm = fit(rng.gamma(3.0, 2.0, 20000), "gamma")
    assert gm.params[0] == pytest.approx(3.0, rel=0.15)


def test_geometric_indices_of_a_fit_match_the_true_distribution():
    rng = np.random.default_rng(11)
    truth = stats.lognorm(0.4, loc=9, scale=1.0)
    fitted = fit(truth.rvs(30000, random_state=rng), "lognormal")
    want = geometric_indices(truth, 8.5, 13.0)
    got = geometric_indices(fitted, 8.5, 13.0)
    assert got.p == pytest.approx(want.p, rel=0.03) and got.pk == pytest.approx(want.pk, rel=0.03)


def test_automatic_choice_follows_the_data():
    rng = np.random.default_rng(1)
    assert choose_automatically(fit_candidates(rng.normal(10, 0.1, 125))).family == "normal"
    assert choose_automatically(fit_candidates(lognormal_sample(300))).family in ("lognormal", "box_cox", "johnson_su")
    assert choose_automatically(fit_candidates(rng.weibull(1.8, 500) * 3)).family == "weibull"
    two = np.r_[rng.normal(9, 0.1, 300), rng.normal(10, 0.15, 200)]
    best = choose_automatically(fit_candidates(two))
    assert best.family == "mixture" and best.dist.describe()["components"] == 2


def test_a_normal_distribution_within_two_aic_units_wins():
    cands = fit_candidates(np.random.default_rng(1).normal(10, 0.1, 125))
    best = min((c for c in cands if c.ok), key=lambda c: c.aic)
    assert choose_automatically(cands).family == "normal" and choose_automatically(cands).aic - best.aic < 2


def test_candidates_report_failures_instead_of_hiding_them():
    cands = fit_candidates(np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]))  # too few for a mixture
    by = {c.family: c for c in cands}
    assert by["mixture"].ok is False and "30" in by["mixture"].reason
    assert cands[0].ok and cands[0].delta_aic == 0.0 and cands[-1].ok is False  # failures last


def test_unusable_input():
    with pytest.raises(FitError):
        fit([1.0, 1.0, 1.0, 1.0, 1.0, 1.0], "lognormal")
    with pytest.raises(FitError):
        fit(np.arange(100.0), "empirical")
    with pytest.raises(ValueError):
        fit(np.arange(100.0), "nonsense")


# ------------------------------------------------------------------ the distribution objects

@pytest.mark.parametrize("x", [lognormal_sample(300), np.random.default_rng(5).normal(3, 1, 300),
                               np.random.default_rng(5).uniform(-2, 5, 300)])  # also data with values <= 0
def test_box_cox_is_a_proper_distribution(x):
    d = fit(x, "box_cox")
    grid = np.linspace(x.min() - 8 * x.std(), x.max() + 8 * x.std(), 40001)
    assert trapezoid(d.pdf(grid), grid) == pytest.approx(1.0, abs=2e-3)
    p = np.array([0.00135, 0.1, 0.5, 0.9, 0.99865])
    assert d.cdf(d.ppf(p)) == pytest.approx(p, abs=1e-7)
    assert d.sf(d.ppf(p)) == pytest.approx(1 - p, abs=1e-7)


def test_box_cox_with_negative_exponent_has_an_upper_bound_and_still_quantiles():
    d = BoxCoxNormal(lam=-1.0, shift=0.0, mu=0.2, sd=0.05)  # T(x) = 1 - 1/x < 1, so x is bounded only through the cut
    q = quantiles(d)
    assert q[0] < q[1] < q[2] and all(math.isfinite(v) for v in q)


def test_mixture_quantiles_and_cdf_agree():
    g = GaussianMixture([0.4, 0.6], [9.0, 10.0], [0.1, 0.15])
    p = np.array([0.00135, 0.2, 0.5, 0.8, 0.99865])
    assert g.cdf(g.ppf(p)) == pytest.approx(p, abs=1e-9)
    grid = np.linspace(8, 11.5, 20001)
    assert trapezoid(g.pdf(grid), grid) == pytest.approx(1.0, abs=1e-6)


def test_empirical_distribution_uses_the_data_quantiles():
    x = np.random.default_rng(2).gamma(3, 1, 3000)
    d = fit(x, "empirical")
    assert isinstance(d, Empirical) and d.ppf(0.5) == pytest.approx(np.median(x))
    assert d.cdf(np.median(x)) == pytest.approx(0.5, abs=1e-3)


# ------------------------------------------------------------------ the z-score method

def test_z_score_keeps_the_resolution_far_in_the_tail():
    far = zscore_indices(stats.norm(0, 1), None, 9.0)  # a share of about 1e-19
    assert far.pk == pytest.approx(3.0, abs=1e-6)
    assert zscore_indices(stats.norm(0, 1), -9.0, None).pk == pytest.approx(3.0, abs=1e-6)
    capped = zscore_indices(stats.norm(0, 1), None, 60.0)  # share is 0: capped, not infinite
    assert capped.pk == pytest.approx(Z_MAX / 3)


def test_g_and_z_agree_for_a_normal_distribution():
    d = stats.norm(10, 0.1)
    g, z = geometric_indices(d, 9.5, 10.6), zscore_indices(d, 9.5, 10.6)
    # the 0.135 % quantile is 3.0000 sigma only to about 1e-5, so the two methods agree to that precision
    assert g.p == pytest.approx(z.p, rel=1e-4) and g.pk == pytest.approx(z.pk, rel=1e-4)


def test_g_and_z_differ_for_a_skewed_distribution():
    d = stats.lognorm(0.5, loc=9, scale=1.0)
    g, z = geometric_indices(d, 8.5, 13.0), zscore_indices(d, 8.5, 13.0)
    assert abs(g.pk - z.pk) > 0.05


# ------------------------------------------------------------------ bootstrap

def test_bootstrap_is_repeatable_and_depends_on_the_seed():
    x = lognormal_sample()
    d = fit(x, "lognormal")
    fn = lambda dd: geometric_indices(dd, 8.5, 13.0)
    a = bootstrap_interval(x, d, fn, 60, 0.95, seed=1)
    b = bootstrap_interval(x, d, fn, 60, 0.95, seed=1)
    c = bootstrap_interval(x, d, fn, 60, 0.95, seed=2)
    assert a == b and a != c
    assert a.ci_pk[0] < fn(d).pk < a.ci_pk[1] and a.succeeded > 50


def test_bootstrap_gives_no_interval_when_too_many_refits_fail():
    x = lognormal_sample()
    d = fit(x, "lognormal")

    def flaky(dd):
        raise ValueError("no")

    res = bootstrap_interval(x, d, flaky, 40, 0.95, seed=1)
    assert res.ci_pk is None and res.ci_p is None and res.succeeded == 0


def test_bootstrap_interval_covers_the_true_value_about_as_often_as_claimed():
    truth = stats.lognorm(0.4, loc=9, scale=1.0)
    want = geometric_indices(truth, 8.5, 13.0).pk
    hits, runs = 0, 40
    for s in range(runs):
        x = truth.rvs(100, random_state=np.random.default_rng(100 + s))
        d = fit(x, "lognormal")
        bi = bootstrap_interval(x, d, lambda dd: geometric_indices(dd, 8.5, 13.0), 80, 0.90, seed=s)
        hits += bi.ci_pk[0] <= want <= bi.ci_pk[1]
    assert hits / runs > 0.70  # nominal 0.90, small sample and few resamples: a loose bound that still catches a broken interval


# ------------------------------------------------------------------ analysis service

def test_default_analysis_is_unchanged_and_has_no_distribution_block():
    r = analyze(Dataset.from_values(lognormal_sample()), req())
    assert "distribution" not in r and r["indices"]["method"] == "normal, total standard deviation"
    assert any(w["code"] == "non_normal" for w in r["warnings"])


def test_non_normal_analysis_gives_g_indices_with_a_bootstrap_interval():
    ds = Dataset.from_values(lognormal_sample())
    r = analyze(ds, req(distribution="lognormal", method="G", bootstrap_n=80))
    d, ix = r["distribution"], r["indices"]
    assert d["name"] == "lognormal" and d["method"] == "G" and d["requested"] == "lognormal"
    assert d["bootstrap"]["requested"] == 80 and d["bootstrap"]["succeeded"] > 60 and d["bootstrap"]["seed"] == 20260701
    assert ix["method"] == "General Geometric (.G), lognormal"
    assert ix["ci_pk"][0] < ix["pk"] < ix["ci_pk"][1]
    assert not any(w["code"] == "non_normal" for w in r["warnings"])
    assert r["targets"]["verdict_pk"] in ("meets", "meets_estimate_only", "fails")
    assert [c["family"] for c in d["candidates"]][:1] and all("aic" in c for c in d["candidates"])
    json.dumps(r)  # plain JSON


def test_z_method_and_ppm_come_from_the_fitted_distribution():
    x = lognormal_sample()
    z = analyze(Dataset.from_values(x), req(distribution="lognormal", method="Z", bootstrap_n=0))
    g = analyze(Dataset.from_values(x), req(distribution="lognormal", method="G", bootstrap_n=0))
    assert z["indices"]["ppm"] == pytest.approx(g["indices"]["ppm"])  # the same distribution gives the same PPM
    assert z["indices"]["pk"] != pytest.approx(g["indices"]["pk"], abs=1e-3)
    d = fit(x, "lognormal")
    assert z["indices"]["ppm"] == pytest.approx((d.cdf(8.5) + d.sf(13.0)) * 1e6, rel=1e-6)


def test_without_an_interval_the_target_is_judged_on_the_estimate_alone():
    x = lognormal_sample()
    r = analyze(Dataset.from_values(x), req(distribution="lognormal", bootstrap_n=0))
    assert r["indices"]["ci_pk"] is None and r["indices"]["ci_p"] is None and r["distribution"]["bootstrap"] is None
    assert r["targets"]["verdict_pk"] in ("meets_no_interval", "fails")


def test_automatic_choice_may_end_with_the_normal_path():
    x = np.random.default_rng(1).normal(10, 0.1, 125)
    r = analyze(Dataset.from_values(x), req(lsl=9.5, usl=10.5, distribution="auto"))
    assert r["distribution"]["name"] == "normal" and r["indices"]["method"] == "normal, total standard deviation"
    assert r["indices"]["ci_pk"] is not None


def test_results_repeat_exactly_and_a_seed_changes_only_the_interval():
    ds = Dataset.from_values(lognormal_sample())
    a = analyze(ds, req(distribution="auto", bootstrap_n=60))
    b = analyze(ds, req(distribution="auto", bootstrap_n=60))
    c = analyze(ds, req(distribution="auto", bootstrap_n=60, seed=5))
    assert a == b
    assert a["indices"]["pk"] == c["indices"]["pk"] and a["indices"]["ci_pk"] != c["indices"]["ci_pk"]


def test_small_samples_are_warned_about_and_large_ones_are_ranked_on_a_sample():
    small = analyze(Dataset.from_values(lognormal_sample(40)), req(distribution="lognormal", bootstrap_n=0))
    assert any(w["code"] == "fit_small_sample" for w in small["warnings"])
    big = analyze(Dataset.from_values(lognormal_sample(8000)), req(distribution="lognormal", bootstrap_n=0))
    assert big["distribution"]["ranked_on"] == 5000 and big["distribution"]["name"] == "lognormal"


@pytest.mark.parametrize("kw", [{"distribution": "nonsense"}, {"method": "Q", "distribution": "lognormal"},
                                {"bootstrap_n": 5000, "distribution": "lognormal"},
                                {"distribution": "empirical", "method": "Z"}])
def test_bad_requests_are_refused(kw):
    with pytest.raises(ValueError):
        analyze(Dataset.from_values(lognormal_sample()), req(**kw))


def test_a_family_that_cannot_be_fitted_says_so():
    with pytest.raises(ValueError, match="could not be fitted"):
        analyze(Dataset.from_values(np.random.default_rng(1).normal(10, 1, 20)), req(distribution="mixture"))
    with pytest.raises(ValueError, match="2000"):
        analyze(Dataset.from_values(lognormal_sample()), req(distribution="empirical"))


def test_empirical_g_needs_a_big_sample_and_works_with_one():
    x = np.random.default_rng(4).gamma(4, 1, 3000)
    r = analyze(Dataset.from_values(x), req(lsl=0.0, usl=20.0, distribution="empirical", bootstrap_n=40))
    q = np.quantile(x, [0.00135, 0.5, 0.99865])
    assert r["indices"]["p"] == pytest.approx(20.0 / (q[2] - q[0]), rel=1e-9)


# ------------------------------------------------------------------ report

def make_report(lang="en", **kw):
    ds = Dataset.from_values(lognormal_sample())
    return generate(ds, req(distribution="lognormal", bootstrap_n=60, **kw), None, lang, now="2026-10-01T00:00:00+00:00", report_id="r1")


def test_report_names_the_method_and_the_fitted_distribution():
    g = make_report()
    assert "Ppk.G" in g.html or "Cpk.G" in g.html
    assert "Lognormal" in g.html and "fitted Lognormal" in g.html
    assert "bootstrap" in g.html and "seed 20260701" in g.html
    assert "Cw" not in g.html
    assert g.html.count("<svg") == 5  # histogram, run chart, probability plot, two control charts


def test_report_with_method_z_and_in_chinese():
    z = make_report(method="Z")
    assert ".Z" in z.html and "z-score method (.Z)" in z.html
    zh = make_report("zh-TW", method="G")
    assert "對數常態" in zh.html and "General Geometric 法" in zh.html and "Cw" not in zh.html


def test_report_without_an_interval_says_so():
    g = generate(Dataset.from_values(lognormal_sample()), req(distribution="lognormal", bootstrap_n=0), None, "en")
    assert "No confidence interval is available" in g.html
    assert ("Estimate met, no interval available" in g.html) or ("Not met" in g.html)


def test_normal_report_still_has_the_old_texts():
    g = generate(Dataset.from_values(np.random.default_rng(1).normal(10, 0.1, 125)), req(lsl=9.5, usl=10.5), None, "en")
    assert "Normal distribution, total standard deviation" in g.html and "fitted normal" in g.html


def test_archive_of_a_non_normal_report_reproduces():
    from spc.report import reproduce

    g = make_report()
    res = reproduce(g.archive)
    assert res.integrity_ok and res.reproduced, res.differences
    assert g.archive["request"]["distribution"] == "lognormal" and g.archive["request"]["seed"] == 20260701


def test_weibull2_and_rayleigh_have_the_lower_limit_at_zero_and_are_never_chosen_automatically():
    import numpy as np
    from scipy import stats

    from spc.core import distributions as D
    from spc.data import Dataset
    from spc.service import AnalysisRequest, analyze

    x = stats.weibull_min.rvs(1.9, scale=0.01, size=600, random_state=3)
    w = D.fit(x, "weibull2")
    assert w.family == "weibull2" and w.params[1] == 0.0 and w.params[0] == pytest.approx(stats.weibull_min.fit(x, floc=0)[0]) and w.k == 2
    q = D.quantiles(w)
    assert q[0] == pytest.approx(stats.weibull_min.ppf(0.00135, w.params[0], scale=w.params[2]))
    r = D.fit(np.abs(np.random.default_rng(1).rayleigh(0.02, 500)), "rayleigh")
    assert r.family == "rayleigh" and r.params[1] == pytest.approx(0.02, rel=0.1) and r.k == 1 and r.describe().keys() == {"location", "scale"}
    with pytest.raises(D.FitError):
        D.fit(np.array([-1.0, 1.0, 2.0, 3.0, 4.0]), "weibull2")
    with pytest.raises(D.FitError):
        D.fit(np.array([-1.0, 1.0, 2.0]), "rayleigh")
    assert "weibull2" not in D.FAMILIES and set(D.EXPLICIT_ONLY) <= set(D.SELECTABLE)
    ds = Dataset.from_values(x, subgroup=[str(i // 5) for i in range(600)])
    res = analyze(ds, AnalysisRequest(usl=0.04, distribution="weibull2", bootstrap_n=0))
    assert res["distribution"]["name"] == "weibull2" and res["indices"]["p"] is None and res["indices"]["pk"] > 0
    auto = analyze(ds, AnalysisRequest(lsl=0.0, usl=0.04, distribution="auto", bootstrap_n=0))
    assert all(c["family"] not in ("weibull2", "rayleigh") for c in auto["distribution"]["candidates"])


# ------------------------------------------------------------------ folded normal and the fit check (draft 7.8.1, 9.4)

def test_the_folded_normal_is_fitted_by_maximum_likelihood_and_never_chosen_automatically():
    import numpy as np
    from scipy import optimize, stats

    from spc.core import distributions as d

    rng = np.random.default_rng(41)
    x = np.abs(rng.normal(0.03, 0.02, 20000))
    m = d.fit(x, "folded_normal")
    p = m.describe()
    assert p["location"] == 0.0 and p["mean_over_sd"] * p["sd"] == pytest.approx(0.03, abs=0.002) and p["sd"] == pytest.approx(0.02, rel=0.05)
    # an independent maximum likelihood search on mu and sd gives the same fit
    nll = lambda v: -np.sum(stats.foldnorm.logpdf(x, abs(v[0]) / v[1], 0, v[1])) if v[1] > 0 else np.inf
    best = optimize.minimize(nll, [0.02, 0.02], method="Nelder-Mead", options={"xatol": 1e-7, "fatol": 1e-7})
    assert p["mean_over_sd"] * p["sd"] == pytest.approx(abs(best.x[0]), rel=2e-3) and p["sd"] == pytest.approx(best.x[1], rel=2e-3)
    q = m.ppf([0.00135, 0.5, 0.99865])
    assert q == pytest.approx(stats.foldnorm(p["mean_over_sd"], 0, p["sd"]).ppf([0.00135, 0.5, 0.99865]))
    assert "folded_normal" in d.EXPLICIT_ONLY and "folded_normal" not in d.FAMILIES
    assert all(c.family != "folded_normal" for c in d.fit_candidates(x))  # the automatic choice does not know it
    with pytest.raises(d.FitError):
        d.fit(np.array([-0.1, 0.2, 0.3, 0.4, 0.5, 0.6]), "folded_normal")


def test_the_probability_plot_correlation_follows_its_definition_and_sees_a_wrong_tail():
    import numpy as np
    from scipy import stats

    from spc.core import distributions as d

    rng = np.random.default_rng(42)
    x = rng.lognormal(0, 0.5, 400)
    good = d.fit(x, "lognormal")
    norm = stats.norm(x.mean(), x.std())
    pc_good, pc_bad = d.probability_plot_correlation(good, x, "upper"), d.probability_plot_correlation(norm, x, "upper")
    xs = np.sort(x)
    pp = (np.arange(1, 401) - 0.3) / 400.4
    assert pc_good["all"] == pytest.approx(np.corrcoef(xs, good.ppf(pp))[0, 1]) and pc_good["n_tail"] == 100
    assert pc_good["tail"] == pytest.approx(np.corrcoef(xs[300:], good.ppf(pp)[300:])[0, 1])
    assert pc_good["all"] > 0.995 and pc_bad["tail"] < pc_good["tail"] and pc_bad["all"] < pc_good["all"]  # a normal curve misses the long upper tail
    low = d.probability_plot_correlation(good, x, "lower")
    assert low["side"] == "lower" and low["tail"] == pytest.approx(np.corrcoef(xs[:100], good.ppf(pp)[:100])[0, 1])
