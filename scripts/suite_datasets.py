"""Resolve and validate local AISBench inputs before a suite starts."""
import csv
from dataclasses import dataclass
from io import StringIO
import json
from pathlib import Path


@dataclass(frozen=True)
class LocalDataset:
    type: str
    path: str
    records: int
    requests: int
    prompt_field: str = ''

    @property
    def container_path(self):
        return '/datasets/input.jsonl' if self.type == 'jsonl' else '/datasets/input.json'

    def mount(self):
        # Docker parses --mount as CSV, including when an absolute path contains a comma.
        value = StringIO()
        csv.writer(value).writerow(['type=bind', 'src=' + self.path, 'dst=' + self.container_path, 'readonly'])
        return value.getvalue().rstrip('\r\n')


def requests_per_record(kind, path, field):
    with path.open(encoding='utf-8' if kind == 'sharegpt' else 'utf-8-sig') as source:
        if kind == 'jsonl':
            for number, line in enumerate(source, 1):
                try:
                    row = json.loads(line)
                except ValueError as exc:
                    raise ValueError(f'{path}:{number}: invalid JSONL: {exc}') from exc
                if not isinstance(row, dict) or not isinstance(row.get(field), str) or not row[field].strip():
                    raise ValueError(f'{path}:{number}: expected nonempty string field {field!r}')
                yield 1
            return
        records = json.load(source)
        if not isinstance(records, list):
            raise ValueError(f'{path}: ShareGPT requires a JSON array')
        for number, row in enumerate(records, 1):
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'].strip():
                raise ValueError(f'{path}: record {number} requires a nonempty string id')
            turns = row.get('conversations')
            if not isinstance(turns, list) or not turns or len(turns) % 2:
                raise ValueError(f'{path}: record {number} requires complete human/gpt pairs')
            for index, turn in enumerate(turns):
                role = 'human' if index % 2 == 0 else 'gpt'
                if (not isinstance(turn, dict) or turn.get('from') != role or
                        not isinstance(turn.get('value'), str) or not turn['value'].strip()):
                    raise ValueError(f'{path}: record {number}, turn {index + 1}: expected {role} with nonempty value')
            yield len(turns) // 2


def resolve_dataset(spec, directory, overrides):
    if not isinstance(spec, dict):
        raise ValueError('dataset must be an object')
    kind = spec.get('type')
    if kind == 'synthetic' and set(spec) == {'type'}:
        return None
    if kind not in ('jsonl', 'sharegpt'):
        raise ValueError('dataset.type must be jsonl, sharegpt or synthetic (synthetic takes no path/options)')
    allowed = {'type', 'path', 'prompt_field'} if kind == 'jsonl' else {'type', 'path'}
    if set(spec) - allowed:
        raise ValueError(f'unsupported {kind} dataset fields: {sorted(set(spec) - allowed)}')
    conflicting = {'dataset_type', 'input_length', 'prefix_length'} & overrides.keys()
    if conflicting:
        raise ValueError(f'{sorted(conflicting)} apply only to synthetic inputs')
    if not isinstance(spec.get('path'), str) or not spec['path'].strip():
        raise ValueError('dataset.path requires a local file')
    path = (directory / Path(spec['path']).expanduser()).resolve()
    if not path.is_file():
        raise ValueError(f'dataset file does not exist: {path}')
    field = spec.get('prompt_field', 'prompt') if kind == 'jsonl' else ''
    if kind == 'jsonl' and (not isinstance(field, str) or not field.strip()):
        raise ValueError('prompt_field must name a top-level text column')

    target = overrides.get('request_count')
    if 'request_count' in overrides and (type(target) is not int or target < 1):
        raise ValueError('request_count must be a positive integer')
    requests = records = 0
    for count in requests_per_record(kind, path, field):
        if target is None or requests < target:
            records += 1
            requests += count
            if target is not None and requests > target:
                raise ValueError(f'request_count={target} splits a ShareGPT conversation; next boundary is {requests}')
    if not records:
        raise ValueError(f'empty dataset: {path}')
    if target is not None and requests < target:
        raise ValueError(f'request_count={target} exceeds {requests} available requests in {path}')
    return LocalDataset(kind, str(path), records, requests, field)
