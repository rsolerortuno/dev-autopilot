# Supply a digest from the trusted registry when building:
# docker build --build-arg PYTHON_IMAGE_DIGEST=sha256:<digest> .
ARG PYTHON_IMAGE=python:3.12-slim
ARG PYTHON_IMAGE_DIGEST

FROM ${PYTHON_IMAGE}@${PYTHON_IMAGE_DIGEST} AS builder
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir --upgrade pip build \
    && python -m build --wheel --outdir /dist

FROM ${PYTHON_IMAGE}@${PYTHON_IMAGE_DIGEST}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder /dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir /tmp/*.whl \
    && rm -f /tmp/*.whl \
    && useradd --create-home --uid 10001 autopilot \
    && mkdir -p /var/lib/dev-autopilot \
    && chown -R autopilot:autopilot /app /var/lib/dev-autopilot
VOLUME ["/var/lib/dev-autopilot"]
USER autopilot
ENTRYPOINT ["dev-autopilot"]
