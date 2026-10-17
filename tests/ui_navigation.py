"""Real Phase 13 UI routes for retained Phase 1-12 behavioral regressions.

Legacy Dashboard tests exercise its retained Resistance Trends tool. New home
and legacy workspace normalization are tested separately, without this adapter.
"""

ROUTES={'Dashboard':('Explore','Resistance Trends'),'Detailed Analysis':('Explore','Detailed Period'),
    'Advanced Anomaly Explorer':('Explore','Investigation'),'Surveillance Map':('Explore','Surveillance Map'),
    'Surveillance Briefing':('Reports','Surveillance Briefing'),'Data Management':('Data & Settings',None)}


def navigate(app,page,view=None):
    destination,subview=ROUTES.get(page,(page,view))
    if app.sidebar.radio[0].value!=destination:
        app.sidebar.radio[0].set_value(destination).run(timeout=30)
    if destination in ('Explore','Reports') and subview:
        key='_explore_view' if destination=='Explore' else '_reports_view'
        if app.radio(key=key).value!=subview:
            app.radio(key=key).set_value(subview).run(timeout=30)
    return app


def source(app,mode):
    destination=app.session_state['navigation']
    view=app.session_state['explore_view' if destination=='Explore' else 'reports_view'] if destination in ('Explore','Reports') else None
    navigate(app,'Data & Settings')
    app.selectbox(key='data_source').set_value(mode).run(timeout=30)
    navigate(app,destination,view)
    return app


def replay_click(app,label):
    destination=app.session_state['navigation']
    view=app.session_state['explore_view' if destination=='Explore' else 'reports_view'] if destination in ('Explore','Reports') else None
    navigate(app,'Data & Settings')
    next(b for b in app.button if b.label==label).click().run(timeout=30)
    navigate(app,destination,view)
    return app
