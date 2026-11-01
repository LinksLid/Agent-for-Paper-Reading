FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN python -m venv .venv && .venv/bin/pip install --no-cache-dir -r requirements.txt
COPY launch.py ./
COPY paperteam ./paperteam
COPY showcase ./showcase
COPY examples/public-demo.json ./examples/public-demo.json
COPY docs/maintainer-results-20261101.md docs/人工评测与课堂演示清单.md ./docs/
EXPOSE 8600
CMD ["python", "launch.py", "--host", "0.0.0.0", "--no-browser"]
