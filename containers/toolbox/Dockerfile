FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends logrotate && rm -rf /var/lib/apt/lists/*
COPY containers/toolbox/*.py /opt/benchmark/
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/opt/benchmark/toolbox.py"]
