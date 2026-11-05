# syntax=docker/dockerfile:1

FROM m.daocloud.io/docker.io/library/debian:bullseye-slim AS tools

RUN rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       bash-static \
       busybox-static \
    && apt-get clean \
    && rm -rf \
       /var/lib/apt/lists/* \
       /var/cache/apt/archives/*

# 在 Debian 阶段准备最终的 /bin 内容
RUN mkdir -p /out/bin \
    && cp /bin/bash-static /out/bin/bash \
    && cp /bin/busybox /out/bin/busybox \
    && for command in $(/bin/busybox --list); do \
         ln -s busybox "/out/bin/${command}"; \
       done

FROM m.daocloud.io/docker.io/grafana/loki:3.7-amd64

# 软链接由 Docker COPY 写入，不再在 distroless 中创建
COPY --from=tools /out/bin/ /bin/

USER root
