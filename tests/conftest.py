from __future__ import annotations

import os

# Explicitly set test environment variables for unit test suite execution
os.environ["ENV"] = "test"
os.environ["HMS_ALLOW_MOCK_DB"] = "true"

