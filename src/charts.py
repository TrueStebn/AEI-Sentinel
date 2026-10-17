"""Plotly figures from aggregate outputs, without recalculating resistance."""

import plotly.graph_objects as go

from src.chart_states import percentage
from src.reporting import STATUS_LABELS, period_label, rate_label
from src.resistance import ResistanceResult, ResistanceTimeSeries

BLUE = "#2563EB"


def _layout(fig: go.Figure) -> go.Figure:
    fig.update_layout(template="plotly_white", height=330, margin=dict(l=12, r=18, t=20, b=16),
                      font=dict(family="Arial, sans-serif", color="#334155"),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      showlegend=False, hoverlabel=dict(bgcolor="white"))
    fig.update_yaxes(title="Resistance (%)", range=[0, 100], gridcolor="#E8EEF7", ticksuffix="%")
    fig.update_xaxes(showgrid=False)
    return fig


def _hover(result):
    return [period_label(result), result.n_susceptible, result.n_resistant,
            result.n_tested, STATUS_LABELS[result.status]]


HOVER = ("%{customdata[0]}<br>Resistance: %{y:.2f}%<br>"
         "S: %{customdata[1]} · R: %{customdata[2]} · Tested: %{customdata[3]}"
         "<br>%{customdata[4]}<extra></extra>")


def trend_chart(series: ResistanceTimeSeries) -> go.Figure:
    values = [percentage(p) for p in series.points]
    fig = go.Figure(go.Scatter(
        x=[p.end_date for p in series.points], y=values, mode="markers" if sum(v is not None for v in values)==1 else "lines+markers", connectgaps=False,
        line=dict(color=BLUE, width=2.5), marker=dict(size=6, symbol="circle"),
        customdata=[_hover(p) for p in series.points], hovertemplate=HOVER, name="Resistance",
    ))
    if not any(value is not None for value in values):
        message = "Insufficient Data" if any(p.n_tested for p in series.points) else "No Data"
        fig.add_annotation(x=0.5, y=0.55, xref="paper", yref="paper", showarrow=False,
                           text=f"<b>{message}</b><br>No periods meet the minimum sample size",
                           font=dict(size=15, color="#64748B"))
    fig.update_xaxes(title="Reporting period end", type="date")
    return _layout(fig)


def comparison_chart(recent: ResistanceResult, annual: ResistanceResult) -> go.Figure:
    results = [recent, annual]
    labels = ["Last 28 Days", "Last 365 Days"]
    fig = go.Figure(go.Bar(
        x=labels, y=[r.resistance_pct if r.status == "ok" else None for r in results],
        marker_color=[BLUE, "#0891B2"], width=0.5,
        customdata=[_hover(r) for r in results], hovertemplate=HOVER,
        text=[rate_label(r) if r.status == "ok" else "" for r in results], textposition="outside",
    ))
    for label, result in zip(labels, results):
        if result.status != "ok":
            fig.add_annotation(x=label, y=0.5, yref="paper", showarrow=False,
                               text=f"{rate_label(result)}<br>Tested: {result.n_tested}",
                               font=dict(color="#64748B"))
    return _layout(fig)


def anomaly_comparison_chart(assessment) -> go.Figure:
    results = [assessment.baseline, assessment.current]
    intervals = [assessment.baseline_ci, assessment.current_ci]
    labels = ["Historical Baseline", "Current 28 Days"]
    upper, lower = [], []
    for result, ci in zip(results, intervals):
        upper.append(max(0.0, ci.upper_pct - result.resistance_pct) if ci else 0.0)
        lower.append(max(0.0, result.resistance_pct - ci.lower_pct) if ci else 0.0)
    fig = go.Figure(go.Bar(
        x=labels, y=[r.resistance_pct for r in results], marker_color=["#64748B", BLUE], width=0.5,
        customdata=[_hover(r) for r in results], hovertemplate=HOVER,
        text=[rate_label(r) if r.status == "ok" else "" for r in results], textposition="outside",
        error_y=dict(type="data", symmetric=False, array=upper, arrayminus=lower,
                     color="#334155", thickness=1.5, width=5),
    ))
    for label, result in zip(labels, results):
        if result.resistance_pct is None:
            fig.add_annotation(x=label, y=0.5, yref="paper", showarrow=False,
                               text=f"{rate_label(result)}<br>Tested: {result.n_tested}")
    return _layout(fig)
