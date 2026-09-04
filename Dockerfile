# Toolbox image: experiment runner, agent runtime, bootstrap and analysis.
#
# Python 3.12 is frozen (testbed-architecture.md §4). It is supplied by this
# image so the host needs only Docker, not a matching Python installation.

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

# Source is bind-mounted at run time so edits do not require a rebuild.
# Nothing Synapse-owned is copied or mounted into this image: the runner and
# agent must hold no server configuration, database credentials or signing
# keys (testbed-architecture.md §2.3, §15; C2 evidence).

CMD ["python", "-c", "print('fam toolbox')"]
