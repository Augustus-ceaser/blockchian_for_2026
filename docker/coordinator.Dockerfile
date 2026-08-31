ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

FROM --platform=linux/amd64 ${PYTHON_IMAGE} AS dependencies
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY backend/requirements/coordinator-runtime.lock /tmp/coordinator-runtime.lock
COPY wheelhouse/*.whl /tmp/wheelhouse/
RUN --network=none /opt/venv/bin/python -m pip install \
      --no-index \
      --find-links=/tmp/wheelhouse \
      --only-binary=:all: \
      --require-hashes \
      --no-cache-dir \
      -r /tmp/coordinator-runtime.lock \
    && /opt/venv/bin/python -m pip check

FROM --platform=linux/amd64 ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/workspace/backend \
    MEDTRUST_LOCAL_EXECUTOR_WORKSPACE=/var/lib/medtrust/workspaces
RUN groupadd --system --gid 10001 medtrust \
    && useradd --system --uid 10001 --gid medtrust --home-dir /nonexistent medtrust \
    && mkdir -p /workspace/backend /var/lib/medtrust/workspaces \
    && chown -R medtrust:medtrust /var/lib/medtrust
COPY --from=dependencies /opt/venv /opt/venv
COPY backend/app /workspace/backend/app
COPY registered_assets /workspace/registered_assets
COPY smoke_test_plans /workspace/smoke_test_plans
USER 10001:10001
WORKDIR /workspace/backend
HEALTHCHECK --interval=15s --timeout=10s --retries=4 \
  CMD ["python", "-c", "import numpy, psutil, torch; assert torch.version.cuda is None and not torch.cuda.is_available()"]
CMD ["python", "-m", "app.workers.execution_coordinator"]
