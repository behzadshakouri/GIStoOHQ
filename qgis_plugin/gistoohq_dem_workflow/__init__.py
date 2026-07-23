def classFactory(iface):
    from .plugin import GIStoOHQDemWorkflowPlugin

    return GIStoOHQDemWorkflowPlugin(iface)
