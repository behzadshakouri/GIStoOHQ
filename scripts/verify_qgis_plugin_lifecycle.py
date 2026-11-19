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
    from qgis.PyQt.QtWidgets import QMainWindow,QDockWidget,QDialog,QPushButton,QFormLayout,QLineEdit
    from qgis.PyQt.QtCore import QEvent,QTimer
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
    data_checks=[]
    for iteration in range(2):
        plugin=classFactory(iface);plugin.initGui();app.processEvents()
        assert isinstance(plugin.dock,QDockWidget) and plugin.dock.isHidden()
        assert len(iface.menu.actions())==len(iface.toolbar.actions())==1
        plugin.action.trigger();app.processEvents()
        assert not plugin.dock.isHidden()
        assert {'build','build-hms','validate-hms','full-run'}<=set(plugin.dock_content.action_buttons)
        if iteration==0:iface.window.grab().save(str(output/'plugin_offscreen.png'))
        if iteration==0:
            # Exercise real modal widgets and clicks, but capture command dispatch
            # so this UI check cannot start provider downloads or model jobs.
            captured=[]; errors=[]
            content=plugin.dock_content
            original_start=content._start_argv
            content._start_argv=lambda action,argv:captured.append((action,argv))
            site=output/'ui_fixture_site.yaml';site.write_text('site_id: ui_fixture\n')
            def check_dialog(resume):
                dialog=app.activeModalWidget()
                try:
                    assert isinstance(dialog,QDialog)
                    fields={}
                    for form in dialog.findChildren(QFormLayout):
                        for row in range(form.rowCount()):
                            label=form.itemAt(row,QFormLayout.LabelRole)
                            item=form.itemAt(row,QFormLayout.FieldRole)
                            if label and label.widget() and item:
                                widget=item.widget()
                                if widget is None and item.layout():
                                    widget=item.layout().itemAt(0).widget()
                                if isinstance(widget,QLineEdit):fields[label.widget().text()]=widget
                    assert {'Gauge basin polygon','Basin weather folder','Weather download limit (MB)'}<=set(fields)
                    fields['SiteSpec'].setText(str(site))
                    fields['Gauge basin polygon'].setText(str(output/'ui_fixture_basin.geojson'))
                    fields['Basin weather folder'].setText(str(output/'never_created_weather'))
                    fields['Weather download limit (MB)'].setText('32')
                    label='Resume basin weather' if resume else 'Get basin rainfall + reference ET'
                    button=next(b for b in dialog.findChildren(QPushButton) if b.text()==label)
                    fields['Selected USGS station ID'].setText('')
                    count=len(captured);button.click()
                    assert len(captured)==count and dialog.isVisible()
                    fields['Selected USGS station ID'].setText('01650800');button.click()
                    action,argv=captured[-1]
                    assert action==('basin-forcing-resume' if resume else 'basin-forcing')
                    assert argv[:3]==['ohqbuild','data','basin-forcing']
                    assert argv[argv.index('--station-id')+1]=='01650800'
                    assert ('--resume' in argv)==resume
                    data_checks.append({'resume':resume,'invalid_station_blocked':True,'command':argv})
                except Exception as exc:errors.append(repr(exc))
                finally:
                    if dialog:dialog.reject()
            try:
                for resume in (False,True):
                    QTimer.singleShot(0,lambda value=resume:check_dialog(value))
                    content.configure_watershed_data()
                assert not errors,errors
                assert len(captured)==2 and not (output/'never_created_weather').exists()
            finally:content._start_argv=original_start
        plugin.unload();app.sendPostedEvents(None,QEvent.DeferredDelete);app.processEvents()
        assert plugin.action is None and plugin.dock is None and plugin.dock_content is None
        assert len(iface.menu.actions())==len(iface.toolbar.actions())==0
        outcomes.append({'cycle':iteration+1,'registered_actual_dock':True,'show_action':True,'unload_clean':True})
    iface.window.close();iface.window.deleteLater();app.sendPostedEvents(None,QEvent.DeferredDelete)
    report={'qgis_version':Qgis.QGIS_VERSION,'cycles':outcomes,'data_dialog_checks':data_checks,'provider_jobs_started':False,'desktop_installed':False,
            'limitations':'Real offscreen QGIS/Qt widgets with a minimal interface adapter; not an interactive QGIS desktop/map-tool/full-run acceptance test.'}
    (output/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    app.exitQgis()
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('new_output',type=Path)
    run(parser.parse_args().new_output)
