"""Phenotype denominator includes negatives, never unrelated organisms."""

import pytest

from src.advanced_analysis import AdvancedAnalysisEngine, CohortFilters
from src.database import connect, transaction
from src.phenotype_analysis import MARKER_SPECIES, PhenotypeEngine
from test_advanced_analysis import EC, KP, SA, advanced_seed, seed
from test_resistance import observation


def phenotype(settings, marker="ESBL", organism=EC, **kwargs):
    return PhenotypeEngine(settings).analyze(CohortFilters(organism=organism), marker, as_of_date="2026-09-27", **kwargs)


@pytest.mark.parametrize("count,positive,percentage", [(29, 1, None), (30, 0, 0), (30, 30, 100), (40, 10, 25)])
def test_marker_formula_negative_denominator_and_minimum(settings, advanced_seed, count, positive, percentage):
    advanced_seed([observation(str(i), esbl=int(i < positive), tests={}) for i in range(count)])
    result = phenotype(settings).current
    assert (result.n_first_isolates, result.n_observed, result.n_positive, result.n_negative) == (count, count, positive, count-positive)
    assert result.percentage == percentage and result.missing_tests == 0


def test_marker_flag_applied_after_first_selection(settings, advanced_seed):
    advanced_seed([observation("a", "2026-09-01", esbl=0), observation("a", "2026-09-02", esbl=1), observation("b", esbl=1)])
    result = phenotype(settings).current
    assert (result.n_observed, result.n_positive, result.n_negative) == (2, 1, 1)


@pytest.mark.parametrize("marker,organism", [("MRSA", EC), ("ESBL", SA), ("VRE", KP), ("CPE", "Pseudomonas aeruginosa")])
def test_unsupported_is_not_negative(settings, advanced_seed, marker, organism):
    advanced_seed([observation(str(i), organism=organism) for i in range(40)])
    result = phenotype(settings, marker, organism)
    assert not result.supported and result.current.status == "unsupported"
    assert result.current.percentage is None and result.current.n_observed == 0
    assert result.assessment.p_value is None and not result.assessment.preliminary_signal


@pytest.mark.parametrize("marker", list(MARKER_SPECIES))
def test_all_organisms_only_supported_species_and_one_flag_per_isolate(settings, advanced_seed, marker):
    organism = MARKER_SPECIES[marker][0]
    unrelated = "Pseudomonas aeruginosa"
    advanced_seed([observation("one", organism=organism, esbl=1, mrsa=1, vre=1, cpe=1),
                   observation("two", organism=organism), observation("unrelated", organism=unrelated, esbl=1, mrsa=1, vre=1, cpe=1)])
    result = phenotype(settings, marker, None).current
    assert (result.n_observed, result.n_positive, result.n_negative) == (2, 1, 1)


def test_phenotypes_work_without_antibiotics_in_partial_source(settings, advanced_seed):
    advanced_seed([observation("a", esbl=1, tests={})])
    with connect(settings) as con, transaction(con):
        con.execute("DELETE FROM antibiotics")
    assert phenotype(settings).current.n_observed == phenotype(settings).current.n_positive == 1


def test_rare_marker_counts_are_not_reliable_rate(settings, advanced_seed):
    advanced_seed([observation(str(i), organism="Enterococcus faecalis", vre=int(i == 0)) for i in range(10)])
    result = phenotype(settings, "VRE", "Enterococcus faecalis")
    assert result.current.n_positive == 1 and result.current.percentage is None
    assert result.assessment.status in ("no_data", "insufficient_data")


def test_subgroup_marker_filters_apply_before_rank(settings, advanced_seed):
    advanced_seed([observation("same", "2026-09-01", age=64), observation("same", "2026-09-02", age=65, esbl=1)])
    result = AdvancedAnalysisEngine(settings).analyze(CohortFilters(age_min=65), mode="phenotype", code="ESBL", as_of_date="2026-09-27")
    assert result.current.n_positive == result.current.n_observed == 1


@pytest.mark.parametrize("shared", [False, True])
def test_marker_temporal_evidence_uses_flags_and_retains_dependence_guards(settings, advanced_seed, shared):
    advanced_seed([observation(f"p{i}", "2026-08-01", "R", esbl=0) for i in range(40)] +
                  [observation(f"p{i}" if shared else f"q{i}", result="S", esbl=1) for i in range(40)])
    result = phenotype(settings)
    assert result.current.percentage == 100 and result.baseline.percentage == 0
    assert result.assessment.difference_pp == 100 and result.assessment.preliminary_signal
    assert result.assessment.status == ("analysis_limited" if shared else "potential_increase")
    assert (result.assessment.p_value is None) == shared
    assert result.assessment.shared_patients == (40 if shared else 0)
