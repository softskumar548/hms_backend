FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /code

RUN apt-get update && apt-get install -y postgresql-client openssl && rm -rf /var/lib/apt/lists/*

# Shared libs are mounted/copied onto PYTHONPATH so services import hms_* directly.
ENV PYTHONPATH=/code:/libs

COPY services/plt/pyproject.toml /code/pyproject.toml
RUN pip install --no-cache-dir \
    fastapi "uvicorn[standard]" "sqlalchemy[asyncio]" asyncpg pydantic alembic \
    fhir.resources PyJWT cryptography pytest pytest-asyncio httpx

COPY libs /libs
COPY services/plt/app /code/app
COPY services/plt/alembic.ini /code/alembic.ini
COPY services/plt/alembic /code/alembic
COPY tests /code/tests

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
