"""Shared execution and navigation adapters over unchanged analytical engines."""

from dataclasses import dataclass
from datetime import date

from src.advanced_analysis import AdvancedAnalysisEngine
from src.analysis_service import DatasetChangedError
from src.anomalies import AnomalyEngine
from src.investigations import database_fingerprint
from src.smart_query import SmartQuery, validate_query


def source_identity(settings, catalog, source, as_of):
    return (database_fingerprint(settings), source, catalog.version, str(as_of))


@dataclass(frozen=True)
class SearchResult:
    query: SmartQuery
    identity: tuple
    report: object
    period: object | None
    findings: tuple = ()


def execute_query(query, settings, catalog, *, source='Laboratory Dataset', as_of=None):
    validate_query(query, catalog)
    end = date.fromisoformat(query.as_of) if query.as_of else (as_of or catalog.latest_sample)
    identity = source_identity(settings, catalog, source, as_of or catalog.latest_sample)
    if query.intent == 'anomaly_scan':
        # Filter only AFTER the full original BY family has been calculated.
        scan = AnomalyEngine(settings).scan(as_of_date=end)
        if scan.data_version != catalog.version:
            raise DatasetChangedError('The source revision changed. Submit the search again.')
        results = tuple(item for item in scan.results if item.difference_pp is not None and item.difference_pp > 0 and all(value is None or getattr(item.filters, key) == value for key, value in
            (('ward_code', query.ward), ('organism', query.organism), ('antibiotic_code', query.antibiotic), ('specimen', query.specimen))))
        return SearchResult(query, identity, scan, None, results)
    report = AdvancedAnalysisEngine(settings).analyze(query.cohort(), code=query.antibiotic, as_of_date=end,
        group_by=query.group_by or 'age', group_period=query.period, expected_version=catalog.version)
    return SearchResult(query, identity, report, getattr(report, query.period))


def transfer_to_explore(state, result, current_identity):
    if result.identity != current_identity:
        raise DatasetChangedError('The source, revision or date changed. Submit the search again before opening an investigation.')
    if result.query.intent == 'anomaly_scan':
        raise ValueError('Choose a concrete anomaly finding to investigate.')
    query = result.query
    # Explicitly clear unrelated retained restrictions before transferring the complete query.
    state.update(navigation='Explore', explore_view='Investigation', ward_code=query.ward,
        organism=query.organism, antibiotic_code=query.antibiotic, specimen=query.specimen,
        analysis_date=result.report.analysis_date, follow_latest_date=False, explorer_mode='antibiotic',
        explorer_origin=None, explorer_sex=query.sex, explorer_age_group='custom' if query.age_min is not None or query.age_max is not None else 'all',
        explorer_age_min=query.age_min if query.age_min is not None else 0,
        explorer_age_max=query.age_max if query.age_max is not None else 130,
        explorer_home_area=None, explorer_ward_type=None, explorer_institution=None, explorer_admission_timing=None,
        explorer_group_dimension=query.group_by or 'age', explorer_group_period=query.period,
        explorer_frequency='Monthly', explorer_section='overview')
    from src.ux_state import sync_aliases
    sync_aliases(state)
