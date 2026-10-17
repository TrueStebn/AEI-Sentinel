"""Opt-in live benchmark; never downloads models or changes laboratory data.

python -m tools.benchmark_smart_search --model qwen3:8b --output <private-path>
"""

import argparse
from collections import Counter
import hashlib
import http.client
import json
from pathlib import Path
import statistics
import subprocess

from src.config import get_settings
from src.local_inference import MODELS, OllamaInterpreter, SYSTEM, examples
from src.smart_query import QueryClarification, interpret_output, response_schema
from src.ui_data import load_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=MODELS, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    fixture = Path(__file__).resolve().parents[1] / 'tests/fixtures/smart_search_queries.json'
    cases = json.loads(fixture.read_text())
    catalog = load_catalog(get_settings())
    client = OllamaInterpreter(model=args.model, timeout=90)
    rows = []
    _, warmup = client.generate('Show E. coli resistance to CIP in Urology.', catalog)
    gpu = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,memory.used,utilization.gpu', '--format=csv,noheader'], capture_output=True, text=True)
    connection = http.client.HTTPConnection(client.host, client.port, timeout=10)
    try:
        connection.request('GET', '/api/ps')
        resources = json.load(connection.getresponse())
    finally:
        connection.close()
    for case in cases:
        payload, timing = client.generate(case['text'], catalog)
        try:
            query = interpret_output(payload, catalog, case['text'])
            observed = dict(status='ready', query=query.to_dict())
        except QueryClarification as exc:
            observed = dict(status='clarification', issue=exc.issue)
        row = dict(id=case['id'], category=case['category'], text=case['text'], expected=case['expected'],
                   raw_model=payload, observed=observed, exact=observed == case['expected'], timing=timing)
        rows.append(row)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(dict(model=args.model, incomplete=True, rows=rows), indent=2) + '\n')
        print(f"{args.model} {len(rows)}/{len(cases)} exact={sum(r['exact'] for r in rows)} latency={timing['wall_seconds']:.2f}s", flush=True)
    latency = sorted(row['timing']['wall_seconds'] for row in rows)
    fields, correct = Counter(), Counter()
    critical = []
    for row in rows:
        if row['expected']['status'] == 'ready':
            for key, expected in row['expected']['query'].items():
                fields[key] += 1
                if row['observed'].get('query', {}).get(key, object()) == expected:
                    correct[key] += 1
            if row['observed']['status'] == 'ready' and row['observed']['query'] != row['expected']['query']:
                critical.append(row['id'])
        elif row['observed']['status'] == 'ready':
            critical.append(row['id'])
    uncertain = [row for row in rows if row['expected']['status'] == 'clarification']
    result = dict(model=args.model, incomplete=False, queries=len(rows), exact_correct=sum(row['exact'] for row in rows),
        exact_accuracy=sum(row['exact'] for row in rows) / len(rows),
        per_field_accuracy={key: correct[key]/total for key, total in fields.items()},
        critical_accepted_errors=critical, clarification_cases=len(uncertain),
        clarification_exact=sum(row['exact'] for row in uncertain),
        raw_model_clarification_exact=sum(row['raw_model'].get('status') == 'clarification' and row['raw_model'].get('issue') == row['expected']['issue'] for row in uncertain),
        median_seconds=statistics.median(latency), p95_seconds=latency[max(0, int(.95*len(latency)+.999)-1)],
        warmup=warmup, resources=resources, gpu_snapshot=gpu.stdout.strip(),
        fixture_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),
        prompt_schema_sha256=hashlib.sha256((SYSTEM+json.dumps(examples(), sort_keys=True)+json.dumps(response_schema(), sort_keys=True)).encode()).hexdigest(),
        options=dict(temperature=0, seed=14, num_ctx=4096, num_predict=500, think=False), rows=rows)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key not in ('rows', 'resources')}), flush=True)


if __name__ == '__main__':
    main()
