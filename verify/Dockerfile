FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /verify
COPY verify/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# API 纯逻辑单元测试随验收一起运行（不依赖第三方库）
COPY api/app ./api/app
COPY api/tests ./api/tests
COPY verify/tests ./tests

ENV PYTHONPATH=/verify/api \
    WEB_BASE_URL=http://web \
    API_BASE_URL=http://api:8000

CMD ["pytest", "api/tests", "tests", "-v", "--tb=short"]
