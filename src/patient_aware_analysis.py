"""Selected exploratory patient analyses, separate from the original BY scan.

Presence means an eligible first isolate, regardless of susceptibility. Patient
IDs exist only inside the read snapshot and never enter any public result.
"""

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date

from scipy.stats import binomtest

from src.advanced_analysis import CohortFilters, JOINS, _conditions, _json
from src.analysis_service import DatasetChangedError
from src.anomalies import AnomalyEngine, AnomalyResult, ProportionInterval, _Population, comparison_windows
from src.config import get_settings
from src.cohort_queries import ranked_isolates_sql
from src.database import connect
from src.investigations import database_fingerprint
from src.resistance import ResistanceEngine, ResistanceFilters, ResistanceResult, _date
from src.workspaces import MODES

METHOD = "Exact McNemar (two-sided binomial on discordant pairs)"
EXPLORATORY = "Selected exploratory tests; no multiplicity adjustment; no global BY alert classification"
ASSUMPTIONS = (
    "The paired analysis concerns returning patients with usable S/R in both periods, not population-wide change.",
    "Pairs must be independent across patients; laboratory data cannot establish absence of other clustering or confounding.",
    "Sensitivity analysis excludes all patients observed in both periods, including those with missing S/R. Exclusion can change the population and introduce selection bias.",
    "Repeated filter exploration is not multiplicity-adjusted. Paired and sensitivity p-values must not be combined or promoted to original global BY alerts.",
    "Results do not establish an outbreak, treatment failure, clinical infection or causality.",
)


@dataclass(frozen=True)
class Membership:
    observed_baseline: int
    observed_current: int
    baseline_only: int
    current_only: int
    shared_observed: int
    shared_tested: int
    baseline_without_usable_sr: int
    current_without_usable_sr: int
    without_usable_sr_either_period: int


@dataclass(frozen=True)
class PatientCategory:
    category: str
    n_patients: int
    baseline: ResistanceResult
    current: ResistanceResult


@dataclass(frozen=True)
class PairedAnalysis:
    status: str
    n_complete_pairs: int | None
    s_to_s: int | None
    s_to_r: int | None
    r_to_s: int | None
    r_to_r: int | None
    discordant_pairs: int | None
    shared_without_complete_sr: int
    ambiguous_shared_patients: int
    p_value: float | None
    method: str
    explanation: str
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class SensitivityAnalysis:
    current: ResistanceResult
    baseline: ResistanceResult
    difference_pp: float | None
    status: str
    p_value: float | None
    test_method: str
    current_ci: ProportionInterval | None
    baseline_ci: ProportionInterval | None
    limitations: tuple[str, ...]
    explanation: str


@dataclass(frozen=True)
class PatientAwareReport:
    cohort: CohortFilters
    antibiotic_code: str
    source: str
    source_identity: str
    data_version: int
    analysis_date: date | None
    minimum_sample_size: int
    confidence_level: float
    membership: Membership
    categories: tuple[PatientCategory, ...]
    full_population: AnomalyResult
    paired: PairedAnalysis
    sensitivity: SensitivityAnalysis
    interpretation: str = EXPLORATORY
    assumptions: tuple[str, ...] = ASSUMPTIONS
    algorithm: str = "patient-aware-v1"

    def to_dict(self):
        return _json(asdict(self))


def exact_mcnemar(s_to_r, r_to_s):
    """Conventional p=1 for zero discordances; caller distinguishes no pairs."""
    if any(type(value) is not int or value < 0 for value in (s_to_r,r_to_s)):
        raise ValueError("Discordant counts must be nonnegative integers")
    n = s_to_r+r_to_s
    return float(binomtest(s_to_r,n,p=.5,alternative="two-sided").pvalue) if n else 1.0


def validate_patient_revision(report,settings):
    with connect(settings,read_only=True) as con:
        version = con.execute("SELECT version FROM data_version WHERE singleton_id=1").fetchone()[0]
    if report.source_identity != database_fingerprint(settings) or version != report.data_version:
        raise DatasetChangedError("Patient-aware source revision changed. Refresh before using or exporting these results.")


class PatientAwareEngine:
    def __init__(self,settings=None):
        self.settings = settings or get_settings()
        self.anomaly = AnomalyEngine(self.settings)

    def analyze(self,cohort=None,*,code,as_of_date=None,expected_version=None,source=MODES[0]):
        cohort = cohort or CohortFilters()
        filters = ResistanceFilters(code,cohort.ward_code,cohort.organism,cohort.specimen)
        if source not in MODES:
            raise ValueError("Invalid patient-aware data source")
        explicit = _date(as_of_date,"as_of_date") if as_of_date is not None else None
        with ResistanceEngine(self.settings)._snapshot() as (con,version,latest):
            if expected_version is not None and expected_version != version:
                raise DatasetChangedError("Patient-aware calculation changed revision. Refresh data.")
            end = explicit or latest
            if cohort.ward_code:
                ward = con.execute("SELECT ward_type,institution FROM wards WHERE ward_code=?",(cohort.ward_code,)).fetchone()
                if ward and ((cohort.ward_type and ward[0]!=cohort.ward_type) or (cohort.institution and ward[1]!=cohort.institution)
                             or (cohort.admission_timing and ward[0]!="in")):
                    raise ValueError("Selected ward is incompatible with the patient-aware cohort restrictions.")
            windows = comparison_windows(end) if end else None
            rows = []
            if windows:
                clauses,params = _conditions(cohort,"antibiotic",code)
                clauses.append("c.sample_date BETWEEN :start_date AND :end_date")
                params.update(start_date=windows[2].isoformat(),end_date=end.isoformat(),current_start=windows[0].isoformat(),antibiotic_code=code)
                ranked = ranked_isolates_sql(clauses,split_period=True,joins=JOINS)
                rows = con.execute(f"WITH ranked AS ({ranked}) SELECT r.patient_id,r.reporting_period,s.result FROM ranked r "
                    "LEFT JOIN susceptibility s ON s.isolate_id=r.isolate_id AND s.antibiotic_code=:antibiotic_code WHERE r.isolate_rank=1",params).fetchall()
            # Build transient patient membership after the original first-isolate selection.
            periods = {key:defaultdict(list) for key in ("baseline","current")}
            for row in rows:
                periods[row["reporting_period"]][row["patient_id"]].append(row["result"])
            base,current = periods["baseline"],periods["current"]
            baseline_ids,current_ids = set(base),set(current)
            shared = baseline_ids & current_ids
            only_baseline,only_current = baseline_ids-shared,current_ids-shared
            def tested(groups):
                return {patient for patient,values in groups.items() if any(value in ("S","R") for value in values)}
            base_tested,current_tested = tested(base),tested(current)
            membership = Membership(len(base),len(current),len(only_baseline),len(only_current),len(shared),
                len(base_tested & current_tested),len(baseline_ids-base_tested),len(current_ids-current_tested),
                len((baseline_ids|current_ids)-(base_tested|current_tested)))
            def population(allowed):
                def read(_,period):
                    pop,first = _Population(),0
                    for patient,values in periods[period].items():
                        if patient not in allowed:
                            continue
                        first += len(values)
                        for value in values:
                            if value in ("S","R"):
                                pop.patients.add(patient)
                                if value=="S":
                                    pop.susceptible+=1
                                else:
                                    pop.resistant+=1
                    return pop,first
                return read
            full = self.anomaly._assess(filters,end,windows,population(baseline_ids|current_ids),version)
            restricted = self.anomaly._assess(filters,end,windows,population(only_baseline|only_current),version)
            # Do not propagate the engine's unadjusted potential_* classification as an alert.
            sensitivity_status = ("analysis_limited" if restricted.limitations and restricted.p_value is None
                and restricted.current.status==restricted.baseline.status=="ok" else
                "no_data" if not restricted.current.n_tested or not restricted.baseline.n_tested else
                "insufficient_data" if restricted.current.status!="ok" or restricted.baseline.status!="ok" else "eligible_exploratory")
            sensitivity = SensitivityAnalysis(restricted.current,restricted.baseline,restricted.difference_pp,sensitivity_status,
                restricted.p_value,restricted.test_method,restricted.current_ci,restricted.baseline_ci,restricted.limitations,
                "Restricted-population sensitivity analysis: all patients observed in both periods are excluded from both samples. "
                "Selection bias is possible; this estimate does not replace the full-population comparison." +
                (" No patients were excluded; this restricted comparison coincides with the full selected comparison." if not shared else ""))
            categories = []
            for name,ids in (("Baseline only",only_baseline),("Current only",only_current),("Both periods",shared)):
                read = population(ids)
                bounds = {"current":windows[:2],"baseline":windows[2:]} if windows else {"current":(None,None),"baseline":(None,None)}
                results = {}
                for period in ("baseline","current"):
                    pop,first = read(filters,period)
                    results[period] = self.anomaly.resistance._result(filters,*bounds[period],first,pop.susceptible,pop.resistant,version)
                categories.append(PatientCategory(name,len(ids),results["baseline"],results["current"]))
            ambiguous = sum(len(base[patient])!=1 or len(current[patient])!=1 for patient in shared)
            incomplete = len(shared-(base_tested&current_tested))
            paired_limits = []
            if ambiguous or (shared and (cohort.organism is None or cohort.specimen is None)):
                paired = PairedAnalysis("analysis_limited",None,None,None,None,None,None,incomplete,ambiguous,None,METHOD,
                    "Paired table and test withheld: pairing requires an exact organism/specimen context and one first isolate per shared patient per period. "
                    "Select an exact organism/specimen or refine the cohort; no arbitrary patient result is chosen and different organisms/specimens are not paired.",
                    ("An exact organism/specimen and a single binary result per patient per period are required for pairing.",))
            else:
                transitions = {key:0 for key in ("SS","SR","RS","RR")}
                for patient in shared:
                    b,c = base[patient][0],current[patient][0]
                    if b in ("S","R") and c in ("S","R"):
                        transitions[b+c]+=1
                n = sum(transitions.values())
                discordant = transitions["SR"]+transitions["RS"]
                if 0 < n < self.anomaly.resistance.settings.minimum_sample_size:
                    paired_limits.append("Small paired sample: exact probabilities remain defined, but evidence can be discrete, unstable and low-powered. No paired resistance percentage is reported.")
                if incomplete:
                    paired_limits.append(f"{incomplete} shared patients lack usable S/R in at least one period and are excluded from the paired table; missing is not S.")
                status = "no_complete_pairs" if not n else "no_discordant_pairs" if not discordant else "eligible_exploratory"
                explanation = ("No complete S/R pairs; the paired test is unavailable." if not n else
                    "No discordant pairs. Conventional exact p=1 is uninformative about equivalence or population-wide change." if not discordant else
                    "Exact McNemar compares S-to-R with R-to-S counts among returning patients with complete S/R. It is not a population-wide resistance test.")
                paired = PairedAnalysis(status,n,transitions["SS"],transitions["SR"],transitions["RS"],transitions["RR"],discordant,
                    incomplete,0,exact_mcnemar(transitions["SR"],transitions["RS"]) if n else None,METHOD,explanation,tuple(paired_limits))
        report = PatientAwareReport(cohort,code,source,database_fingerprint(self.settings),version,end,
            self.anomaly.resistance.settings.minimum_sample_size,self.settings.anomaly_confidence_level,
            membership,tuple(categories),full,paired,sensitivity)
        validate_patient_revision(report,self.settings)
        return report
