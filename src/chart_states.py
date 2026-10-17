"""Presentation of existing reporting statuses; never calculate or estimate a rate."""

from dataclasses import dataclass
from math import isfinite


def percentage(point):
    value = getattr(point, 'percentage', getattr(point, 'resistance_pct', None))
    return value if point.status == 'ok' and value is not None and isfinite(value) else None


@dataclass(frozen=True)
class ChartAvailability:
    total: int
    valid: int
    insufficient: int
    empty: int
    unsupported: int
    minimum: int | None

    @property
    def status(self):
        if self.valid == 1:
            return 'single'
        if self.valid:
            return 'partial' if self.valid < self.total else 'available'
        if self.unsupported:
            return 'unsupported'
        return 'insufficient_data' if self.insufficient else 'no_data'


def availability(points, *, supported=True, minimum=None):
    points = tuple(points)
    thresholds = {p.minimum_sample_size for p in points}
    if minimum is None and len(thresholds) == 1:
        minimum = thresholds.pop()
    return ChartAvailability(len(points), sum(percentage(p) is not None for p in points),
        sum(p.status == 'insufficient_data' for p in points), sum(p.status == 'no_data' for p in points),
        sum(p.status == 'unsupported' for p in points) or (0 if supported else 1), minimum)


def explanation(state, frequency='reporting', *, aggregate=None, denominator='eligible tested first isolates'):
    """Shared web/PDF copy, including the distinction between a trend and an aggregate."""
    if state.status == 'unsupported':
        title = 'Unsupported combination'
        text = 'This organism, antibiotic, specimen or phenotype combination is not supported by the current analysis rules.'
    elif state.status == 'no_data':
        title = f'No Data for {frequency} values'
        text = 'No eligible observations with applicable test results are available in these periods. An unavailable percentage is not zero resistance.'
    elif state.status == 'insufficient_data':
        title = f'Insufficient data for {frequency} trends'
        text = f'None of the {frequency} periods contains enough laboratory observations to display a percentage.'
    elif state.status == 'single':
        title = 'Single valid observation'
        text = ('Only one period has a reportable percentage. The marker is an observation, not a continuous trend.'
            if frequency in ('weekly','monthly','reporting') else 'Only one group or comparison period has a reportable percentage. Unavailable values are not zero.')
    elif state.status == 'partial':
        title = 'Partial Data'
        text = ('Some periods have no reportable percentage. Gaps are not zero; lines do not bridge unavailable periods.'
            if frequency in ('weekly','monthly','reporting') else 'Some groups or comparison periods have no reportable percentage. Unavailable values are not zero.')
    else:
        title, text = 'Available observations', 'All periods have reportable percentages.'
    if state.status != 'unsupported' and state.minimum is not None:
        text += f' {state.valid} of {state.total} periods meet the minimum of {state.minimum} {denominator} per period.'
    if aggregate is not None and percentage(aggregate) is not None:
        text += f' The overall 28-day result is still available ({percentage(aggregate):.2f}%).'
    return title, text


def render_chart(points, factory, *, key, frequency='reporting', aggregate=None, supported=True,
                 minimum=None, monthly=None, monthly_factory=None, monthly_action=None,
                 denominator='eligible tested first isolates'):
    """Decide before creating Plotly; alternate periods are already-computed engine outputs."""
    import streamlit as st
    from src.design import expert
    state = availability(points, supported=supported, minimum=minimum)
    if not state.valid or state.status in ('single', 'partial'):
        title, text = explanation(state, frequency, aggregate=aggregate if not state.valid else None,denominator=denominator)
        st.info(f'**{title}**\n\n{text}')
    if expert():
        st.caption(f'Reporting periods: {state.valid} valid; {state.insufficient} Insufficient Data; '
            f'{state.empty} No Data; {state.unsupported} unsupported. Configured minimum: {state.minimum if state.minimum is not None else "not applicable"}.')
    if state.valid:
        st.plotly_chart(factory(), width='stretch', key=key)
    elif monthly is not None and state.status != 'unsupported':
        alternate = availability(monthly, supported=supported, minimum=minimum)
        if alternate.valid:
            if monthly_action is not None:
                st.button('View monthly trend', key=key+'_monthly', on_click=monthly_action)
            else:
                def show_monthly():
                    st.session_state[key+'_show_monthly']=True
                st.button('View monthly trend', key=key+'_monthly',on_click=show_monthly)
            if monthly_action is None and st.session_state.get(key+'_show_monthly'):
                render_chart(monthly, monthly_factory, key=key+'_monthly_chart', frequency='monthly', aggregate=aggregate,denominator=denominator)
        else:
            st.caption('Monthly aggregation also has no reportable percentages at the same minimum sample size. The threshold has not been reduced.')
    if state.valid:
        st.caption('Missing or insufficient percentages remain gaps. Counts are period-specific; do not add denominators across periods.')
    return state
