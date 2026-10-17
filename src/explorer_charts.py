"""Aggregate-only charts. Missing percentages remain gaps."""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
from src.chart_states import percentage


def period_rows(report, periods):
    phenotype = report.mode == "phenotype"
    return [{"Period": label, "Start": item.start_date, "End": item.end_date,
        "Eligible first isolates": item.n_first_isolates,
        "Marker negative" if phenotype else "S": item.n_negative,
        "Marker positive" if phenotype else "R": item.n_positive,
        "Eligible denominator" if phenotype else "Tested": item.n_observed,
        "Missing tests": None if phenotype else item.missing_tests,
        "Phenotype (%)" if phenotype else "Resistance (%)": item.percentage,
        "Status": item.status, "Distinct observed patients": item.distinct_observed_patients,
        "Wilson lower (%)": item.confidence_interval.lower_pct if item.confidence_interval else None,
        "Wilson upper (%)": item.confidence_interval.upper_pct if item.confidence_interval else None,
        "Minimum N": item.minimum_sample_size, "Data version": report.data_version,
        } for label, item in periods]


def trend_chart(report, periods):
    phenotype = report.mode == "phenotype"
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    x = [p.end_date for p in periods]
    detail = [[p.n_positive, p.n_negative, p.n_observed, p.n_first_isolates, p.status, str(p.start_date), str(p.end_date)] for p in periods]
    fig.add_trace(go.Bar(x=x, y=[p.n_observed if percentage(p) is not None else None for p in periods], name="Eligible first isolates" if phenotype else "Tested first isolates",
        marker_color="#C7DBF7", opacity=.55, customdata=detail, hovertemplate="%{x}<br>N=%{y}<extra></extra>"), secondary_y=True)
    if phenotype:
        fig.add_trace(go.Bar(x=x, y=[p.n_positive if percentage(p) is not None else None for p in periods], name="Marker-positive first isolates", marker_color="#75B8CA", opacity=.55), secondary_y=True)
    fig.add_trace(go.Scatter(x=x, y=[percentage(p) for p in periods], connectgaps=False, mode="markers" if sum(percentage(p) is not None for p in periods)==1 else "lines+markers", zorder=3,
        name="Phenotype (%)" if phenotype else "Resistance (%)", line_color="#2457A7", customdata=detail,
        hovertemplate=("%{customdata[5]} – %{customdata[6]}<br>%{y:.2f}%<br>" +
            ("Positive=%{customdata[0]}, Negative=%{customdata[1]}, Eligible=%{customdata[2]}" if phenotype else
             "R=%{customdata[0]}, S=%{customdata[1]}, Tested=%{customdata[2]}") + "<br>First=%{customdata[3]}<br>%{customdata[4]}<extra></extra>")), secondary_y=False)
    for period, color, label in ((report.baseline, "#DCE8F8", "Historical baseline"), (report.current, "#C4E9E1", "Current 28 days")):
        if period.start_date:
            fig.add_vrect(x0=period.start_date.isoformat(), x1=period.end_date.isoformat(), fillcolor=color, opacity=.25,
                          layer="below", line_width=0, annotation_text=label, annotation_position="top left")
    fig.update_yaxes(title_text="Percentage", range=[0, 100], secondary_y=False)
    fig.update_yaxes(title_text="Absolute first-isolate count", rangemode="tozero", secondary_y=True)
    fig.update_layout(template="plotly_white", height=390, margin=dict(l=15, r=15, t=35, b=15),
                      legend=dict(orientation="h", y=-.22), barmode="overlay", hovermode="x unified")
    return fig


def group_chart(report):
    periods = [p for _, p in report.groups]
    fig = go.Figure(go.Bar(x=[label for label, _ in report.groups], y=[percentage(p) for p in periods],
        marker_color="#4477BA", name="Phenotype (%)" if report.mode == "phenotype" else "Resistance (%)",
        customdata=[[p.n_positive, p.n_negative, p.n_observed, p.status] for p in periods],
        hovertemplate=("%{x}<br>%{y:.2f}%<br>" + ("Marker positive=%{customdata[0]}, Marker negative=%{customdata[1]}, Eligible=%{customdata[2]}" if report.mode == "phenotype" else "R=%{customdata[0]}, S=%{customdata[1]}, Tested=%{customdata[2]}") + "<extra></extra>"),
        error_y=dict(type="data", symmetric=False,
            array=[p.confidence_interval.upper_pct-p.percentage if p.confidence_interval else None for p in periods],
            arrayminus=[p.percentage-p.confidence_interval.lower_pct if p.confidence_interval else None for p in periods])))
    fig.update_layout(template="plotly_white", height=330, margin=dict(l=15, r=15, t=20, b=20), yaxis=dict(title="Percentage", range=[0, 100]))
    return fig
