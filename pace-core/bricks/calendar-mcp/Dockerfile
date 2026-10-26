FROM python:3.13-slim

RUN pip install --no-cache-dir "mcp>=1.10,<2"

WORKDIR /app
COPY server.py .

EXPOSE 8080

CMD ["python", "server.py"]
