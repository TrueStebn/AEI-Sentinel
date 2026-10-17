"""Untrusted interpretation contract and source-bound, allowlisted query validation."""

from dataclasses import asdict, dataclass
from datetime import date
import re

from src.advanced_analysis import CohortFilters

INTENTS = ('resistance_analysis', 'historical_comparison', 'patient_group_comparison',
           'resistance_trend', 'anomaly_scan')
PERIODS = ('current', 'annual', 'baseline')
GROUPS = ('sex', 'age', 'ward')
ISSUES = ('missing_information', 'ambiguous', 'contradictory', 'unsupported', 'injection')
MESSAGES = {
    'missing_information': 'Please specify an organism and antibiotic, or use Manual Search to choose them.',
    'ambiguous': 'Please clarify the intended filters or comparison. No analysis has been run.',
    'contradictory': 'The request contains conflicting conditions. Please correct them before searching.',
    'unsupported': 'This constraint or operation is unavailable. Supported windows are the current 28 days, rolling 365 days and historical baseline; group comparisons are descriptive.',
    'injection': 'Smart Search accepts surveillance questions only. Instructions to execute code, change rules, access secrets or recommend treatment are not supported.',
}


class QueryClarification(ValueError):
    def __init__(self, issue, message=None):
        self.issue = issue
        super().__init__(message or MESSAGES[issue])


@dataclass(frozen=True)
class SmartQuery:
    intent: str = 'resistance_analysis'
    ward: str | None = None
    organism: str | None = None
    antibiotic: str | None = None
    specimen: str | None = None
    sex: str | None = None
    age_min: int | None = None
    age_max: int | None = None
    period: str = 'current'
    group_by: str | None = None
    metric: str = 'percentage'
    as_of: str | None = None

    def to_dict(self):
        return asdict(self)

    def cohort(self):
        return CohortFilters(ward_code=self.ward, organism=self.organism, specimen=self.specimen,
                             sex=self.sex, age_min=self.age_min, age_max=self.age_max)


def response_schema():
    properties = {
        'status': {'type': 'string', 'enum': ['ready', 'clarification']},
        'issue': {'enum': [None, *ISSUES]},
        'intent': {'enum': list(INTENTS)},
        **{key: {'type': ['string', 'null']} for key in ('ward', 'organism', 'antibiotic', 'specimen', 'sex', 'as_of')},
        **{key: {'type': ['integer', 'null'], 'minimum': 0, 'maximum': 130} for key in ('age_min', 'age_max')},
        **{key: {'type': 'boolean'} for key in ('age_min_inclusive', 'age_max_inclusive')},
        'period': {'enum': [*PERIODS, 'last_calendar_month', 'custom']},
        'period_days': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 3650},
        'group_by': {'enum': [None, *GROUPS]},
        'metric': {'enum': ['percentage', 'resistant_count']},
    }
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def _terms(catalog, kind):
    values = getattr(catalog, kind)
    terms = {}
    for key in values:
        terms[key.casefold()] = key
        if isinstance(values, dict):
            terms[values[key].casefold()] = key
            terms[values[key].split(' (')[0].casefold()] = key
    synonyms = {
        'organisms': {'e. coli': 'Escherichia coli', 'e coli': 'Escherichia coli',
                      'k. pneumoniae': 'Klebsiella pneumoniae', 's. aureus': 'Staphylococcus aureus'},
        'antibiotics': {'cipro': 'CIP', 'trimethoprim': 'TMP', 'ampicillin': 'AMP', 'ciprofloxacin': 'CIP'},
        'wards': {'urology': 'URO', 'emergency': 'ED'},
        'specimens': {'urinary': 'urine'},
    }
    for term, key in synonyms[kind].items():
        if key in values:
            terms[term.casefold()] = key
    return terms


def resolve(value, catalog, kind):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 160:
        raise QueryClarification('unsupported')
    resolved = _terms(catalog, kind).get(value.strip().casefold())
    if resolved is None:
        raise QueryClarification('unsupported', f'The requested {kind[:-1]} is not available in this source. Please choose a value in Manual Search.')
    return resolved


def input_guard(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 1200:
        raise QueryClarification('ambiguous', 'Enter an English surveillance question of 1 to 1,200 characters.')
    if re.search(r'ignore\s+(?:all\s+|the\s+|previous\s+)*(?:instructions|rules|validation)|system\s*prompt|\b(?:execute|sql|drop\s+table|password|secret|token|prescribe|treatment|recommend|dosage)\b', text, re.I):
        raise QueryClarification('injection')
    if re.search(r'\b(?:except|excluding|exclude|without|neither|not|non[- ]?(?:male|female)|mortality|survival|causal|causation|forecast|predict|median|length of stay|significance between|inpatients?|outpatients?|home areas?|admission|hospital days?|diabetes|diabetic|pregnant|pregnancy|ESBL|MRSA|VRE|CPE)\b', text, re.I):
        raise QueryClarification('unsupported')
    if re.search(r'\bpatients\s+(?:(?:who|that)\s+are\s+)?both\s+(?:male and female|female and male)\b', text, re.I):
        raise QueryClarification('contradictory')
    _age_constraints(text)


def _age_constraints(text):
    """Audit explicit numeric boundaries; this is not a free-text fallback parser."""
    text = text.casefold()
    low, high = [], []
    for pattern, shift, target in (
        (r'(?:older than|over|above|age\s*>\s*)\s*(\d+)', 1, low),
        (r'(?:at least|age\s*>=\s*)\s*(\d+)', 0, low),
        (r'(\d+)\s*(?:\+|and older|or older|and above|or above)', 0, low),
        (r'(?:younger than|under|below|age\s*<\s*)\s*(\d+)', -1, high),
        (r'(?:at most|age\s*<=\s*)\s*(\d+)', 0, high),
        (r'(\d+)\s*(?:and younger|or younger|and under|or under)', 0, high),
    ):
        target.extend(int(n) + shift for n in re.findall(pattern, text))
    for lo, hi in re.findall(r'(?:aged?|ages|between)\s*(\d+)\s*(?:to|and|[-–])\s*(\d+)', text):
        low.append(int(lo)); high.append(int(hi))
    for age in re.findall(r'(?:exactly|aged?)\s*(\d+)(?=\s*(?:years?\s*old|[.,]|$))', text):
        low.append(int(age)); high.append(int(age))
    if low and high and max(low) > min(high):
        raise QueryClarification('contradictory')
    return max(low) if low else None, min(high) if high else None


def semantic_guard(text, query, catalog):
    """Reject common lost constraints instead of silently executing a wider cohort."""
    input_guard(text)
    if re.search(r'\b(?:elderly|recent|older patients|older people)\b', text, re.I):
        raise QueryClarification('ambiguous', 'Please specify an exact age boundary or reporting window.')
    low, high = _age_constraints(text)
    for expected, actual, endpoint in ((low, query.age_min, 0), (high, query.age_max, 130)):
        if (expected is not None and expected != (actual if actual is not None else endpoint)) or (expected is None and actual is not None):
            raise QueryClarification('ambiguous', 'The age boundary could not be preserved exactly. Please correct it in Manual Search.')
    for kind, field in (('wards', 'ward'), ('organisms', 'organism'), ('antibiotics', 'antibiotic'), ('specimens', 'specimen')):
        found = {key for term, key in _terms(catalog, kind).items()
                 if re.search(r'(?<![\w])' + re.escape(term) + r'(?![\w])', text, re.I)}
        if len(found) > 1 and not (kind == 'wards' and query.group_by == 'ward'):
            raise QueryClarification('ambiguous')
        if len(found) == 1 and getattr(query, field) not in found:
            raise QueryClarification('ambiguous', 'A named filter was not preserved. Please correct it in Manual Search.')
        if not found and getattr(query, field) is not None:
            if kind in ('organisms', 'antibiotics'):
                raise QueryClarification('missing_information')
            raise QueryClarification('ambiguous', 'A filter could not be traced to your question. Please choose it explicitly in Manual Search.')
    sexes = {code for word, code in (('male', 'M'), ('males', 'M'), ('men', 'M'), ('female', 'F'), ('females', 'F'), ('women', 'F'))
             if re.search(r'\b' + word + r'\b', text, re.I)}
    if sexes and query.group_by != 'sex' and (len(sexes) != 1 or query.sex not in sexes):
        raise QueryClarification('contradictory' if len(sexes) > 1 else 'ambiguous')
    if not sexes and query.sex is not None:
        raise QueryClarification('ambiguous')
    if re.search(r'\b(?:last|past|previous) year\b', text, re.I) and query.period != 'annual':
        raise QueryClarification('ambiguous')
    dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', text)
    if dates and (len(dates) != 1 or query.as_of != dates[0]):
        raise QueryClarification('unsupported', 'Only one explicit analysis cutoff is supported. Use YYYY-MM-DD or Manual Search.')
    if query.as_of and not dates:
        raise QueryClarification('ambiguous')
    if query.intent == 'anomaly_scan' and (re.search(r'\b(?:decrease|decreases|decline|fall|changes)\b', text, re.I) or not re.search(r'\b(?:increases?|rising|rise|higher)\b', text, re.I)):
        raise QueryClarification('unsupported', 'Smart Search currently supports unusual resistance increases. Use the existing full surveillance briefing for changes in both directions.')
    if query.intent == 'patient_group_comparison':
        evidence = {'sex': r'\b(?:sex|male|female|men|women)\b', 'age': r'\bage groups?\b', 'ward': r'\b(?:wards|departments|units)\b'}
        if not re.search(evidence[query.group_by], text, re.I) or not re.search(r'\b(?:compare|versus|vs|between|across)\b', text, re.I):
            raise QueryClarification('ambiguous')
    explicit_period = re.search(r'\b(?:days?|year|baseline|current|recent)\b', text, re.I)
    if query.intent == 'resistance_trend' and not explicit_period and query.period != 'annual':
        raise QueryClarification('ambiguous')
    if re.search(r'last (?:calendar )?month', text, re.I):
        raise QueryClarification('unsupported', 'Last calendar month is different from a rolling 28-day window and is unavailable in Smart Search. Please choose a supported period.')
    for days in re.findall(r'(?:last|past|previous)\s+(\d+)\s*days?', text, re.I):
        if int(days) != {'current': 28, 'annual': 365}.get(query.period):
            raise QueryClarification('unsupported')
    if re.search(r'\b(?:compare|versus|vs\.?|between)\b', text, re.I) and query.intent not in ('historical_comparison', 'patient_group_comparison'):
        raise QueryClarification('ambiguous')
    if re.search(r'\b(?:baseline|historical)\b', text, re.I) and query.intent != 'historical_comparison' and query.period != 'baseline':
        raise QueryClarification('ambiguous')
    if re.search(r'\b(?:monthly|weekly|trend)\b', text, re.I) and query.intent != 'resistance_trend':
        raise QueryClarification('ambiguous')
    if re.search(r'\bweekly\b', text, re.I):
        raise QueryClarification('unsupported', 'Smart Search currently opens monthly trends. Weekly trends remain available in Explore.')
    if re.search(r'\b(?:count|number|how many)\b', text, re.I) and query.metric != 'resistant_count':
        raise QueryClarification('ambiguous')


def validate_query(query, catalog):
    if not isinstance(query, SmartQuery) or query.intent not in INTENTS or query.period not in PERIODS or query.metric not in ('percentage', 'resistant_count'):
        raise QueryClarification('unsupported')
    for field, kind in (('ward', 'wards'), ('organism', 'organisms'), ('antibiotic', 'antibiotics'), ('specimen', 'specimens')):
        value = getattr(query, field)
        if value is not None and value not in getattr(catalog, kind):
            raise QueryClarification('unsupported')
    try:
        query.cohort()
    except ValueError as exc:
        raise QueryClarification('contradictory', str(exc)) from None
    if query.as_of is not None:
        try:
            end = date.fromisoformat(query.as_of)
            assert end.isoformat() == query.as_of
        except (TypeError, ValueError, AssertionError):
            raise QueryClarification('unsupported', 'Use a valid analysis date in YYYY-MM-DD format.') from None
        if catalog.latest_sample and end > catalog.latest_sample:
            raise QueryClarification('unsupported', 'The analysis date is later than the latest source sample.')
    if query.intent == 'anomaly_scan':
        if any(x is not None for x in (query.age_min, query.age_max, query.sex, query.group_by)) or query.period != 'current':
            raise QueryClarification('unsupported', 'The original anomaly scan supports ward, organism, antibiotic and specimen scope with its fixed windows; patient restrictions are not available.')
    else:
        if not query.organism or not query.antibiotic:
            raise QueryClarification('missing_information')
    if query.intent == 'patient_group_comparison':
        if query.group_by not in GROUPS or (query.group_by == 'sex' and query.sex is not None) or (query.group_by == 'age' and (query.age_min is not None or query.age_max is not None)) or (query.group_by == 'ward' and query.ward is not None):
            raise QueryClarification('contradictory', 'Choose a comparison dimension without restricting that same dimension. Other filters are retained.')
    elif query.group_by is not None:
        raise QueryClarification('unsupported')
    if query.intent == 'historical_comparison' and query.period != 'current':
        raise QueryClarification('unsupported', 'Historical comparisons use the current 28 days against the preceding non-overlapping 365 days.')
    return query


def interpret_output(payload, catalog, text):
    input_guard(text)
    if not isinstance(payload, dict) or set(payload) != set(response_schema()['required']):
        raise QueryClarification('ambiguous', 'The model returned an invalid structured response. Please retry or use Manual Search.')
    for key, value in payload.items():
        spec = response_schema()['properties'][key]
        if 'enum' in spec and value not in spec['enum']:
            raise QueryClarification('unsupported')
        if key.startswith('age_') and key.endswith('inclusive') and type(value) is not bool:
            raise QueryClarification('unsupported')
    if payload['status'] == 'clarification':
        raise QueryClarification(payload['issue'] or 'ambiguous')
    if payload['issue'] is not None:
        raise QueryClarification(payload['issue'])
    values = {key: payload[key] for key in SmartQuery.__dataclass_fields__}
    for field, kind in (('ward', 'wards'), ('organism', 'organisms'), ('antibiotic', 'antibiotics'), ('specimen', 'specimens')):
        values[field] = resolve(values[field], catalog, kind)
    values['sex'] = {'male': 'M', 'males': 'M', 'men': 'M', 'female': 'F', 'females': 'F', 'women': 'F'}.get(str(values['sex']).casefold(), values['sex'])
    for key, shift in (('age_min', 1), ('age_max', -1)):
        age = values[key]
        if age is not None:
            if type(age) is not int or not 0 <= age <= 130:
                raise QueryClarification('unsupported')
            values[key] += 0 if payload[key + '_inclusive'] else shift
    low, high = _age_constraints(text)
    # Only normalize an explicitly extracted numeric boundary that is already
    # the requested literal or its adjacent integer. Never fill a dropped bound.
    # Integer ages make >65 exactly >=66; the model's inclusive flag is not authority.
    for key, expected in (('age_min', low), ('age_max', high)):
        literal = payload[key]
        if expected is not None and type(literal) is int and abs(literal - expected) <= 1:
            values[key] = expected
    if low is None and values['age_min'] == 0:
        values['age_min'] = None
    if high is None and values['age_max'] == 130:
        values['age_max'] = None
    period_days = payload['period_days']
    if period_days is not None and (type(period_days) is not int or period_days != {'current': 28, 'annual': 365, 'baseline': 365}.get(values['period'])):
        raise QueryClarification('unsupported')
    query = validate_query(SmartQuery(**values), catalog)
    semantic_guard(text, query, catalog)
    return query
