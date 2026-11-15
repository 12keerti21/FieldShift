# FieldShift — container image.
#
# The six precomputed bundles are committed, so the image needs no build step
# and no credentials: it serves a working tool the moment it starts.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY web/ ./web/
COPY data/ ./data/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
EXPOSE 8080

CMD exec uvicorn fieldshift.api.main:app --host 0.0.0.0 --port ${PORT}
