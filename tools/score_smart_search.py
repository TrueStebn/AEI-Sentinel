"""Score saved real predictions with one identical frozen validator for both models."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from src.config import get_settings
from src.smart_query import QueryClarification, interpret_output, resolve, SmartQuery
from src.ui_data import load_catalog


def score(path):
    data = json.loads(path.read_text())
    assert data['incomplete'] is False and len(data['rows']) == 64
    catalog = load_catalog(get_settings())
    counts, hits, raw_hits = Counter(), Counter(), Counter()
    critical, blocked_valid = [], []
    raw_errors = []
    raw_ready_exact = 0
    for row in data['rows']:
        try:
            observed = dict(status='ready', query=interpret_output(row['raw_model'], catalog, row['text']).to_dict())
        except QueryClarification as exc:
            observed = dict(status='clarification', issue=exc.issue)
        row.update(observed=observed, exact=observed == row['expected'])
        if row['expected']['status'] == 'ready':
            raw_fields = {key: row['raw_model'].get(key) for key in SmartQuery.__dataclass_fields__}
            for field, kind in (('ward', 'wards'), ('organism', 'organisms'), ('antibiotic', 'antibiotics'), ('specimen', 'specimens')):
                try:
                    raw_fields[field] = resolve(raw_fields[field], catalog, kind)
                except QueryClarification:
                    raw_fields[field] = '<invalid>'
            raw_fields['sex'] = {'male': 'M', 'men': 'M', 'female': 'F', 'women': 'F'}.get(str(raw_fields['sex']).casefold(), raw_fields['sex'])
            for field, shift, endpoint in (('age_min', 1, 0), ('age_max', -1, 130)):
                if type(raw_fields[field]) is int:
                    if row['raw_model'].get(field + '_inclusive') is False:
                        raw_fields[field] += shift
                    if raw_fields[field] == endpoint and row['expected']['query'][field] is None:
                        raw_fields[field] = None
            for key, expected in row['expected']['query'].items():
                counts[key] += 1
                if observed.get('query', {}).get(key, object()) == expected:
                    hits[key] += 1
                if raw_fields[key] == expected:
                    raw_hits[key] += 1
            wrong = [key for key, expected in row['expected']['query'].items() if raw_fields[key] != expected]
            if not wrong and row['raw_model'].get('status') == 'ready' and row['raw_model'].get('issue') is None:
                raw_ready_exact += 1
            if wrong:
                raw_errors.append(dict(id=row['id'], fields=wrong))
            if observed['status'] != 'ready':
                blocked_valid.append(row['id'])
        if observed['status'] == 'ready' and not row['exact']:
            critical.append(row['id'])
    uncertain = [row for row in data['rows'] if row['expected']['status'] == 'clarification']
    data.update(exact_correct=sum(row['exact'] for row in data['rows']),
        exact_accuracy=sum(row['exact'] for row in data['rows']) / 64,
        per_field_accuracy={key: hits[key]/total for key, total in counts.items()},
        raw_normalized_field_accuracy={key: raw_hits[key]/total for key, total in counts.items()},
        raw_extraction_errors=raw_errors,
        critical_accepted_errors=critical, valid_requests_held_for_clarification=blocked_valid,
        clarification_exact=sum(row['exact'] for row in uncertain),
        clarification_safely_held=sum(row['observed']['status'] == 'clarification' for row in uncertain),
        raw_model_exact_correct=raw_ready_exact + sum(row['raw_model'].get('status') == 'clarification' and row['raw_model'].get('issue') == row['expected']['issue'] for row in uncertain),
        validator_sha256=hashlib.sha256((Path(__file__).resolve().parents[1] / 'src/smart_query.py').read_bytes()).hexdigest(),
        scoring_note='Original real model predictions and timings retained; both candidates scored with the same validator. This is a development benchmark, not a held-out clinical validation.')
    target = path.with_name(path.stem + '-scored.json')
    target.write_text(json.dumps(data, indent=2) + '\n')
    print(json.dumps({key: value for key, value in data.items() if key not in ('rows', 'resources')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('predictions', type=Path)
    score(parser.parse_args().predictions)
