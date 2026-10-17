"""Guided surveillance home: units and original findings, never pooled rates."""

from html import escape
import pandas as pd
import streamlit as st

from src.briefing import quality_summary,finding_rows
from src.design import expert
from src.investigations import open_investigation
from src.overview import build_overview
from src.ux_state import select_department,go,department_report,explore_department
from src.presentation import expert_action,limitations


def finding_card(item,settings,*,key):
    r,f=item.comparison,item.comparison.filters
    with st.container(border=True,key=key):
        supported=item.kind=='BY-alert'
        st.html(f'<div class="aei-finding {"aei-supported" if supported else ""}">{"Statistically supported alert" if supported else "Preliminary finding · descriptive"}</div>'+
            f'<div class="aei-finding-title">{escape(f.ward_code)} · {escape(f.organism)}</div>'+
            f'<div class="aei-finding-values">{escape(f.antibiotic_code)} / {escape(f.specimen)} · <b>{r.difference_pp:+.2f} pp</b><br>'+
            f'Historical {r.baseline.resistance_pct:.2f}% → Current {r.current.resistance_pct:.2f}%</div>')
        limitations(r.limitations)
        if not expert() and not supported:
            st.caption('A change was observed and may deserve further investigation. This is not a statistically supported alert.')
        if expert():
            st.caption(f"Current S/R/N {r.current.n_susceptible}/{r.current.n_resistant}/{r.current.n_tested}; "
                f"baseline {r.baseline.n_susceptible}/{r.baseline.n_resistant}/{r.baseline.n_tested}.")
            st.caption(f"Original BY-adjusted p: {r.adjusted_p_value if r.adjusted_p_value is not None else 'withheld'}; family {r.correction_family_size}.")
        st.button('Investigate',key=key+'_open',on_click=open_investigation,args=(r,item.kind,settings),width='stretch')


def unit_cards(units):
    with st.container(horizontal=True,wrap=True,gap='small'):
        for unit in units:
            selected=st.session_state.get('ward_code')==unit.code
            with st.container(border=True,width=240,height=290,key='unit_'+unit.code):
                st.button(unit.name,key='select_unit_'+unit.code,on_click=select_department,args=(unit,),width='stretch',
                    type='primary' if selected else 'secondary',help=f"Select {unit.name} ({unit.code})")
                st.html(f'<div class="aei-unit-detail"><b>{escape(unit.code)}</b> · {"Inpatient" if unit.ward_type=="in" else "Outpatient"}<br>'
                    f'</div><div class="aei-culture-count">{unit.cultures_28d:,}</div><div class="aei-unit-caption">cultures · last 28 days'
                    f'{" · No Data" if not unit.cultures_28d else ""}<br>{unit.preliminary_count} preliminary {"finding" if unit.preliminary_count==1 else "findings"}'
                    f'<br>Latest sample: {unit.latest_sample or "No Data"}</div>'
                    f'<div class="aei-unit-selection {"aei-unit-selected" if selected else ""}">{"Selected department" if selected else ""}</div>')


def render_overview(settings,catalog):
    from src.smart_search_interface import render_smart_search
    render_smart_search(settings,catalog)
    report=build_overview(settings,catalog=catalog,as_of_date=st.session_state.get('analysis_date'),
        expected_version=catalog.version,source=st.session_state.get('data_source','Laboratory Dataset'),
        signal_threshold=st.session_state.get('signal_threshold',settings.signal_minimum_change_pp))
    st.html('<section><div class="aei-kicker">Overview</div><h2 class="aei-overview-title">See what needs a closer look</h2>'+
        '<p class="aei-intro">Choose a healthcare unit to explore patterns, or investigate a finding.</p></section>')
    with st.container(key='overview_metrics'):
        cols=st.columns(4)
        for col,label,value in zip(cols,('Healthcare units','Preliminary signals','Supported alerts','Latest available sample'),
            (len(report.units),len(report.preliminary),len(report.scan.alerts),str(report.latest_sample or 'No Data'))):
            col.metric(label,value,border=True)
    st.caption('Source-wide counts. Preliminary = observed change for review. Supported = full statistical alert criteria met. Counts can overlap; do not add them.')
    if not expert() and not report.scan.alerts:
        st.caption('No change met the full statistical alert criteria. This does not mean resistance is absent; some comparisons cannot be tested reliably.')
    if not catalog.latest_sample:
        st.warning('No Data. This source has no laboratory observations yet. Open Data & Settings to import or advance Replay.')
    selected=next((u for u in report.units if u.code==st.session_state.get('ward_code')),None)
    left,right=st.columns([2,1],gap='large')
    with left,st.container(key='overview_units'):
        for title,institution in (('Hospital Departments','hospital'),('Primary Care Centres','primary_care')):
            units=tuple(u for u in report.units if u.institution==institution)
            if units:
                st.markdown(f'### {title}')
                unit_cards(units)
        if not report.units:
            st.info('No healthcare units are available in this source yet.')
        st.caption('Culture counts include distinct cultures collected in the last 28 days, including negative cultures. They are not counts of infected patients. No resistance percentage is pooled across unrelated tests.')
        if selected:
            with st.container(border=True,key='selected_department'):
                st.subheader(f'{selected.name} · {selected.code}')
                st.write(f'{selected.type_label} · {"Inpatient" if selected.ward_type=="in" else "Outpatient"}')
                st.write(f'**{selected.cultures_28d:,} cultures in 28 days** · Latest sample: {selected.latest_sample or "No Data"}')
                st.write(f'{selected.preliminary_count} preliminary signals · {selected.supported_count} supported alerts. These counts can overlap.')
                q=quality_summary(tuple(r for r in report.scan.results if r.filters.ward_code==selected.code))
                if expert():
                    st.info(f"Interpret with care: {q['analysis_limited']} comparisons have adequate N but known patient dependence; "
                        f"{q['insufficient_data']} have insufficient observations; {q['no_data']} have no tested data in at least one period. No alert does not establish absence of change or clinical safety.")
                else:
                    st.info(f"{q['analysis_limited']} comparisons are limited by repeated patients; {q['insufficient_data']} need more observations; "
                        f"{q['no_data']} have no tested results in at least one period. No alert does not establish absence of change or clinical safety.")
                a,b=st.columns(2)
                a.button('Explore '+selected.name,on_click=explore_department,type='primary',width='stretch')
                b.button('Department-focused report',on_click=department_report,width='stretch')
                for i,item in enumerate(report.findings):
                    if item.comparison.filters.ward_code==selected.code:
                        finding_card(item,settings,key=f'department_finding_{i}')
    with right,st.container(key='overview_side'):
        st.markdown('### Recent Changes')
        st.caption('Original surveillance findings requiring review. Ordered by the existing review priority, not by clinical risk.')
        for i,item in enumerate(report.findings[:4]):
            finding_card(item,settings,key=f'overview_finding_{i}')
        if not report.findings:
            st.info('No findings meet the current surveillance rules. Check data coverage; this does not prove absence of change.')
        if len(report.findings)>4:
            st.button('Review all findings',on_click=go,args=('Reports','Surveillance Briefing'))
        with st.container(border=True,key='data_status'):
            st.markdown('### Data Status')
            st.write(f'**{report.source} · Revision {report.data_version}**')
            st.write(f'Latest laboratory sample: **{report.latest_sample or "No Data"}**')
            st.caption(f'Last successful import (UTC): {report.last_successful_import or "No completed import"}. This is an ingestion timestamp, not a sample date.')
            st.caption('Synthetic source. '+('Isolated replay; not a live hospital feed.' if report.source!='Laboratory Dataset' else 'Local laboratory demonstration dataset.'))
            st.caption(f'Analysis date: {report.analysis_date or "No Data"}. Source observations after that date are excluded from comparisons and unit activity.')
            st.button('Data & Settings',key='data_status_settings',on_click=go,args=('Data & Settings',),width='stretch')
    if not expert():
        st.caption('No Data means no tested results. Insufficient Data means too few observations for a reliable percentage. Neither means zero resistance.')
        expert_action('overview')
        return
    with st.expander('Evidence quality and terminology'):
        q=quality_summary(report.scan.results)
        st.write(f"Original scan: {q['combinations']} observed exact combinations; {q['eligible']} eligible tests in the global BY family. "
            f"{q['analysis_limited']} dependence-limited, {q['insufficient_data']} insufficient and {q['no_data']} no-data comparisons.")
        st.write('Resistance is the percentage of tested first isolates reported R. An isolate is an organism recovered from a culture. The baseline is the preceding non-overlapping 365 days. Patient overlap means a person appears in both comparison periods, limiting independent-sample testing.')
        st.write('No Data means absent tested observations; Insufficient Data means the minimum sample requirement is not met. Neither is a zero resistance rate. Findings do not establish causality, treatment efficacy or clinical danger.')
        if expert():
            st.dataframe(pd.DataFrame([dict(Department=u.name,Code=u.code,Type=u.type_label,Cultures_28d=u.cultures_28d,
                Latest_sample=u.latest_sample,Preliminary=u.preliminary_count,Supported=u.supported_count) for u in report.units]),hide_index=True,width='stretch')
            st.dataframe(pd.DataFrame(finding_rows(report.findings)),hide_index=True,width='stretch')
