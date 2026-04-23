import pytest
import fakeredis
from unittest.mock import patch


@pytest.fixture(autouse=True)
def mock_redis():
    fake = fakeredis.FakeRedis(decode_responses=True)
    with patch("main.r", fake):
        yield fake
