# Koyeb deploys this directly. A Dockerfile is used in preference to buildpack
# autodetection so the Python version and the uvicorn invocation are pinned and
# reproducible.
FROM python:3.11-slim

# uvicorn[standard] pulls in websockets, which this app genuinely requires:
# the whole dashboard is driven by a WebSocket, not by polling.
RUN pip install --no-cache-dir \
    "fastapi==0.115.0" \
    "uvicorn[standard]==0.30.0" \
    "websockets==12.0" \
    "pydantic>=2.0,<3.0"

WORKDIR /app
COPY app/ ./app/

# The free Instance has 512 MB RAM and 0.1 vCPU. One worker is correct here:
# more workers on 0.1 vCPU would thrash, and with more than one worker the
# in-memory WebSocket fan-out would break anyway (a broadcast only reaches the
# clients attached to THAT worker). See the note in README.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]