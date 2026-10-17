"""Submit-only natural language search and a separate, AI-free manual form."""

from datetime import date
from html import escape
from time import monotonic

import pandas as pd
import streamlit as st

from src.analysis_service import DatasetChangedError
from src.chart_states import render_chart
from src.explorer_charts import trend_chart
from src.investigations import open_investigation
from src.local_inference import InferenceUnavailable, OllamaInterpreter
from src.smart_query import INTENTS, PERIODS, SmartQuery, QueryClarification
from src.smart_search import execute_query, source_identity, transfer_to_explore

INTENT_LABELS = dict(resistance_analysis='Resistance summary', historical_comparison='Current vs historical baseline',
    patient_group_comparison='Patient-group comparison (descriptive)', resistance_trend='Monthly resistance trend', anomaly_scan='Original anomaly scan (increases)')
PERIOD_LABELS = dict(current='Last 28 days', annual='Rolling 365 days', baseline='Historical baseline (preceding 365 days)')


def _context(settings, catalog):
    return dict(source=st.session_state.get('data_source', 'Laboratory Dataset'),
                as_of=st.session_state.get('analysis_date') or catalog.latest_sample)


def _open(result, settings, catalog):
    from src.workspace_interface import autosave_workspace
    transfer_to_explore(st.session_state, result, source_identity(settings, catalog, **_context(settings, catalog)))
    autosave_workspace()


def _submit(text, settings, catalog, *, manual=None):
    # Results are session-local, never cached globally or persisted in workspace URLs.
    st.session_state.pop('_smart_result', None)
    st.session_state.pop('_smart_message', None)
    now = monotonic()
    if manual is None and now - st.session_state.get('_smart_last_ai', -100) < 2:
        st.session_state['_smart_message'] = 'Please wait a moment before another AI search, or use Manual Search.'
        return
    try:
        context = _context(settings, catalog)
        if manual is None:
            st.session_state['_smart_original_text'] = text
            st.session_state['_smart_last_ai'] = now
            with st.spinner('Interpreting your question locally...'):
                query, timing = OllamaInterpreter().interpret(text, catalog)
            st.session_state['_smart_timing'] = timing
        else:
            query = manual
        with st.spinner('Calculating the existing surveillance analysis...'):
            result = execute_query(query, settings, catalog, **context)
        st.session_state['_smart_result'] = result
        st.session_state['_smart_query'] = query
        # A submitted manual form already contains this query. Re-seeding it on
        # the next submission would overwrite the visitor's newly edited values.
        st.session_state['_smart_form_seed'] = query.to_dict() if manual is not None else None
    except (QueryClarification, InferenceUnavailable, DatasetChangedError, ValueError) as exc:
        st.session_state['_smart_message'] = str(exc)


def _manual_form(settings, catalog, query=None):
    query = query or SmartQuery(organism=catalog.default_organism, antibiotic=catalog.default_antibiotic)
    seed = query.to_dict()
    if st.session_state.get('_smart_form_seed') != seed or '_smart_manual_period' not in st.session_state:
        values = dict(intent=query.intent, ward=query.ward or '__all__', organism=query.organism or '__all__',
            antibiotic=query.antibiotic or '__all__', specimen=query.specimen or '__all__', sex=query.sex or '__all__',
            period=query.period, group=query.group_by or 'sex', metric=query.metric,
            use_min=query.age_min is not None, use_max=query.age_max is not None,
            age_min=query.age_min if query.age_min is not None else 0,
            age_max=query.age_max if query.age_max is not None else 130,
            as_of=date.fromisoformat(query.as_of) if query.as_of else (_context(settings, catalog)['as_of'] or date.today()))
        for key, value in values.items():
            st.session_state['_smart_manual_' + key] = value
        st.session_state['_smart_form_seed'] = seed
    intent = st.selectbox('Analysis type', INTENTS, key='_smart_manual_intent', format_func=INTENT_LABELS.get)
    group = st.selectbox('Comparison dimension', ['sex', 'age', 'ward'], key='_smart_manual_group',
        help='Leave the compared dimension unrestricted; other filters are retained.') if intent == 'patient_group_comparison' else None
    allowed_periods = ('current',) if intent in ('anomaly_scan', 'historical_comparison') else PERIODS
    if st.session_state['_smart_manual_period'] not in allowed_periods:
        st.session_state['_smart_manual_period'] = 'current'
    with st.form('smart_manual', border=False):
        st.caption('No AI is used. These controls also let you correct an interpreted query. Changes are applied only when you submit.')
        left, right = st.columns(2)
        with left:
            ward = st.selectbox('Healthcare unit', ['__all__', *catalog.wards], key='_smart_manual_ward', format_func=lambda v: catalog.wards.get(v, 'All healthcare units')) if group != 'ward' else '__all__'
            organism = st.selectbox('Organism', ['__all__', *catalog.organisms], key='_smart_manual_organism', format_func=lambda v: 'Not specified' if v == '__all__' else v)
            antibiotic = st.selectbox('Antibiotic', ['__all__', *catalog.antibiotics], key='_smart_manual_antibiotic', format_func=lambda v: catalog.antibiotics.get(v, 'Not specified'))
            specimen = st.selectbox('Specimen type', ['__all__', *catalog.specimens], key='_smart_manual_specimen', format_func=lambda v: 'All specimen types' if v == '__all__' else v.title())
        with right:
            sex = st.selectbox('Sex', ['__all__', 'F', 'M'], key='_smart_manual_sex', format_func=lambda v: {'__all__': 'All sexes', 'F': 'Female', 'M': 'Male'}[v]) if intent != 'anomaly_scan' and group != 'sex' else '__all__'
            period = st.selectbox('Analysis period', allowed_periods, key='_smart_manual_period', format_func=PERIOD_LABELS.get)
            metric = st.selectbox('Requested measure', ['percentage', 'resistant_count'], key='_smart_manual_metric', format_func=lambda v: {'percentage': 'Resistance percentage', 'resistant_count': 'Resistant first-isolate count'}[v])
            as_of = st.date_input('Analysis cutoff', key='_smart_manual_as_of', max_value=catalog.latest_sample or date.today())
        use_min = use_max = False
        age_min = age_max = None
        if intent != 'anomaly_scan' and group != 'age':
            low, high = st.columns(2)
            with low:
                use_min = st.checkbox('Restrict minimum age', key='_smart_manual_use_min')
                age_min = st.number_input('Minimum age (inclusive)', min_value=0, max_value=130, step=1, key='_smart_manual_age_min')
            with high:
                use_max = st.checkbox('Restrict maximum age', key='_smart_manual_use_max')
                age_max = st.number_input('Maximum age (inclusive)', min_value=0, max_value=130, step=1, key='_smart_manual_age_max')
        st.caption('Age is measured at sampling. For older than 65 use minimum 66; for younger than 40 use maximum 39. Current/baseline comparisons retain their fixed windows.')
        submitted = st.form_submit_button('Run Manual Search', width='stretch')
    if submitted:
        selected = lambda value: None if value == '__all__' else value
        manual = SmartQuery(intent, selected(ward), selected(organism), selected(antibiotic), selected(specimen), selected(sex),
            age_min if use_min else None, age_max if use_max else None, period,
            group if intent == 'patient_group_comparison' else None, metric, as_of.isoformat())
        _submit('', settings, catalog, manual=manual)


def _recognised(query, catalog, result):
    st.markdown('**Interpreted query**')
    age = 'All ages'
    if query.age_min is not None and query.age_max is not None:
        age = f'{query.age_min} to {query.age_max} (inclusive)'
    elif query.age_min is not None:
        age = f'{query.age_min} and older'
    elif query.age_max is not None:
        age = f'{query.age_max} and younger'
    rows = dict(Analysis=INTENT_LABELS[query.intent], Unit=catalog.wards.get(query.ward, 'All healthcare units'),
        Organism=query.organism or 'All organisms', Antibiotic=catalog.antibiotics.get(query.antibiotic, 'All antibiotics'),
        Specimen=query.specimen or 'All specimens', Sex={'M': 'Male', 'F': 'Female'}.get(query.sex, 'All sexes'),
        Age=age, Period=PERIOD_LABELS[query.period], Measure=query.metric.replace('_', ' ').title(),
        Source=result.identity[1], Revision=str(result.identity[2]), Cutoff=str(result.report.analysis_date or 'No Data'))
    if query.group_by:
        rows['Compare by'] = query.group_by.title()
    st.html('<dl class="aei-query-parameters">' + ''.join(
        f'<div><dt>{escape(key)}</dt><dd>{escape(value)}</dd></div>' for key, value in rows.items()) + '</dl>')


def _results(result, settings, catalog):
    query, report = result.query, result.report
    _recognised(query, catalog, result)
    st.caption('To correct these parameters, open Build a query manually. Your original question is preserved.')
    if query.intent == 'anomaly_scan':
        alerts = [item for item in result.findings if item in report.alerts]
        st.write(f'{len(result.findings)} exact comparisons in scope; {len(alerts)} supported alerts from the original complete surveillance family.')
        from src.signals import surveillance_signals
        preliminary = surveillance_signals(report, settings.signal_minimum_change_pp)
        candidates = [(item, 'BY-alert') for item in alerts]
        candidates.extend((signal.comparison, 'preliminary') for signal in preliminary if signal.comparison in result.findings and signal.comparison not in alerts)
        st.caption('Filtering this display does not recompute the BY family. No alert does not establish absence of resistance or clinical safety.')
        for index, (item, kind) in enumerate(candidates[:10]):
            st.write(f'{item.filters.ward_code} / {item.filters.organism} / {item.filters.antibiotic_code} / {item.filters.specimen} · {kind}')
            st.button('Open Detailed Investigation', key=f'_smart_finding_{index}', on_click=open_investigation, args=(item, kind, settings))
        if len(candidates) > 10:
            st.caption(f'Showing the first 10 of {len(candidates)} findings. Review the full scan in Reports.')
        return
    period = result.period
    columns = st.columns(3)
    columns[0].metric('Observed resistance' if query.metric == 'percentage' else 'Resistant first isolates',
        (f'{period.percentage:.2f}%' if period.percentage is not None else period.status.replace('_', ' ').title()) if query.metric == 'percentage' else str(period.n_positive))
    columns[1].metric('Tested first isolates', str(period.n_observed))
    columns[2].metric('Data sufficiency', 'Sufficient Data' if period.status == 'ok' else period.status.replace('_', ' ').title())
    st.caption(f'{period.start_date} to {period.end_date} · R / S / tested: {period.n_positive} / {period.n_negative} / {period.n_observed}. Counts below the minimum are descriptive; missing percentages are not zero.')
    if period.confidence_interval:
        interval = period.confidence_interval
        st.caption(f'{100 * interval.confidence_level:g}% Wilson CI: {interval.lower_pct:.2f}% to {interval.upper_pct:.2f}%.')
    if query.period == 'current':
        baseline = report.baseline
        st.write(f'Historical baseline ({baseline.start_date} to {baseline.end_date}): ' +
                 (f'{baseline.percentage:.2f}%' if baseline.percentage is not None else baseline.status.replace('_', ' ').title()))
        st.write(report.assessment.explanation)
        st.caption('Subgroup assessment: ' + report.assessment.status.replace('_', ' ') + '. This selected exploratory comparison is not a BY-adjusted surveillance alert.')
    for message in report.assessment.limitations:
        st.caption(message)
    if query.intent == 'patient_group_comparison':
        st.table(pd.DataFrame([{'Group': label, 'Resistance (%)': item.percentage,
            'Resistant first isolates': item.n_positive, 'Tested': item.n_observed, 'Status': item.status.replace('_', ' ')} for label, item in report.groups]))
        st.caption('Each group ranks its own first isolates. Do not add group denominators. Differences are descriptive; no between-group significance test is performed.')
    st.caption('Monthly context: the existing engine displays its full current-plus-historical timeline, independently ranked per calendar bucket. This is contextual evidence, not a new calculation for the selected window.')
    render_chart(report.monthly, lambda: trend_chart(report, report.monthly), key='smart_trend',
        frequency='monthly', aggregate=period, supported=report.supported, minimum=period.minimum_sample_size,
        denominator='eligible tested first isolates')
    st.caption('First-isolate selection, S/R eligibility, sample thresholds and historical windows follow the existing engines. Findings do not establish infection, causality or treatment efficacy.')
    st.button('Open Detailed Investigation', key='_smart_open', on_click=_open, args=(result, settings, catalog), type='primary')


def render_smart_search(settings, catalog):
    identity = source_identity(settings, catalog, **_context(settings, catalog))
    if st.session_state.get('_smart_identity') != identity:
        for key in ('_smart_result', '_smart_query', '_smart_message', '_smart_form_seed', '_smart_timing'):
            st.session_state.pop(key, None)
        st.session_state['_smart_identity'] = identity
    st.html('''<style>
    .st-key-smart_search{max-width:1050px;margin-left:auto;margin-right:auto;background:#fff;border:1px solid #D7E1EB;border-radius:14px;padding:22px;}
    .st-key-smart_search h3{text-align:center;}
    .st-key-smart_search [data-testid="stForm"]{border:0;padding:0;}
    .aei-query-parameters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px 20px;margin:0;}
    .aei-query-parameters div{min-width:0;}.aei-query-parameters dt{font-size:13px;color:#365169;}
    .aei-query-parameters dd{margin:2px 0 0;font-size:14px;font-weight:600;overflow-wrap:anywhere;}
    @media(max-width:800px){.aei-query-parameters{grid-template-columns:repeat(2,minmax(0,1fr));}}
    </style>''')
    with st.container(key='smart_search'):
        st.subheader('Smart Search')
        st.caption('Ask about organisms, antibiotics, patients, departments, resistance trends or anomalies. Interpretation stays on this computer.')
        with st.form('smart_natural', border=False):
            st.session_state.setdefault('_smart_text', st.session_state.get('_smart_original_text', ''))
            text = st.text_input('Your surveillance question', key='_smart_text', max_chars=1200,
                placeholder='Show E. coli resistance to ciprofloxacin in Urology among patients aged 65 and older during the last 28 days.',
                help='Type a complete English question. Press Enter or Search to submit. Typing never runs AI inference.')
            submitted = st.form_submit_button('Search', type='primary', width='stretch')
        st.caption('Example: Show the monthly trend for E. coli resistance to ampicillin.')
        if submitted:
            _submit(text, settings, catalog)
        with st.expander('Build a query manually', expanded=False):
            _manual_form(settings, catalog, st.session_state.get('_smart_query'))
        if st.session_state.get('_smart_message'):
            st.info(st.session_state['_smart_message'])
        result = st.session_state.get('_smart_result')
        if result is not None and result.identity == identity:
            _results(result, settings, catalog)
