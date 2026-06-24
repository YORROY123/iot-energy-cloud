import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS

import redis as redis_lib

# ---------------------------------------------------------------------------
# PostgreSQL – SQLAlchemy
# ---------------------------------------------------------------------------

POSTGRES_URL: str = os.getenv(
    "POSTGRES_URL",
    "postgresql://iess:iess123@postgres/iess_db",
)

engine = create_engine(
    POSTGRES_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# InfluxDB 2
# ---------------------------------------------------------------------------

INFLUX_URL: str = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN: str = os.getenv("INFLUX_TOKEN", "my-super-secret-token")
INFLUX_ORG: str = os.getenv("INFLUX_ORG", "iess")
INFLUX_BUCKET: str = os.getenv("INFLUX_BUCKET", "realtime")

influx_client = InfluxDBClient(
    url=INFLUX_URL,
    token=INFLUX_TOKEN,
    org=INFLUX_ORG,
)

write_api = influx_client.write_api(write_options=SYNCHRONOUS)
query_api = influx_client.query_api()


def get_influx_query_api():
    """FastAPI dependency for InfluxDB query API."""
    return query_api


# ---------------------------------------------------------------------------
# Redis (synchronous)
# ---------------------------------------------------------------------------

REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379")

redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True)


def get_redis():
    """FastAPI dependency for Redis client."""
    return redis_client
