import os, sys
from pathlib import Path
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/municipalguard_v021_api_tests.db")
Path("/tmp/municipalguard_v021_api_tests.db").unlink(missing_ok=True)
os.environ.setdefault("MGAI_AUTO_CREATE_SCHEMA", "true")
os.environ.setdefault("MGAI_API_KEYS_JSON", '{"tenant-a-test-key-000000000000":"tenant-a","tenant-b-test-key-000000000000":"tenant-b"}')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
