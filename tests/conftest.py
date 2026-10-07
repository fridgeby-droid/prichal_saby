"""Isolate tests before any application module is imported."""
import os
import tempfile

_test_dir = tempfile.TemporaryDirectory(prefix='pickup-tests-')
os.environ['APP_MODE'] = 'demo'
os.environ['DB_PATH'] = _test_dir.name + '/app.db'
os.environ['PHOTO_DIR'] = _test_dir.name + '/photos'
os.environ.pop('DATABASE_URL', None)

# Legacy checkout scenarios isolate the new geographic/minimum rules.
os.environ['PICKUP_GEO_REQUIRED'] = 'false'
os.environ['PICKUP_MINIMUM_RUB'] = '0'
