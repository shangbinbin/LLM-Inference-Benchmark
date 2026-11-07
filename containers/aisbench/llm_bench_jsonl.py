"""AISBench extension mounted for local JSONL cases."""
from itertools import islice
import json

from datasets import Dataset
from ais_bench.benchmark.datasets.base import BaseDataset
from ais_bench.benchmark.openicl.icl_prompt_template import PromptTemplate


class BusinessJsonlDataset(BaseDataset):
    @staticmethod
    def load(path, prompt_field, records, **kwargs):
        with open(path, encoding='utf-8-sig') as source:
            prompts = [json.loads(line)[prompt_field] for line in islice(source, records)]
        return Dataset.from_dict({'question': prompts})


class LiteralPrompt(PromptTemplate):
    def generate_item(self, entry, **kwargs):
        # Business text can itself contain template placeholders, including "{idx}".
        return entry['question']
