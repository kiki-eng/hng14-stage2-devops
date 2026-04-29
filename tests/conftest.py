import sys
import os
import pytest
from unittest.mock import patch
import fakeredis

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["GITHUB_CLIENT_ID"] = "test-client-id"
os.environ["GITHUB_CLIENT_SECRET"] = "test-client-secret"
os.environ["REDIS_HOST"] = "localhost"

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

from database import Base, get_db
from models import User, Profile
from auth import create_access_token, create_refresh_token


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def app(db):
    import database
    original_engine = database.engine
    database.engine = test_engine

    from main import app as fastapi_app

    def override_get_db():
        try:
            yield db
        finally:
            pass

    fastapi_app.dependency_overrides[get_db] = override_get_db
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()
    database.engine = original_engine


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin_user(db):
    user = User(
        github_id=1001,
        username="admin_user",
        email="admin@test.com",
        role="admin",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def analyst_user(db):
    user = User(
        github_id=1002,
        username="analyst_user",
        email="analyst@test.com",
        role="analyst",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def admin_token(admin_user):
    return create_access_token(admin_user)


@pytest.fixture
def analyst_token(analyst_user):
    return create_access_token(analyst_user)


@pytest.fixture
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def analyst_headers(analyst_token):
    return {"Authorization": f"Bearer {analyst_token}"}


@pytest.fixture
def sample_profiles(db, admin_user):
    profiles = [
        Profile(
            full_name="John Doe",
            email="john@test.com",
            location="Lagos, Nigeria",
            skills=["Python", "FastAPI"],
            company="TechCo",
            role_title="Backend Engineer",
            years_of_experience=5,
            created_by_id=admin_user.id,
        ),
        Profile(
            full_name="Jane Smith",
            email="jane@test.com",
            location="Nairobi, Kenya",
            skills=["React", "TypeScript"],
            company="WebDev Inc",
            role_title="Frontend Developer",
            years_of_experience=3,
            created_by_id=admin_user.id,
        ),
        Profile(
            full_name="Emeka Obi",
            email="emeka@test.com",
            location="Lagos, Nigeria",
            skills=["Python", "Django", "PostgreSQL"],
            company="DataCorp",
            role_title="Senior Developer",
            years_of_experience=8,
            created_by_id=admin_user.id,
        ),
    ]
    for p in profiles:
        db.add(p)
    db.commit()
    for p in profiles:
        db.refresh(p)
    return profiles


@pytest.fixture(autouse=True)
def mock_redis():
    import main
    main.r = fakeredis.FakeRedis(decode_responses=True)
    yield main.r
