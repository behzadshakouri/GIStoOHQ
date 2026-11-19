import importlib.util
import os
import subprocess
import sys
import pytest


def test_standalone_doctor_initialization_does_not_abort_without_display(tmp_path):
    if not sys.platform.startswith('linux') or importlib.util.find_spec('qgis') is None:
        pytest.skip('Linux QGIS runtime not installed')
    env=dict(os.environ,XDG_CONFIG_HOME=str(tmp_path/'config'),QGIS_CUSTOM_CONFIG_PATH=str(tmp_path/'qgis'))
    for key in ['DISPLAY','WAYLAND_DISPLAY','QT_QPA_PLATFORM']:env.pop(key,None)
    code='from ohqbuilder.qgis_env import ensure_qgis_application; assert ensure_qgis_application(); print("HEADLESS_QGIS_OK")'
    result=subprocess.run([sys.executable,'-c',code],env=env,text=True,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr
    assert 'HEADLESS_QGIS_OK' in result.stdout
