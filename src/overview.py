"""Source-consistent unit activity and the unchanged original surveillance scan."""

from dataclasses import dataclass
from datetime import timedelta,date
from collections import Counter

from src.anomalies import AnomalyEngine
from src.analysis_service import DatasetChangedError
from src.briefing import Finding,ordered_findings
from src.database import connect
from src.investigations import database_fingerprint
from src.signals import surveillance_signals
from src.ui_data import load_catalog
from src.workspaces import MODES


@dataclass(frozen=True)
class HealthcareUnit:
    code:str
    name:str
    ward_type:str
    institution:str
    cultures_28d:int
    latest_sample:date|None
    preliminary_count:int
    supported_count:int

    @property
    def type_label(self):
        return 'Hospital department' if self.institution=='hospital' else 'Primary care centre'


@dataclass(frozen=True)
class Overview:
    source:str
    source_identity:str
    data_version:int
    analysis_date:date|None
    latest_sample:date|None
    last_successful_import:str|None
    units:tuple
    scan:object
    preliminary:tuple
    findings:tuple


def build_overview(settings,*,catalog=None,as_of_date=None,expected_version=None,source='Laboratory Dataset',signal_threshold=None):
    if source not in MODES:
        raise ValueError('Invalid overview source')
    catalog=catalog or load_catalog(settings)
    expected=catalog.version if expected_version is None else expected_version
    end=date.fromisoformat(as_of_date) if isinstance(as_of_date,str) else as_of_date or catalog.latest_sample
    start=end-timedelta(days=27) if end else None
    with connect(settings,read_only=True) as con:
        con.execute('BEGIN')
        version=con.execute('SELECT version FROM data_version WHERE singleton_id=1').fetchone()[0]
        if version!=expected or catalog.version!=expected:
            raise DatasetChangedError('Overview metadata changed. Refresh data.')
        # All cultures, including negative/mixed cultures, once per culture ID; no AST join.
        rows=con.execute('''SELECT w.ward_code,w.ward_en,w.ward_type,w.institution,
            COUNT(DISTINCT CASE WHEN c.sample_date BETWEEN :start AND :end THEN c.culture_id END) AS recent,
            MAX(CASE WHEN c.sample_date<=:end THEN c.sample_date END) AS latest
            FROM wards w LEFT JOIN cultures c ON c.ward_code=w.ward_code
            GROUP BY w.ward_code,w.ward_en,w.ward_type,w.institution ORDER BY w.institution,w.ward_en,w.ward_code''',
            dict(start=start.isoformat() if start else None,end=end.isoformat() if end else None)).fetchall()
    scan=AnomalyEngine(settings).scan(as_of_date=end)
    if scan.data_version!=expected:
        raise DatasetChangedError('Overview and surveillance scan changed revision. Refresh data.')
    threshold=settings.signal_minimum_change_pp if signal_threshold is None else signal_threshold
    preliminary=tuple(item.comparison for item in surveillance_signals(scan,threshold))
    alerts=scan.alerts
    preliminary_counts=Counter(r.filters.ward_code for r in preliminary)
    supported_counts=Counter(r.filters.ward_code for r in alerts)
    units=tuple(HealthcareUnit(r[0],r[1],r[2],r[3],r[4],date.fromisoformat(r[5]) if r[5] else None,
        preliminary_counts[r[0]],supported_counts[r[0]]) for r in rows)
    def key(r):
        f=r.filters
        return f.ward_code,f.organism,f.antibiotic_code,f.specimen
    preliminary_keys={key(r) for r in preliminary}
    alert_keys={key(r) for r in alerts}
    findings=[]
    for r in scan.results:
        if key(r) in alert_keys or key(r) in preliminary_keys:
            findings.append(Finding(r,'BY-alert' if key(r) in alert_keys else 'preliminary',None))
    with connect(settings,read_only=True) as con:
        if con.execute('SELECT version FROM data_version WHERE singleton_id=1').fetchone()[0]!=expected:
            raise DatasetChangedError('Overview source changed while reading. Refresh data.')
    return Overview(source,database_fingerprint(settings),expected,end,catalog.latest_sample,catalog.last_successful_import,
        units,scan,preliminary,ordered_findings(findings))
