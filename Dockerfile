# Debian/Ubuntu 기반. 한글 파일/폴더명 인식을 위해 UTF-8 locale 강제
FROM python:3.12-slim

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    locales \
    && locale-gen C.UTF-8 \
    && update-locale LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# data/pdf 포함 (COPY 시 .dockerignore에서 제외하지 말 것)
# 실행 시 ENV PYTHONPATH=/app
ENV PYTHONPATH=/app

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
