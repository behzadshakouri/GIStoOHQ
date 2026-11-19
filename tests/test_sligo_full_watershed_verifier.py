"""Fail before GIS reads when preservation boundaries are violated."""
import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('sligo_reference_verifier', SCRIPTS / 'verify_sligo_full_watershed.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def test_existing_output_is_never_overwritten(tmp_path):
    output = tmp_path / 'existing'
    output.mkdir()
    marker = output / 'marker'
    marker.write_text('preserve')
    with pytest.raises(FileExistsError):
        verifier.verify(tmp_path / 'nonexistent_source', output)
    assert marker.read_text() == 'preserve'


@pytest.mark.parametrize('protected_side', ['source', 'output'])
def test_protected_folder_rejected_before_reads(tmp_path, protected_side):
    protected = Path('/home/arash/Dropbox/OHQ Watershed Models/Sligo Creek/gis')
    source, output = tmp_path / 'source', tmp_path / 'output'
    if protected_side == 'source':
        source = protected
    else:
        output = protected / 'never_write'
    with pytest.raises(ValueError, match='not the OHQ folder'):
        verifier.verify(source, output)
    assert not (tmp_path / 'output').exists()


def test_reference_symlink_cannot_read_protected_folder(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'mouth_snapped.txt').symlink_to('/home/arash/Dropbox/OHQ Watershed Models/nonexistent')
    with pytest.raises(ValueError, match='protected OHQ folder'):
        verifier.verify(source, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()
