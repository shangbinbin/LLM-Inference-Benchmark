"""Resolve inference credentials only when constructing the API client.

Keep token values out of AISBench/MMEngine serialized model configurations.
"""
import os

from ais_bench.benchmark.models import VLLMCustomAPI, VLLMCustomAPIChat


class BearerTokenMixin:
    def __init__(self, api_key_env, **kwargs):
        token = os.environ.get(api_key_env, '')
        if not token.strip() or token.lower().startswith('bearer ') or any(c.isspace() for c in token):
            raise ValueError('Missing or invalid inference token environment variable: ' + api_key_env)
        kwargs['api_key'] = token
        super().__init__(**kwargs)


class BearerVLLMAPI(BearerTokenMixin, VLLMCustomAPI):
    pass


class BearerVLLMAPIChat(BearerTokenMixin, VLLMCustomAPIChat):
    pass
