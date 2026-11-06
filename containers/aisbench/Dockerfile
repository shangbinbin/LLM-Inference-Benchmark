FROM python:3.11-slim-bookworm
ARG AISBENCH_COMMIT=e330dc134c33f47671926144d9c143b554bc71cc
LABEL org.opencontainers.image.source="https://github.com/AISBench/benchmark"
LABEL org.opencontainers.image.revision=$AISBENCH_COMMIT
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HUB_OFFLINE=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libgomp1 git && rm -rf /var/lib/apt/lists/*
COPY source.tar.gz /tmp/aisbench.tar.gz
RUN mkdir /opt/aisbench && tar -xzf /tmp/aisbench.tar.gz -C /opt/aisbench --strip-components=1 && rm /tmp/aisbench.tar.gz
WORKDIR /opt/aisbench
RUN pip install 'pip<26' 'setuptools<=80.10.2' wheel && pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install 'transformers==5.22.2' 'tokenizers==0.23.3' . && pip check && pip freeze > /opt/aisbench/dependencies.txt
ENV MPLCONFIGDIR=/tmp/matplotlib HF_HOME=/tmp/huggingface
ENTRYPOINT ["ais_bench"]
