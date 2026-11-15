"""Restore synthetic historical stores without recreating their old admissions."""

import base64
import gzip
import json
from pathlib import Path
import shutil

from research_harness.storage import Store


def restore_stored_review(case, filename):
    fixture = json.loads(gzip.decompress((Path(__file__).with_name('fixtures') / filename).read_bytes()))
    case.assertEqual(fixture['source_release'], '0.88.1')
    shutil.rmtree(case.root)
    for name, encoded in fixture['files'].items():
        path = case.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(encoded))
    case.store = Store(case.root)
    case.artifacts = case.store.build_artifact_store()
    case.sequence = fixture['sequence']
    return fixture
