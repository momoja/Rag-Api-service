FROM public.ecr.aws/lambda/python:3.12

COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /uvx /bin/

WORKDIR /var/task

ENV PYTHONPATH=/var/task/.venv/lib/python3.12/site-packages \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
COPY rag_agent ./rag_agent
RUN uv sync --frozen --no-dev --no-install-project


COPY lambda/ingest_document/ingest_handler.py ./ingest_handler.py

CMD ["ingest_handler.lambda_handler"]
