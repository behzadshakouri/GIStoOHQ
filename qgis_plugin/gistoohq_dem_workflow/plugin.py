from __future__ import annotations

from .dock import DemWorkflowDock


class GIStoOHQDemWorkflowPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dock = None
        self.dock_content = None

    def initGui(self):
        from qgis.PyQt.QtWidgets import QAction
        from qgis.PyQt.QtCore import Qt

        self.action = QAction("GIStoOHQ DEM Workflow", self.iface.mainWindow())
        self.action.triggered.connect(self.show_dock)
        self.iface.addPluginToMenu("&GIStoOHQ", self.action)
        self.iface.addToolBarIcon(self.action)
        # DemWorkflowDock is a controller around its real QDockWidget. Passing
        # the controller itself causes QGIS/SIP to reject addDockWidget().
        self.dock_content = DemWorkflowDock(self.iface)
        self.dock = self.dock_content.widget
        self.dock.setObjectName("GIStoOHQ_DEM_Workflow_Dock")
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.hide()

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu("&GIStoOHQ", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        self.dock_content = None

    def show_dock(self):
        if self.dock is not None:
            self.dock.show()
            self.dock.raise_()
            self.dock.activateWindow()
