FROM python:3.12-slim

WORKDIR /sandbox
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/sandbox

# Only packages baked from the platform allowlist at image build time.
# Rebuild this image when platform_python_packages changes.
COPY requirements-allowlist.txt /tmp/requirements-allowlist.txt
RUN pip install --no-cache-dir -r /tmp/requirements-allowlist.txt \
    && useradd --create-home --uid 10001 sandbox \
    && mkdir -p /sandbox/work \
    && chown -R sandbox:sandbox /sandbox

COPY atlas_sdk /sandbox/atlas_sdk
# Compatibility shim: `import requests` → HttpProxy IPC (no real PyPI requests).
COPY requests /sandbox/requests
# Minimal Agno / supertools import shims (not full frameworks).
COPY agno /sandbox/agno
COPY supertools /sandbox/supertools
COPY runner.py /sandbox/runner.py

USER sandbox
WORKDIR /sandbox/work
ENTRYPOINT ["python", "/sandbox/runner.py"]
