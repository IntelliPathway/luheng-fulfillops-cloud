FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
ARG INSTALL_HARNESS=false
ARG INSTALL_AWS=false
COPY requirements.txt requirements-harness.txt requirements-aws.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_HARNESS" = "true" ]; then pip install --no-cache-dir -r requirements-harness.txt; fi \
    && if [ "$INSTALL_AWS" = "true" ]; then pip install --no-cache-dir -r requirements-aws.txt; fi
COPY app ./app
COPY harness ./harness
COPY migrations ./migrations
COPY replay ./replay
RUN useradd --system --uid 10001 --create-home --home-dir /home/fulfillops fulfillops \
    && mkdir -p /var/lib/luheng/dsh \
    && chown -R fulfillops:fulfillops /app /var/lib/luheng /home/fulfillops

EXPOSE 8000
USER 10001:10001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
