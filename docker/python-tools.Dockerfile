ARG PYTHON_VERSION=3.10
FROM python:${PYTHON_VERSION}-slim
RUN find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -exec sed -i 's|http://deb.debian.org|https://deb.debian.org|g' {} + \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends build-essential git libffi-dev zlib1g-dev libncurses-dev \
    && rm -rf /var/lib/apt/lists/*
