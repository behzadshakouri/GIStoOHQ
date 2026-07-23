from __future__ import annotations

from .dock import DemWorkflowDock


class GIStoOHQDemWorkflowPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None

    def initGui(self):
        from qgis.PyQt.QtWidgets import QAction
        from qgis.PyQt.QtCore import Qt

        self.action = QAction("GIStoOHQ DEM Workflow", self.iface.mainWindow())
        self.action.triggered.connect(self.show_dock)
        self.iface.addPluginToMenu("GIStoOHQ", self.action)
        self.iface.addToolBarIcon(self.action)
        self.dock = DemWorkflowDock(self.iface)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.hide()

    def unload(self):
        if getattr(self, "action", None) is not None:
            self.iface.removePluginMenu("GIStoOHQ", self.action)
            self.iface.removeToolBarIcon(self.action)
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock = None

    def show_dock(self):
        if self.dock is not None:
            self.dock.show()
            self.dock.raise_()
