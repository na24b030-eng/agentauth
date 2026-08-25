FROM python:3.12-slim AS builder
WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml ./
COPY backend ./backend
RUN pip wheel --wheel-dir /wheels .

FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 10001 trustcart
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY alembic.ini ./
COPY alembic ./alembic
USER trustcart
ENV PYTHONUNBUFFERED=1 PORT=8000 TRUSTCART_SERVICE_ROLE=merchant-api
EXPOSE 8000
CMD ["python", "-m", "trustcart.entrypoint"]
