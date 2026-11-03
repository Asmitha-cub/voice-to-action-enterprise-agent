import os
import tempfile

# Must run before any app import so Settings() picks these up.
_db_path = os.path.join(tempfile.gettempdir(), 'enterprise_agent_test.db')
if os.path.exists(_db_path):
    os.remove(_db_path)
os.environ['DATABASE_URL'] = f'sqlite:///{_db_path}'
os.environ['AUTH_ENABLED'] = 'false'
os.environ['EMAIL_ENABLED'] = 'false'   # deterministic: no real SMTP in tests
os.environ['SEED_ON_STARTUP'] = 'true'

import pytest  # noqa: E402

from app.db import init_db, seed_reference_data  # noqa: E402


@pytest.fixture(scope='session', autouse=True)
def _prepare_db():
    init_db()
    seed_reference_data()
    yield
