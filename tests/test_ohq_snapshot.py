import hashlib
import json
import pytest
from scripts.build_ohq_initialization_probe import build


@pytest.mark.parametrize('changed',['original','snapshot'])
def test_failed_build_resume_rejects_changed_inputs_before_object_reuse(tmp_path,changed):
    repo=tmp_path/'repo';repo.mkdir()
    work=tmp_path/'build';(work/'source').mkdir(parents=True)
    expected=b'original header'
    (repo/'header.h').write_bytes(expected)
    (work/'source/header.h').write_bytes(expected)
    (work/'source_manifest.json').write_text(json.dumps({'source_sha256':{'header.h':hashlib.sha256(expected).hexdigest()}}))
    path=repo/'header.h' if changed=='original' else work/'source/header.h'
    path.write_bytes(b'changed header')
    with pytest.raises(ValueError,match='changed'):
        build(repo,work,'unused-qmake',resume=True)
