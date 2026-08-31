FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README_backend.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

RUN pip install --no-cache-dir .

RUN groupadd --gid 10001 medtrust \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin medtrust \
    && mkdir -p /var/lib/medtrust/data /var/lib/medtrust/policy-signing /var/cache/medtrust \
    && chown -R medtrust:medtrust /app /var/lib/medtrust /var/cache/medtrust

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
