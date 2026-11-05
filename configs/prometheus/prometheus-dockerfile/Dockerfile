# syntax=docker/dockerfile:1

FROM m.daocloud.io/docker.io/prom/prometheus:v3-busybox AS upstream

FROM m.daocloud.io/docker.io/alpine:3.22

RUN apk add --no-cache bash ca-certificates tzdata \
    && mkdir -p /prometheus

COPY --from=upstream /bin/prometheus /bin/prometheus
COPY --from=upstream /bin/promtool /bin/promtool
COPY --from=upstream /etc/prometheus/ /etc/prometheus/
COPY --from=upstream /LICENSE /LICENSE
COPY --from=upstream /NOTICE /NOTICE

# Alpine 已内置 root，显式设为默认用户
USER root

WORKDIR /prometheus

EXPOSE 9090
VOLUME ["/prometheus"]

ENTRYPOINT ["/bin/bash"]
