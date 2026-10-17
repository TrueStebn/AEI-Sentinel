"""Public-demo policy is enforced below UI controls; local workflow is unchanged."""

import hashlib
from dataclasses import replace
import pytest

from src.database import connect, initialize_database, database_summary
from src.data_loader import import_csv_bundle, import_directory
from src.workspaces import WorkspaceStore
from src.simulator import ReplaySession, scenario_plan
from streamlit.testing.v1 import AppTest
from test_app import APP, ui_data


def test_shared_source_cannot_be_written_or_imported_in_public_mode(settings, bundle, monkeypatch):
    monkeypatch.setenv('AEI_DATABASE_PATH', str(settings.database_path))
    before = hashlib.sha256(settings.database_path.read_bytes()).hexdigest()
    monkeypatch.setenv('AEI_PUBLIC_DEMO', '1')
    with pytest.raises(RuntimeError, match='prepared synthetic'):
        initialize_database(settings)
    with pytest.raises(RuntimeError, match='read-only'):
        with connect(settings) as con:
            con.execute('DELETE FROM patients')
    with pytest.raises(RuntimeError, match='read-only'):
        import_csv_bundle(bundle, settings)
    with pytest.raises(RuntimeError, match='read-only'):
        import_directory(settings.dataset_path / 'missing', settings)
    assert hashlib.sha256(settings.database_path.read_bytes()).hexdigest() == before
    with pytest.raises(RuntimeError, match='disabled'):
        WorkspaceStore(settings)
    with pytest.raises(RuntimeError, match='disabled'):
        ReplaySession(settings, scenario_plan())


def test_local_developer_import_remains_available(settings, bundle, monkeypatch):
    monkeypatch.setenv('AEI_PUBLIC_DEMO', '0')
    assert import_csv_bundle(bundle, settings).rows_inserted > 0


def test_public_mode_application_starts_and_hides_disallowed_imports(ui_data, monkeypatch, tmp_path):
    from src.demo_assets import prepare_demo
    from src.demo_limits import prepare_controls
    assets, controls = tmp_path / 'snapshots', tmp_path / 'controls'
    prepare_demo(assets, laboratory_bundle=ui_data)
    prepare_controls(controls)
    monkeypatch.setenv('AEI_DEMO_ASSETS', str(assets))
    monkeypatch.setenv('AEI_DEMO_CONTROL', str(controls))
    monkeypatch.setenv('AEI_PUBLIC_DEMO', '1')
    app = AppTest.from_file(str(APP)).run(timeout=30)
    assert not app.error and not app.exception
    app.radio(key='_presentation_mode').set_value('Expert').run(timeout=30)
    app.sidebar.radio[0].set_value('Data & Settings').run(timeout=30)
    assert not app.error and not app.exception
    assert any('read-only' in item.value for item in app.info)
    assert not any('Import original' in button.label for button in app.button)
