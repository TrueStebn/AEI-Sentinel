"""Hand-counted evidence, original scan scopes and revision-safe aggregate PDFs."""

import io
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import date

import pytest
from pypdf import PdfReader

from src.analysis_service import DatasetChangedError
from src.anomalies import AnomalyEngine
from src.briefing import (BriefingFilters, ORDERINGS, build_briefing, finding_rows, freshness,
                          ordered_findings, validate_revision)
from src.briefing_pdf import render_pdf
from src.database import connect, transaction
from src.investigations import origin_locator, resolve_origin
from src.resistance import ResistanceEngine, ResistanceFilters
from src.simulator import ReplaySession, SCENARIO, HISTORICAL, historical_plan, scenario_plan
from src.signals import surveillance_signals
from src.workspaces import MODES
from test_resistance import observation, seed

EC = "Escherichia coli"


def brief(settings,**kwargs):
    return build_briefing(settings,as_of_date="2026-09-27",generated_at="2026-10-17T00:00:00+00:00",**kwargs)


@pytest.fixture
def populations(seed):
    # ED independent, adequate, large change; URO dependent; KP sparse; blood baseline-only.
    seed([observation(f"old{i}","2026-08-01","S") for i in range(40)] +
         [observation(f"new{i}",result="R" if i<24 else "S") for i in range(40)] +
         [observation(f"shared{i}","2026-08-01",ward_code="URO") for i in range(30)] +
         [observation(f"shared{i}",result="R",ward_code="URO") for i in range(30)] +
         [observation("sparse",organism="Klebsiella pneumoniae")] +
         [observation("oldblood","2026-08-01",specimen="blood")])


def test_summary_counts_and_original_global_family(settings,populations):
    report = brief(settings,filters=BriefingFilters("ED",EC,"urine"),code="CIP")
    q = report.global_quality
    assert (q["combinations"],q["eligible"],q["analysis_limited"],q["insufficient_data"],q["no_data"]) == (4,1,1,0,2)
    assert len(report.findings)==2 and len(report.selected_findings)==1
    assert report.findings[0].kind=="BY-alert" and report.findings[1].kind=="preliminary"
    assert report.selected_quality["combinations"]==1
    selected=report.selected_findings[0].comparison
    full=AnomalyEngine(settings).scan(as_of_date=report.analysis_date)
    assert selected==next(r for r in full.results if r.filters==selected.filters)
    assert selected.correction_family_size==full.tested_comparisons==1
    assert selected.current.n_tested==40 and selected.current.resistance_pct==60
    assert selected.baseline.n_tested==40 and selected.baseline.resistance_pct==0


def test_supported_alert_not_lost_with_higher_preliminary_threshold(settings,populations):
    r=brief(settings,signal_threshold=100)
    assert not surveillance_signals(r.scan,100)
    assert len(r.findings)==1 and r.findings[0].kind=="BY-alert"


@pytest.mark.parametrize("ordering",ORDERINGS)
def test_queue_sort_deterministic_filters_and_rows(settings,populations,ordering):
    r=brief(settings)
    assert ordered_findings(r.findings,ordering)==ordered_findings(tuple(reversed(r.findings)),ordering)
    assert len(ordered_findings(r.findings,ordering,"Preliminary"))==1
    assert len(ordered_findings(r.findings,ordering,"Statistically supported"))==1
    rows=finding_rows(r.findings)
    assert rows[0]["Current S/R/N"]=="16/24/40"
    assert rows[1]["Shared patients"]==30
    assert rows[1]["Last relevant observation"]==date(2026,9,27)


def test_original_origin_navigation_scope_and_stale_evidence(settings,populations):
    r=brief(settings,filters=BriefingFilters("URO"))
    item=r.selected_findings[0]
    locator=origin_locator(item.comparison,item.kind,settings,MODES[0])
    assert locator["ward_code"]=="URO" and locator["organism"]==EC
    assert resolve_origin(locator,settings,MODES[0],7)==item.comparison
    assert resolve_origin(locator,settings,SCENARIO,7) is None
    assert resolve_origin(locator,settings,MODES[0],8) is None


def test_missing_first_isolate_does_not_use_later_test_and_sparse_status(settings,seed):
    seed([observation("a","2026-09-01",tests={}),observation("a","2026-09-02","R"),
          observation("b"),observation("old","2026-08-01")])
    r=brief(settings,code="CIP")
    q=r.global_quality
    assert q["current_missing_opportunities"]==1
    assert q["current_first_opportunities"]==2 and q["current_tested_opportunities"]==1
    assert q["insufficient_data"]==1 and q["eligible"]==0
    assert r.selected_analysis.current.percentage is None
    assert r.selected_analysis.current.n_observed==1


@pytest.mark.parametrize("count,status",[(0,"no_data"),(29,"insufficient_data"),(30,"ok")])
def test_insufficient_missing_and_real_zero(settings,seed,count,status):
    seed([observation(str(i)) for i in range(count)])
    r=brief(settings,code="CIP")
    p=r.selected_analysis.current
    assert p.status==status and p.n_observed==count
    assert p.percentage==(0 if count==30 else None)
    assert not r.findings


def test_freshness_tracks_source_and_selected_analysis_separately(settings,seed):
    seed([observation("a","2026-08-01"),observation("b","2026-09-27",ward_code="URO")])
    r=brief(settings,filters=BriefingFilters("ED"),code="CIP")
    assert r.latest_sample==date(2026,9,27)
    assert r.selected_analysis.latest_relevant_sample==date(2026,8,1)
    assert freshness(r,today=date(2026,10,17))["sample_age_days"]==20
    earlier=build_briefing(settings,as_of_date="2026-08-01")
    assert freshness(earlier)["latest_sample_after_analysis"]


@pytest.mark.parametrize("scope",["hospital","selected"])
def test_pdf_real_values_reproducibility_and_privacy(settings,populations,scope):
    r=brief(settings,filters=BriefingFilters("ED",EC,"urine"),code="CIP")
    before=None
    with connect(settings,read_only=True) as con:
        before=list(con.iterdump())
    data=render_pdf(r,settings,scope=scope)
    assert data==render_pdf(r,settings,scope=scope)
    text="\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)
    assert "AEI Sentinel" in text and "Synthetic Demo Data" in text
    assert "60.00%" in text and "S=16, R=24, N=40" in text
    assert r.digest(scope) in text
    assert "2026-09-27" in text and "2026-10-17T00:00:00+00:00" in text
    if scope=="selected":
        assert "Rolling 365 days" in text and "non-overlapping" in text
        annual=ResistanceEngine(settings).last_365_days(ResistanceFilters("CIP","ED",EC,"urine"),as_of_date=r.analysis_date)
        assert r.selected_analysis.annual.n_observed==annual.n_tested==80
        assert r.selected_analysis.annual.percentage==annual.resistance_pct==30
    manifest=json.dumps(r.to_dict(scope))
    for private in ("patient_id","culture_id","isolate_id","workspace=","restore_token","old0","new0",str(settings.database_path)):
        assert private not in text and private not in manifest
    with connect(settings,read_only=True) as con:
        assert list(con.iterdump())==before


def test_pdf_preserves_insufficient_status(settings,seed):
    seed([observation("PRIVATE_PATIENT",culture_id="PRIVATE_CULTURE",isolate_id="PRIVATE_ISOLATE")])
    r=brief(settings,code="CIP")
    text="\n".join(p.extract_text() for p in PdfReader(io.BytesIO(render_pdf(r,settings,scope="selected"))).pages)
    assert "Insufficient Data" in text and "No Data" in text
    assert "PRIVATE_" not in text and "0.00%" not in text


def test_changes_during_scan_and_pdf_rejected(settings,populations,monkeypatch):
    import src.briefing_pdf as module
    r=brief(settings)
    original=module.SimpleDocTemplate.build
    def mutate(doc,*args,**kwargs):
        original(doc,*args,**kwargs)
        with connect(settings) as con,transaction(con):
            con.execute("UPDATE data_version SET version=8")
    monkeypatch.setattr(module.SimpleDocTemplate,"build",mutate)
    with pytest.raises(DatasetChangedError):
        render_pdf(r,settings)
    with pytest.raises(DatasetChangedError):
        validate_revision(r,settings)
    with pytest.raises(DatasetChangedError):
        brief(settings,expected_version=7)


def test_version_change_between_service_calls_rejected(settings,populations,monkeypatch):
    original=AnomalyEngine.scan
    def scan(self,**kwargs):
        result=original(self,**kwargs)
        with connect(settings) as con,transaction(con):
            con.execute("UPDATE data_version SET version=8")
        return result
    monkeypatch.setattr(AnomalyEngine,"scan",scan)
    with pytest.raises(DatasetChangedError):
        brief(settings)


@pytest.mark.parametrize("mode",[HISTORICAL,SCENARIO])
def test_replay_reports_isolated_from_primary(settings,populations,mode):
    from src.database import database_summary
    before=database_summary(settings)
    replay=ReplaySession(settings,historical_plan(settings) if mode==HISTORICAL else scenario_plan())
    r=build_briefing(replay.settings,source=mode,code="CIP")
    assert not r.scan.results and r.data_version==0
    assert r.source_identity!=brief(settings).source_identity
    replay.step()
    r=build_briefing(replay.settings,source=mode,code="CIP")
    assert r.data_version==1 and r.latest_sample
    text="\n".join(p.extract_text() for p in PdfReader(io.BytesIO(render_pdf(r,replay.settings))).pages)
    assert mode in text and "REPLAY / ARTIFICIAL" in text
    assert database_summary(settings)==before
    with pytest.raises(DatasetChangedError):
        validate_revision(r,settings)
    replay.pause()
    replay.lease.close()


def test_full_scan_only_once_and_no_cell_query_loop(settings,populations,monkeypatch):
    import src.resistance as resistance
    calls=[]
    sql=[]
    scan=AnomalyEngine.scan
    def tracked(self,**kwargs):
        calls.append(1)
        return scan(self,**kwargs)
    original=resistance.connect
    @contextmanager
    def traced(*args,**kwargs):
        with original(*args,**kwargs) as con:
            con.set_trace_callback(sql.append)
            yield con
    monkeypatch.setattr(AnomalyEngine,"scan",tracked)
    monkeypatch.setattr(resistance,"connect",traced)
    r=brief(settings,code="CIP")
    assert len(calls)==1 and len(r.scan.results)==4
    assert sum(q.lstrip().upper().startswith("WITH") for q in sql)==3 # one scan + selected periods + groups


@pytest.mark.parametrize("kwargs",[{"source":"Unknown"},{"filters":BriefingFilters(specimen="blood"),"signal_threshold":101}])
def test_invalid_requests_rejected(settings,kwargs):
    with pytest.raises(ValueError):
        brief(settings,**kwargs)
