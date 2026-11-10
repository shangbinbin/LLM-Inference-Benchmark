FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim-bookworm
RUN set -eux; \
    for source in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do \
        if [ -f "$source" ]; then \
            sed -i -E \
                -e 's|https?://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
                -e 's|https?://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
                -e 's|https?://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' \
                "$source"; \
        fi; \
    done; \
    apt-get update -o APT::Update::Error-Mode=any && \
    apt-get install -y --no-install-recommends logrotate && rm -rf /var/lib/apt/lists/*
COPY containers/toolbox/*.py /opt/benchmark/
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/opt/benchmark/toolbox.py"]
