#!/usr/bin/env python3
"""Load/show/unload the repo plugin in offscreen QGIS/Qt without installation.

A minimal interface adapter hosts real Qt widgets. No maps, source downloads,
user profiles, production workflows or desktop plugin installs are changed.
"""
import argparse
import json
import os
from pathlib import Path
import sys


def run(output):
    output=Path(output).resolve()
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True)
    os.environ['QT_QPA_PLATFORM']='offscreen'
    os.environ['XDG_CONFIG_HOME']=str(output/'profile')
    os.environ['QGIS_CUSTOM_CONFIG_PATH']=str(output/'profile')
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from qgis.core import QgsApplication,Qgis
    from qgis.PyQt.QtWidgets import QMainWindow,QDockWidget
    from qgis.PyQt.QtCore import QEvent
    from qgis_plugin.gistoohq_dem_workflow import classFactory
    app=QgsApplication([],False,str(output/'profile'))
    app.initQgis()
    class Interface:
        def __init__(self):
            self.window=QMainWindow();self.window.resize(1050,850)
            self.menu=self.window.menuBar().addMenu('GIStoOHQ')
            self.toolbar=self.window.addToolBar('GIStoOHQ')
        def mainWindow(self):return self.window
        def addPluginToMenu(self,name,action):self.menu.addAction(action)
        def addToolBarIcon(self,action):self.toolbar.addAction(action)
        def addDockWidget(self,area,dock):
            if not isinstance(dock,QDockWidget):raise TypeError('Expected actual QDockWidget')
            self.window.addDockWidget(area,dock)
        def removePluginMenu(self,name,action):self.menu.removeAction(action)
        def removeToolBarIcon(self,action):self.toolbar.removeAction(action)
        def removeDockWidget(self,dock):self.window.removeDockWidget(dock)
    iface=Interface();iface.window.show()
    outcomes=[]
    for iteration in range(2):
        plugin=classFactory(iface);plugin.initGui();app.processEvents()
        assert isinstance(plugin.dock,QDockWidget) and plugin.dock.isHidden()
        assert len(iface.menu.actions())==len(iface.toolbar.actions())==1
        plugin.action.trigger();app.processEvents()
        assert not plugin.dock.isHidden()
        assert {'build','build-hms','validate-hms','full-run'}<=set(plugin.dock_content.action_buttons)
        if iteration==0:iface.window.grab().save(str(output/'plugin_offscreen.png'))
        plugin.unload();app.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()
        assert plugin.action is None and plugin.dock is None and plugin.dock_content is None
        assert len(iface.menu.actions())==len(iface.toolbar.actions())==0
        outcomes.append({'cycle':iteration+1,'registered_actual_dock':True,'show_action':True,'unload_clean':True})
    iface.window.close();iface.window.deleteLater();app.sendPostedEvents(None,QEvent.DeferredDelete)
    report={'qgis_version':Qgis.QGIS_VERSION,'cycles':outcomes,'desktop_installed':False,
            'limitations':'Real offscreen QGIS/Qt widgets with a minimal interface adapter; not an interactive QGIS desktop/map-tool/full-run acceptance test.'}
    (output/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    app.exitQgis()
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('new_output',type=Path)
    run(parser.parse_args().new_output)
