# 模块四单独虚拟机镜像：只有标准库 + PyYAML，启动即跑 prepublish.check。
# 基础镜像用 slim 版，构建期只装 requirements.txt 里的两个依赖，不联网拉额外包。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CLAWHUNT_1364_RULES=/app/rules

WORKDIR /app

# 先只拷依赖清单，命中缓存时不必重装
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY core /app/core
COPY prepublish /app/prepublish
COPY rules /app/rules
COPY scripts /app/scripts

# 健康探针：直接跑一次自检，退出码 0 才算就绪（不额外装 curl）
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD ["python", "-m", "prepublish.check", "--config", "/etc/clawhunt/isolated.yaml"]

ENTRYPOINT ["python", "-m", "prepublish.check"]
CMD ["--config", "/etc/clawhunt/isolated.yaml"]