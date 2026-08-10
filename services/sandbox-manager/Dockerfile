FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# CLI only — socket is mounted from the host/daemon (see docker-compose).
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

RUN pip install --no-cache-dir fastapi==0.115.12 uvicorn==0.34.2 httpx==0.28.1 pydantic==2.13.4 redis==5.2.1

COPY app.py /app/app.py

EXPOSE 8090
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
