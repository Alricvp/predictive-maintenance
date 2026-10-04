# Render deploys this directly. (Koyeb removed its free tier for new accounts
# after joining Mistral AI in Feb 2026.) A Dockerfile is used in preference to
# buildpack autodetection so the Python version and the uvicorn invocation are
# pinned and reproducible.
FROM python:3.11-slim

# uvicorn[standard] pulls in websockets, which this app genuinely requires:
# the whole dashboard is driven by a WebSocket, not by polling.
RUN pip install --no-cache-dir \
    "fastapi==0.115.0" \
    "uvicorn[standard]==0.30.0" \
    "websockets==12.0" \
    "pydantic>=2.0,<3.0"

WORKDIR /app
# Copy the app/ CONTENTS into the working dir, not the directory itself:
# uvicorn imports `main`, and main.py does `import storage`. Nesting the files
# under /app/app/ leaves nothing importable at /app and the container would
# crash the moment it started - the bug this line used to have.
COPY app/ ./

# The free instance has 512 MB RAM on a shared CPU. One worker is correct
# here: more workers would thrash, and with more than one worker the in-memory
# WebSocket fan-out would break anyway (a broadcast only reaches the clients
# attached to THAT worker). See the note in README.
# Render injects PORT and its default is 10000; this fallback matches, so the
# container binds to whatever port the platform routes to either way.
ENV PORT=10000
EXPOSE 10000

CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]