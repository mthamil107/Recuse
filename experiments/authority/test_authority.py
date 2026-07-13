"""Deterministic tests for the authority-hierarchy harness -- NO API key, NO network.

We inject a mock agent with a FIXED, known source preference and assert that the
harness (outcome coding) + analyze (aggregation) correctly RECOVER that preference
as the derived authority ranking, with sensible follow-rates and confidence
intervals. If the round-robin math or coding is wrong, these fail.

Run:  pytest -q test_authority.py
"""
import os
import tempfile

import authority as A
import analyze as AN

# Ground-truth preference the mock obeys (highest authority first).
PREF = ["in_band_server_signal", "system_prompt", "user_prompt", "tool_output"]


def _run(pref=PREF, n_per_cell=30, noise=0.0, seed=0):
    agent = A.mock_agent(pref, seed=seed, noise=noise)
    recs = []
    for sc in A.build_scenarios([("test", "mock")], n_per_cell=n_per_cell):
        recs.append(A.run_trial(sc, agent))
    return recs


# -------------------------------------------------------------------- coding unit tests
def test_code_which_won_conflict():
    sc = A.Scenario("m", "mock", "in_band_server_signal", "user_prompt",
                    A.RECUSE, A.PROCEED, "conflict", 0)
    # agent recused -> the RECUSE-carrying channel (in_band) won
    assert A.code_which_won(A.RECUSE, sc) == "in_band_server_signal"
    # agent proceeded -> the PROCEED-carrying channel (user_prompt) won
    assert A.code_which_won(A.PROCEED, sc) == "user_prompt"
    # neither -> ambiguous
    assert A.code_which_won("ambiguous", sc) == "ambiguous"


def test_code_which_won_agree():
    sc = A.Scenario("m", "mock", "system_prompt", "tool_output",
                    A.PROCEED, A.PROCEED, "agree", 0)
    assert A.code_which_won(A.PROCEED, sc) == "followed"
    assert A.code_which_won(A.RECUSE, sc) == "defied"


def test_mock_obeys_top_preference():
    # in a pair, the higher-preference channel's directive is always chosen
    agent = A.mock_agent(PREF)
    ti = A.render_trial_input(
        A.Scenario("m", "mock", "in_band_server_signal", "system_prompt",
                   A.RECUSE, A.PROCEED, "conflict", 0), "u")
    assert agent(ti)["action"] == A.RECUSE      # in_band outranks system_prompt


# -------------------------------------------------------------------- recovery tests
def test_recovers_exact_ranking():
    report = AN.analyze(_run())
    a = report["mock"]
    assert a["authority_ranking"] == PREF, a["authority_ranking"]
    # perfect round-robin win rates: 3/3, 2/3, 1/3, 0/3
    rates = [round(a["follow_rates"][s]["rate"], 3) for s in PREF]
    assert rates == [1.0, round(2/3, 3), round(1/3, 3), 0.0], rates
    assert a["ambiguous_conflicts"] == 0


def test_recovers_a_different_ranking():
    pref2 = ["user_prompt", "tool_output", "in_band_server_signal", "system_prompt"]
    report = AN.analyze(_run(pref=pref2))
    assert report["mock"]["authority_ranking"] == pref2


def test_cell_counts_and_n_per_cell():
    # 4 sources -> C(4,2)=6 pairs; 2 relations; n=30/cell.
    recs = _run(n_per_cell=30)
    conflicts = [r for r in recs if r["relation"] == "conflict"]
    agrees = [r for r in recs if r["relation"] == "agree"]
    assert len(conflicts) == 6 * 30
    assert len(agrees) == 6 * 30
    # every conflict cell has >= 30 trials
    from collections import Counter
    cells = Counter((tuple(sorted((r["source_a"], r["source_b"]))), r["relation"])
                    for r in recs)
    assert all(c >= 30 for c in cells.values()), cells


def test_agree_baseline_compliance_full():
    a = AN.analyze(_run())["mock"]
    # mock always obeys its top present channel -> on agree trials it always follows
    assert a["agree_compliance"]["rate"] == 1.0


def test_confidence_intervals_present_and_bracket_rate():
    a = AN.analyze(_run())["mock"]
    for s in PREF:
        f = a["follow_rates"][s]
        lo, hi = f["ci95"]
        assert 0.0 <= lo <= f["rate"] <= hi <= 1.0
        assert f["ci_method"].startswith("Clopper-Pearson") or f["ci_method"].startswith("Wilson")


def test_noise_still_recovers_ranking_but_softens_rates():
    # with mild noise the ordering must survive though rates move off {0,1}
    a = AN.analyze(_run(noise=0.15, n_per_cell=60))["mock"]
    assert a["authority_ranking"] == PREF
    assert a["follow_rates"][PREF[0]]["rate"] < 1.0   # top channel now loses a few


def test_pairwise_matrix_is_transitive_for_fixed_preference():
    a = AN.analyze(_run())["mock"]
    pw = a["pairwise_wins"]
    rank = {s: i for i, s in enumerate(PREF)}
    # higher-preference source beats every lower one in every head-to-head trial
    for winner, losers in pw.items():
        for loser in losers:
            assert rank[winner] < rank[loser], (winner, loser)


def test_run_batch_persists_jsonl(tmp_path=None):
    import json
    d = tempfile.mkdtemp()
    path = os.path.join(d, "out.jsonl")
    recs, p = A.run_batch([("test", "mock")], A.mock_agent(PREF),
                          n_per_cell=30, results_path=path)
    assert p == path and os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        lines = [json.loads(x) for x in f if x.strip()]
    assert len(lines) == len(recs) == 6 * 30 * 2
    r = lines[0]
    for key in ("model", "vendor", "source_a", "source_b", "directive_a",
                "directive_b", "relation", "action", "which_won", "seed", "ts"):
        assert key in r


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
