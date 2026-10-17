"""Selectable square-cell heatmap using supported Plotly scatter selections."""

import plotly.graph_objects as go

STATUS = {"ok": "Valid percentage", "no_data": "No Data", "insufficient_data": "Insufficient Data", "unavailable": "Unavailable: urine-only reporting"}


def cell_value(report, cell):
    return cell.level.resistance_pct if report.view == "level" and cell.status == "ok" else cell.difference_pp if cell.status == "ok" else None


def cell_hover(report, cell):
    def describe(label, item):
        rate = f"{item.resistance_pct:.2f}%" if item.resistance_pct is not None else STATUS[item.status]
        return (f"{label}: {item.start_date} – {item.end_date}<br>{rate}; S={item.n_susceptible}, R={item.n_resistant}, "
                f"Tested={item.n_tested}<br>Eligible first={item.n_first_isolates}; missing tests={item.n_first_isolates-item.n_tested}")
    text = f"{dict(report.wards)[cell.ward_code]} ({cell.ward_code}) · {dict(report.antibiotics)[cell.antibiotic_code]} ({cell.antibiotic_code})<br>"
    text += describe("Selected level period", cell.level) if report.view == "level" else describe("Current", cell.current)+"<br>"+describe("Historical baseline", cell.baseline)
    text += f"<br>Cell status: {STATUS[cell.status]}; minimum N={cell.current.minimum_sample_size}"
    if cell.difference_pp is not None and report.view == "change":
        text += f"<br>Descriptive change: {cell.difference_pp:+.2f} pp"
    if cell.indicator:
        text += "<br>Matching Preliminary Signal (descriptive)" if cell.indicator == "P" else "<br>Matching BY-adjusted alert (exploratory)"
    return text


def visible_antibiotics(report, *, hide_empty=False):
    return tuple((code,label) for code,label in report.antibiotics if not hide_empty or
        any(cell.antibiotic_code==code and cell_value(report,cell) is not None for cell in report.cells))


def matrix_chart(report, *, sort="name", scale_max=100, hide_empty=False):
    if scale_max not in (50,100):
        raise ValueError("Map scale must be 50 or 100 percent")
    wards = list(report.wards)
    if sort == "volume":
        wards.sort(key=lambda pair: (-max(((cell.current if report.view == "change" else cell.level).n_first_isolates for cell in report.cells if cell.ward_code == pair[0]), default=0), pair[1]))
    codes = [code for code, _ in visible_antibiotics(report,hide_empty=hide_empty)]
    displayed = [cell for cell in report.cells if cell.antibiotic_code in codes]
    fig = go.Figure()
    contrast = .55*(100 if report.view=="change" else scale_max)
    has_light = any(cell_value(report, cell) is not None and abs(cell_value(report, cell)) <= contrast for cell in displayed)
    for kind in ("light", "dark", "missing"):
        observed = kind != "missing"
        cells = [cell for cell in displayed if (cell_value(report, cell) is None if not observed else
                 cell_value(report, cell) is not None and (abs(cell_value(report, cell)) > contrast) == (kind == "dark"))]
        text_color = "white" if kind == "dark" else "#173354"
        texts = []
        for cell in cells:
            value = cell_value(report, cell)
            label = (f"{value:.1f}" if report.view == "level" else f"{value:+.1f}") if value is not None else {"no_data": "ND", "insufficient_data": "IN", "unavailable": "NA"}[cell.status]
            texts.append(label + (f"<br>{cell.indicator}" if cell.indicator else ""))
        values = [cell_value(report, cell) for cell in cells]
        color = dict(color=values, colorscale=[[0, "#E7F0FA"], [1, "#154679"]], cmin=0, cmax=scale_max,
                     showscale=(kind == "light" if has_light else kind == "dark"), colorbar=dict(title=f"Resistance %<br>0–{scale_max}"+(" focused" if scale_max==50 else ""), thickness=12, len=.8)) if observed else dict(color="#E7EAF0")
        if observed and report.view == "change":
            color.update(colorscale=[[0, "#356F9C"], [.5, "#F4F4F2"], [1, "#AD622D"]], cmin=-100, cmax=100, cmid=0,
                         colorbar=dict(title="Change (pp)", thickness=12, len=.8))
        fig.add_trace(go.Scatter(x=[cell.antibiotic_code for cell in cells], y=[cell.ward_code for cell in cells],
            mode="markers+text", marker=dict(symbol="square", size=38, line=dict(width=1, color="#CAD5E2"), **color),
            text=texts, textposition="middle center", textfont=dict(size=10, color=text_color),
            customdata=[[cell.ward_code, cell.antibiotic_code] for cell in cells], hovertext=[cell_hover(report, cell) for cell in cells],
            hovertemplate="%{hovertext}<extra></extra>", showlegend=False,
            selected=dict(marker=dict(opacity=1, size=42), textfont=dict(color=text_color)),
            unselected=dict(marker=dict(opacity=1), textfont=dict(color=text_color))))
    fig.update_layout(template="plotly_white", height=max(250, 46*len(wards)+80), margin=dict(l=70, r=30, t=35, b=45),
        clickmode="event+select", dragmode=False, hovermode="closest", plot_bgcolor="#F6F8FC",
        xaxis=dict(title=None, type="category", categoryorder="array", categoryarray=codes, side="top", fixedrange=True,
                   tickfont=dict(size=11), range=[-.6, max(.6, len(codes)-.4)], showgrid=False, zeroline=False),
        yaxis=dict(title=None, type="category", categoryorder="array", categoryarray=[code for code, _ in wards], autorange="reversed",
                   fixedrange=True, tickfont=dict(size=11), showgrid=False, zeroline=False))
    return fig
