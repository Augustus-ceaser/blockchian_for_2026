FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/connector
COPY hospital-connector/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hospital-connector/app ./app
RUN useradd --system --uid 10001 connector \
    && mkdir -p /var/lib/medtrust-connector /var/log/medtrust-connector \
    && chown -R connector:connector /var/lib/medtrust-connector /var/log/medtrust-connector /opt/connector
USER connector
EXPOSE 18600
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18600", "--no-access-log"]
