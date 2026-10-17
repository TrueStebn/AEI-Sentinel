"""Portable bundled-data discovery with explicit overrides and legacy fallback."""

from src import config


def test_bundled_dataset_is_preferred_over_legacy_sibling(tmp_path,monkeypatch):
    project=tmp_path/'project';bundled=project/'data/synthetic';bundled.mkdir(parents=True)
    (bundled/'patients.csv').touch()
    sibling=tmp_path/'data/raw/legacy';sibling.mkdir(parents=True);(sibling/'patients.csv').touch()
    monkeypatch.setattr(config,'PROJECT_ROOT',project)
    monkeypatch.delenv('AEI_DATASET_PATH',raising=False)
    assert config.get_settings().dataset_path==bundled.resolve()


def test_legacy_sibling_dataset_remains_supported(tmp_path,monkeypatch):
    project=tmp_path/'project';project.mkdir()
    sibling=tmp_path/'data/raw/legacy';sibling.mkdir(parents=True);(sibling/'patients.csv').touch()
    monkeypatch.setattr(config,'PROJECT_ROOT',project)
    monkeypatch.delenv('AEI_DATASET_PATH',raising=False)
    assert config.get_settings().dataset_path==sibling.resolve()


def test_explicit_dataset_override_wins_over_bundled_data(tmp_path,monkeypatch):
    project=tmp_path/'project';bundled=project/'data/synthetic';bundled.mkdir(parents=True)
    (bundled/'patients.csv').touch();override=tmp_path/'override'
    monkeypatch.setattr(config,'PROJECT_ROOT',project)
    monkeypatch.setenv('AEI_DATASET_PATH',str(override))
    assert config.get_settings().dataset_path==override.resolve()


def test_empty_checkout_uses_creatable_relative_runtime_location(tmp_path,monkeypatch):
    project=tmp_path/'project';project.mkdir()
    monkeypatch.setattr(config,'PROJECT_ROOT',project)
    monkeypatch.delenv('AEI_DATASET_PATH',raising=False)
    monkeypatch.delenv('AEI_DATABASE_PATH',raising=False)
    settings=config.get_settings()
    assert settings.database_path==project/'data/runtime/aei.db'
    assert not settings.database_path.exists()
