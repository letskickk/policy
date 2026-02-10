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
# 인덱스 캐시: /tmp는 재시작 시 휘발 → 영구 볼륨 마운트 시 이 경로 사용
ENV PYTHONPATH=/app
ENV INDEX_CACHE_DIR=/app/data/index_cache

EXPOSE 80
# workers=1: 멀티워커 시 인덱스 미공유 이슈 회피. RAG 검색 시 단일 워커 권장.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "80", "--workers", "1"]
