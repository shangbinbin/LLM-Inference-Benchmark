"""Safe YAML configuration I/O; JSON remains readable for existing site files."""
import json
from pathlib import Path

import yaml


class ConfigLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently losing a user's setting."""


def _mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ValueError('Configuration mapping keys must be strings')
        if key in result:
            raise ValueError(f'Duplicate configuration key: {key}')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


ConfigLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def read_data(path):
    path = Path(path)
    try:
        # Preserve JSON semantics (e.g. scientific notation) for legacy files/state.
        if path.suffix == '.json':
            return json.loads(path.read_text(encoding='utf-8'))
        return yaml.load(path.read_text(encoding='utf-8'), Loader=ConfigLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f'{path}: invalid YAML: {exc}') from exc


def read_object(path):
    value = read_data(path)
    if not isinstance(value, dict):
        raise ValueError(f'{path}: expected a YAML mapping')
    return value


def yaml_text(data):
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)


def write_yaml(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_text(data), encoding='utf-8')
