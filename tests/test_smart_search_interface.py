"""Submit-only search, manual fallback, revision/session isolation and exact handoff."""

from datetime import date
import pytest
from streamlit.testing.v1 import AppTest

from src.smart_query import SmartQuery, QueryClarification
from src.local_inference import InferenceUnavailable
from src.database import connect, transaction
from test_app import APP, ui_data


def click(app, label):
    next(button for button in app.button if button.label == label).click().run(timeout=30)
    assert not app.error and not app.exception


@pytest.fixture
def interpreted(ui_data, monkeypatch):
    calls = []
    def interpret(self, text, catalog):
        calls.append(text)
        return SmartQuery(ward='ED', organism='Escherichia coli', antibiotic='CIP', age_min=40), {'wall_seconds': .1}
    monkeypatch.setattr('src.local_inference.OllamaInterpreter.interpret', interpret)
    return calls


def test_search_explicit_submission_reruns_and_exact_explore_transfer(interpreted):
    app = AppTest.from_file(str(APP)).run(timeout=30)
    assert not interpreted and not app.exception
    app.text_input(key='_smart_text').set_value('Show E. coli resistance to CIP in ED for patients 40+.')
    assert not interpreted and '_smart_result' not in app.session_state
    # No autocomplete component replaces the sentence or performs inference.
    assert app.text_input(key='_smart_text').value.endswith('40+.')
    click(app, 'Search')
    assert len(interpreted) == 1 and app.session_state['_smart_result'].period.n_observed == 30
    app.radio(key='_presentation_mode').set_value('Expert').run(timeout=30)
    assert len(interpreted) == 1
    click(app, 'Open Detailed Investigation')
    assert len(interpreted) == 1 and app.session_state['navigation'] == 'Explore'
    assert app.session_state['explorer_age_min'] == 40 and app.session_state['antibiotic_code'] == 'CIP'
    assert app.session_state['ward_code'] == 'ED' and app.session_state['analysis_date'] == date(2026, 9, 27)
    click(app, 'Smart Search')
    assert app.session_state['navigation'] == 'Overview'
    assert app.text_input(key='_smart_text').value == interpreted[0]


def test_manual_search_never_calls_ai_and_uses_real_results(ui_data, monkeypatch):
    def forbidden(*args): raise AssertionError('Manual Search invoked AI')
    monkeypatch.setattr('src.local_inference.OllamaInterpreter.interpret', forbidden)
    app = AppTest.from_file(str(APP)).run(timeout=30)
    app.selectbox(key='_smart_manual_organism').set_value('Escherichia coli')
    app.selectbox(key='_smart_manual_antibiotic').set_value('CIP')
    click(app, 'Run Manual Search')
    assert app.session_state['_smart_result'].period.n_observed == 30
    assert app.session_state['_smart_result'].period.n_positive == 7
    assert not app.error and not app.exception


@pytest.mark.parametrize('error', [InferenceUnavailable('Local AI is unavailable. Use Manual Search.'),
    QueryClarification('ambiguous')])
def test_failed_search_clears_old_results_and_retains_original_text(interpreted, monkeypatch, error):
    app = AppTest.from_file(str(APP)).run(timeout=30)
    app.text_input(key='_smart_text').set_value('Show E. coli resistance to CIP.')
    click(app, 'Search')
    def fail(*args): raise error
    monkeypatch.setattr('src.local_inference.OllamaInterpreter.interpret', fail)
    app.session_state['_smart_last_ai'] = -100
    click(app, 'Search')
    assert '_smart_result' not in app.session_state
    assert app.text_input(key='_smart_text').value == 'Show E. coli resistance to CIP.'
    assert app.info


def test_dataset_revision_invalidates_result_without_new_inference(interpreted, settings):
    app = AppTest.from_file(str(APP)).run(timeout=30)
    app.text_input(key='_smart_text').set_value('Show resistance.')
    click(app, 'Search')
    with connect(settings) as con, transaction(con):
        con.execute('UPDATE data_version SET version=2')
    click(app, 'Refresh data')
    assert '_smart_result' not in app.session_state and len(interpreted) == 1


def test_query_state_does_not_cross_sessions(interpreted):
    first = AppTest.from_file(str(APP)).run(timeout=30)
    first.text_input(key='_smart_text').set_value('My private search')
    click(first, 'Search')
    second = AppTest.from_file(str(APP)).run(timeout=30)
    assert '_smart_result' not in second.session_state
    assert second.text_input(key='_smart_text').value == ''
    assert len(interpreted) == 1
