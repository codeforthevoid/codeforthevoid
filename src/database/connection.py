from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.engine import Engine, Connection
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.pool import QueuePool
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager
from typing import Generator, Optional
import logging
from datetime import datetime
import time

from ..config.settings import (
    DATABASE_URL,
    MAX_OVERFLOW,
    POOL_SIZE,
    POOL_TIMEOUT,
    POOL_RECYCLE,
    POOL_PRE_PING,
    CONNECT_TIMEOUT,
    COMMAND_TIMEOUT
)

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    pass


class ConnectionPool:
    _instance: Optional['ConnectionPool'] = None

    def __new__(cls) -> 'ConnectionPool':
        if cls._instance is None:
            cls._instance = super(ConnectionPool, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.engine = create_async_engine(
            DATABASE_URL,
            pool_size=POOL_SIZE,
            max_overflow=MAX_OVERFLOW,
            pool_timeout=POOL_TIMEOUT,
            pool_recycle=POOL_RECYCLE,
            pool_pre_ping=POOL_PRE_PING
        )

        self.SessionLocal = sessionmaker(
            class_=AsyncSession,
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )

        self._setup_engine_events()
        self._initialized = True

    def _setup_engine_events(self):
        @event.listens_for(self.engine, 'before_cursor_execute')
        def before_cursor_execute(
                conn: Connection,
                cursor,
                statement: str,
                parameters: tuple,
                context,
                executemany: bool
        ):
            conn.info.setdefault('query_start_time', []).append(time.time())
            logger.debug("Execute query: %s", statement)

        @event.listens_for(self.engine, 'after_cursor_execute')
        def after_cursor_execute(
                conn: Connection,
                cursor,
                statement: str,
                parameters: tuple,
                context,
                executemany: bool
        ):
            total = time.time() - conn.info['query_start_time'].pop(-1)
            logger.debug("Query completed in %.3f seconds", total)

        @event.listens_for(Engine, 'connect')
        def connect(dbapi_connection, connection_record):
            logger.info("New database connection established")

        @event.listens_for(Engine, 'checkout')
        def checkout(dbapi_connection, connection_record, connection_proxy):
            logger.debug("Database connection checked out from pool")

        @event.listens_for(Engine, 'checkin')
        def checkin(dbapi_connection, connection_record):
            logger.debug("Database connection returned to pool")


class DatabaseSession:
    def __init__(self):
        self.pool = ConnectionPool()

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """
        Session context manager with automatic error handling and cleanup.

        Yields:
            Session: Database session

        Raises:
            DatabaseError: When database operations fail
        """
        session: Session = self.pool.SessionLocal()
        session_id = id(session)
        start_time = time.time()

        try:
            logger.debug("Starting database session %d", session_id)
            yield session
            session.commit()
            logger.debug(
                "Database session %d committed successfully (%.3f seconds)",
                session_id,
                time.time() - start_time
            )

        except DBAPIError as e:
            logger.error(
                "Database API error in session %d: %s",
                session_id,
                str(e),
                exc_info=True
            )
            session.rollback()
            raise DatabaseError(f"Database operation failed: {str(e)}")

        except SQLAlchemyError as e:
            logger.error(
                "SQLAlchemy error in session %d: %s",
                session_id,
                str(e),
                exc_info=True
            )
            session.rollback()
            raise DatabaseError(f"Database operation failed: {str(e)}")

        except Exception as e:
            logger.error(
                "Unexpected error in session %d: %s",
                session_id,
                str(e),
                exc_info=True
            )
            session.rollback()
            raise

        finally:
            session.close()
            logger.debug(
                "Database session %d closed (%.3f seconds)",
                session_id,
                time.time() - start_time
            )


async def get_db() -> Generator[Session, None, None]:
    """
    Dependency for FastAPI routes that need database access.

    Yields:
        Session: Database session
    """
    db = DatabaseSession()
    with db.get_session() as session:
        yield session


def init_database() -> None:
    """
    Initialize database connection pool and verify connectivity.
    """
    try:
        pool = ConnectionPool()
        with pool.engine.connect() as connection:
            logger.info("Database connection successful")

    except Exception as e:
        logger.error("Database initialization failed: %s", str(e))
        raise DatabaseError("Failed to initialize database connection") from e


def close_database() -> None:
    """
    Clean up database connections and resources.
    """
    try:
        pool = ConnectionPool()
        pool.engine.dispose()
        logger.info("Database connections closed successfully")

    except Exception as e:
        logger.error("Error closing database connections: %s", str(e))
        raise DatabaseError("Failed to close database connections") from e