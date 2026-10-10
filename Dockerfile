# Python base image from Amazon ECR Public (no Docker Hub rate limits on AWS)
FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
COPY data ./data
COPY web ./web
EXPOSE 8000
CMD ["sh", "-c", "[ -f data/wards.json ] || python scripts/prepare_data.py; uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
