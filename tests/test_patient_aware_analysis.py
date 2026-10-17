"""Independent hand-counts and exact-binomial references, with no identifier exports."""

import json
from contextlib import contextmanager
from dataclasses import replace
from math import comb

import pytest

from src.advanced_analysis import AdvancedAnalysisEngine,CohortFilters
from src.analysis_service import DatasetChangedError
from src.anomalies import AnomalyEngine
from src.database import connect,transaction,database_summary
from src.patient_aware_analysis import PatientAwareEngine,exact_mcnemar,validate_patient_revision
from src.patient_aware_interface import validate_matches_advanced
from src.resistance import ResistanceFilters
from src.simulator import ReplaySession,historical_plan,scenario_plan,HISTORICAL,SCENARIO
from test_resistance import observation,seed
from test_advanced_analysis import advanced_seed

EC="Escherichia coli"


def analyze(settings,cohort=None,**kwargs):
    return PatientAwareEngine(settings).analyze(cohort or CohortFilters(organism=EC,specimen="urine"),code="CIP",as_of_date="2026-09-27",**kwargs)


def paired_rows(transitions):
    rows=[]
    for index,(b,c) in enumerate(transitions):
        rows += [observation(f"shared-{index}","2026-08-01",tests={"CIP":b} if b else {}),
                 observation(f"shared-{index}",tests={"CIP":c} if c else {})]
    return rows


@pytest.mark.parametrize("b,c,known",[(0,0,1),(1,0,1),(2,0,.5),(6,0,.03125),(5,1,.21875),(3,3,1),(13,7,.26317596435546875)])
def test_exact_mcnemar_against_hand_binomial(b,c,known):
    independent=min(1,2*sum(comb(b+c,k) for k in range(min(b,c)+1))/2**(b+c))
    assert exact_mcnemar(b,c)==pytest.approx(known)
    assert exact_mcnemar(b,c)==pytest.approx(independent)
    assert exact_mcnemar(c,b)==exact_mcnemar(b,c)


@pytest.mark.parametrize("b,c",[(-1,0),(True,1),(1.,2),(0,"2")])
def test_invalid_discordant_counts(b,c):
    with pytest.raises(ValueError):
        exact_mcnemar(b,c)


def test_completely_independent_and_sensitivity_equal_original(settings,seed):
    seed([observation(f"old{i}","2026-08-01") for i in range(30)]+
         [observation(f"new{i}",result="R") for i in range(30)])
    result=analyze(settings)
    assert result.membership.shared_observed==result.membership.shared_tested==0
    assert result.membership.baseline_only==result.membership.current_only==30
    assert result.paired.status=="no_complete_pairs" and result.paired.p_value is None
    assert result.sensitivity.current==result.full_population.current
    assert result.sensitivity.baseline==result.full_population.baseline
    assert result.sensitivity.p_value==pytest.approx(2/comb(60,30))
    assert result.sensitivity.status=="eligible_exploratory" # never potential_* / BY
    assert "No patients were excluded" in result.sensitivity.explanation


def test_all_shared_transitions_and_empty_sensitivity(settings,seed):
    seed(paired_rows([("S","S")]*5+[("S","R")]*6+[("R","S")]*2+[("R","R")]*4))
    r=analyze(settings)
    assert r.membership.shared_observed==17
    assert r.membership.baseline_only==r.membership.current_only==0
    assert (r.paired.s_to_s,r.paired.s_to_r,r.paired.r_to_s,r.paired.r_to_r)==(5,6,2,4)
    assert r.paired.discordant_pairs==8
    assert r.paired.p_value==pytest.approx(74/256)
    assert r.sensitivity.status=="no_data" and r.sensitivity.p_value is None
    assert r.sensitivity.current.resistance_pct is None
    assert any("Small paired sample" in s for s in r.paired.limitations)


def test_partial_overlap_categories_and_restricted_counts(settings,seed):
    seed(paired_rows([("S","R"),("R","S"),("R","R")])+
         [observation(f"old{i}","2026-08-01","R" if i<7 else "S") for i in range(31)]+
         [observation(f"new{i}",result="R" if i<12 else "S") for i in range(30)])
    r=analyze(settings)
    assert (r.membership.baseline_only,r.membership.current_only,r.membership.shared_observed)==(31,30,3)
    assert (r.sensitivity.baseline.n_susceptible,r.sensitivity.baseline.n_resistant,r.sensitivity.baseline.n_tested)==(24,7,31)
    assert (r.sensitivity.current.n_susceptible,r.sensitivity.current.n_resistant,r.sensitivity.current.n_tested)==(18,12,30)
    assert r.sensitivity.difference_pp==pytest.approx(40-700/31)
    assert r.full_population.current.n_tested==33 and r.full_population.baseline.n_tested==34
    assert r.full_population.status=="analysis_limited"
    assert r.sensitivity.current_ci and r.sensitivity.baseline_ci
    assert "selection bias" in r.sensitivity.explanation.lower()


def test_shared_presence_includes_missing_tests_and_never_imputes_s(settings,seed):
    seed(paired_rows([("S",None),(None,"R"),(None,None),("S","R")])+
         [observation("baseonly","2026-08-01",tests={}),observation("currentonly",tests={})])
    r=analyze(settings)
    assert (r.membership.shared_observed,r.membership.shared_tested)==(4,1)
    assert r.membership.baseline_without_usable_sr==3 and r.membership.current_without_usable_sr==3
    assert r.membership.without_usable_sr_either_period==3
    assert r.paired.n_complete_pairs==1 and r.paired.s_to_r==1
    assert r.paired.s_to_s==r.paired.r_to_s==r.paired.r_to_r==0
    assert r.paired.shared_without_complete_sr==3
    assert r.sensitivity.baseline.n_first_isolates==r.sensitivity.current.n_first_isolates==1
    assert r.sensitivity.baseline.n_tested==r.sensitivity.current.n_tested==0


def test_first_untested_is_not_replaced_and_filters_precede_ranking(settings,advanced_seed):
    advanced_seed([observation("a","2026-08-01",tests={},age=64),observation("a","2026-08-02","R",age=65),
                   observation("a","2026-09-01","S",age=65),observation("a","2026-09-02","R",age=65)])
    full=analyze(settings)
    assert full.full_population.baseline.n_tested==0 and full.full_population.current.n_susceptible==1
    assert full.paired.n_complete_pairs==0
    older=analyze(settings,CohortFilters(organism=EC,specimen="urine",age_min=65))
    assert older.paired.r_to_s==1
    legacy=AdvancedAnalysisEngine(settings).analyze(older.cohort,code="CIP",as_of_date="2026-09-27")
    validate_matches_advanced(older,legacy)


def test_ties_and_calendar_boundaries(settings,seed):
    seed([observation("tie","2026-08-31",tests={},culture_id="same",isolate_no=1),
          observation("tie","2026-08-31","R",culture_id="same",isolate_no=2),
          observation("tie","2026-08-30","S"),
          observation("oldedge","2025-08-31","R"),observation("too_old","2025-08-30","R"),
          observation("future","2026-09-28","R")])
    r=analyze(settings)
    assert r.membership.shared_observed==1 and r.membership.baseline_only==1 and r.membership.current_only==0
    assert r.full_population.current.n_first_isolates==1 and r.full_population.current.n_tested==0
    assert r.full_population.baseline.n_tested==2
    assert r.paired.n_complete_pairs==0


@pytest.mark.parametrize("pairs,status,p",[([],"no_complete_pairs",None),([("S","S")]*4,"no_discordant_pairs",1),([("R","R")],"no_discordant_pairs",1),([("S","R")],"eligible_exploratory",1),([("S","R")]*6,"eligible_exploratory",.03125)])
def test_zero_and_small_discordant_counts(settings,seed,pairs,status,p):
    seed(paired_rows(pairs))
    r=analyze(settings)
    assert r.paired.status==status and r.paired.p_value==p
    assert ("No paired resistance percentage" in " ".join(r.paired.limitations))==bool(pairs)


@pytest.mark.parametrize("n,status,pct",[(0,"no_data",None),(29,"insufficient_data",None),(30,"eligible_exploratory",0)])
def test_sensitivity_sample_size_gates(settings,seed,n,status,pct):
    seed([observation(f"old{i}","2026-08-01") for i in range(n)]+[observation(f"new{i}") for i in range(30)])
    r=analyze(settings)
    assert r.sensitivity.status==status and r.sensitivity.baseline.resistance_pct==pct
    assert (r.sensitivity.p_value is not None)==(n>=30)


def test_ambiguous_pooled_pairs_withheld_not_arbitrarily_collapsed(settings,seed):
    seed(paired_rows([("S","R")])+[
        observation("shared-0","2026-08-01","R",organism="Klebsiella pneumoniae"),
        observation("shared-0",result="S",organism="Klebsiella pneumoniae")])
    r=analyze(settings,CohortFilters(specimen="urine"))
    assert r.paired.ambiguous_shared_patients==1
    assert r.paired.n_complete_pairs is None and r.paired.s_to_s is None and r.paired.p_value is None
    assert r.paired.status=="analysis_limited"


def test_remaining_within_period_dependence_withholds_fisher_and_ci(settings,seed):
    rows=[]
    for i in range(30):
        rows += [observation(f"base{i}","2026-08-01",organism=organism) for organism in (EC,"Klebsiella pneumoniae")]
        rows.append(observation(f"cur{i}",result="R"))
    seed(rows)
    r=analyze(settings,CohortFilters(specimen="urine"))
    assert r.sensitivity.baseline.n_tested==60
    assert r.sensitivity.status=="analysis_limited" and r.sensitivity.p_value is None
    assert r.sensitivity.baseline_ci is None and r.sensitivity.current_ci is not None


def test_pooled_cross_species_single_results_are_not_paired(settings,seed):
    seed([observation("same","2026-08-01","S",organism=EC),
          observation("same",result="R",organism="Klebsiella pneumoniae")])
    r=analyze(settings,CohortFilters(specimen="urine"))
    assert r.membership.shared_tested==1
    assert r.paired.ambiguous_shared_patients==0
    assert r.paired.n_complete_pairs is None and r.paired.p_value is None
    assert "different organisms/specimens are not paired" in r.paired.explanation


def test_read_only_privacy_and_global_alerts_unchanged(settings,seed):
    seed(paired_rows([("S","R")]*35))
    before=AnomalyEngine(settings).scan(as_of_date="2026-09-27")
    with connect(settings,read_only=True) as con:
        dump=list(con.iterdump())
    r=analyze(settings)
    assert r.full_population.status=="analysis_limited" and r.paired.p_value<.05
    assert AnomalyEngine(settings).scan(as_of_date="2026-09-27")==before
    assert before.tested_comparisons==0 and not before.alerts
    text=json.dumps(r.to_dict())
    assert all(private not in text for private in ("shared-0","patient_id","culture_id","isolate_id","workspace=","restore_token",str(settings.database_path)))
    assert all(word in " ".join(r.assumptions).lower() for word in ("confounding","selection bias","not multiplicity-adjusted","must not be combined"))
    with connect(settings,read_only=True) as con:
        assert list(con.iterdump())==dump


def test_expected_version_export_and_during_calculation_guards(settings,seed,monkeypatch):
    import src.patient_aware_analysis as module
    seed([observation("a")])
    r=analyze(settings)
    with pytest.raises(DatasetChangedError):
        analyze(settings,expected_version=8)
    original=module.PatientAwareReport
    def mutate(*args,**kwargs):
        result=original(*args,**kwargs)
        with connect(settings) as con,transaction(con):
            con.execute("UPDATE data_version SET version=8")
        return result
    monkeypatch.setattr(module,"PatientAwareReport",mutate)
    with pytest.raises(DatasetChangedError):
        analyze(settings,expected_version=7)
    with pytest.raises(DatasetChangedError):
        validate_patient_revision(r,settings)


def test_incompatible_context_and_full_report_parity_guard(settings,seed):
    seed([observation("a")])
    with pytest.raises(ValueError):
        analyze(settings,CohortFilters(ward_code="ED",ward_type="in"))
    r=analyze(settings)
    a=AdvancedAnalysisEngine(settings).analyze(r.cohort,code="CIP",as_of_date=r.analysis_date)
    validate_matches_advanced(r,a)
    with pytest.raises(DatasetChangedError):
        validate_matches_advanced(r,replace(a,data_version=8))
    with pytest.raises(DatasetChangedError):
        validate_matches_advanced(r,replace(a,current=replace(a.current,n_positive=5)))


@pytest.mark.parametrize("mode",[HISTORICAL,SCENARIO])
def test_replay_isolation_and_empty_source(settings,seed,mode):
    seed([observation("a","2026-08-01")])
    before=database_summary(settings)
    replay=ReplaySession(settings,historical_plan(settings) if mode==HISTORICAL else scenario_plan())
    r=PatientAwareEngine(replay.settings).analyze(code="CIP",source=mode)
    assert r.analysis_date is None and r.data_version==0 and r.paired.p_value is None
    replay.step()
    r=PatientAwareEngine(replay.settings).analyze(code="CIP",source=mode)
    assert r.source==mode and r.data_version==1
    assert r.source_identity!=analyze(settings).source_identity
    with pytest.raises(DatasetChangedError):
        validate_patient_revision(r,settings)
    assert database_summary(settings)==before
    replay.pause()
    replay.lease.close()


def test_one_set_based_read_and_no_global_scan(settings,seed,monkeypatch):
    import src.resistance as resistance
    seed(paired_rows([("S","R")]*100))
    original=resistance.connect
    sql=[]
    @contextmanager
    def traced(*args,**kwargs):
        with original(*args,**kwargs) as con:
            con.set_trace_callback(sql.append)
            yield con
    monkeypatch.setattr(resistance,"connect",traced)
    monkeypatch.setattr(AnomalyEngine,"scan",lambda *a,**k:pytest.fail("No patient-aware scan family exists"))
    r=analyze(settings)
    assert r.paired.n_complete_pairs==100
    assert sum(q.lstrip().upper().startswith("WITH") for q in sql)==1
    assert sum(q.lstrip().upper().startswith(("SELECT","WITH")) for q in sql)==3
