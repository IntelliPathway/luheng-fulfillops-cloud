FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
ARG INSTALL_HARNESS=false
COPY requirements.txt requirements-harness.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_HARNESS" = "true" ]; then pip install --no-cache-dir -r requirements-harness.txt; fi
COPY app ./app
COPY harness ./harness
COPY migrations ./migrations

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
