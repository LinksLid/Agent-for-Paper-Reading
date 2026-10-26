ARG PYTHON_VERSION=3.10
FROM python:${PYTHON_VERSION}-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git libffi-dev zlib1g-dev libncurses-dev \
    && rm -rf /var/lib/apt/lists/*
