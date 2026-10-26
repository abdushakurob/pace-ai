FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY pace-core ./pace-core

ENV PACE_HOST=0.0.0.0
ENV PACE_PORT=10000
ENV PACE_DB=/app/data/pace.db
EXPOSE 10000

CMD ["python", "pace-core/python/hub.py"]
