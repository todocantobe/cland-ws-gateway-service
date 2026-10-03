# ------------------------------------------------------------------
# 运行镜像（cland-ws-gateway-service，Go）
# 二进制由 Jenkins 流水线的前一阶段构建（build/app），此处只打包，
# 不在镜像内重新拉码/编译。基础镜像走 5001 缓存仓，避免直连 Docker Hub。
# ------------------------------------------------------------------
FROM 192.168.1.7:5001/library/debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

# 非 root 运行
RUN useradd -m -u 1001 appuser
WORKDIR /app
RUN mkdir -p /app/logs /app/data && chown -R appuser:appuser /app

COPY --chown=appuser:appuser build/app /app/app
COPY --chown=appuser:appuser conf /app/conf

ENV CLAND_SERVER_PORT=8080
# 8080 HTTP(/api/health) + 8081 WS 帧网关(/ws)
EXPOSE 8080 8081

USER appuser
ENTRYPOINT ["/app/app"]
