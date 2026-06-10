# ═══════════════════════════════════════════════════════════════════════════════
# PR Review Agent — Lambda Container Image
# ═══════════════════════════════════════════════════════════════════════════════
# Build:  docker build -t pr-review-agent .
# Test:   docker run -p 9000:8080 pr-review-agent
#         curl -X POST "http://localhost:9000/2015-03-31/functions/function/invocations" -d '{}'
# ═══════════════════════════════════════════════════════════════════════════════

FROM public.ecr.aws/lambda/python:3.12

# ── Install dependencies ─────────────────────────────────────────────────────
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# ── Copy application source ─────────────────────────────────────────────────
COPY main.py ${LAMBDA_TASK_ROOT}/
COPY app/ ${LAMBDA_TASK_ROOT}/app/

# ── Lambda entry-point ───────────────────────────────────────────────────────
# Points to the Mangum handler defined in main.py
CMD ["main.handler"]
