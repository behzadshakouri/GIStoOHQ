from __future__ import annotations
from ..model.watershed import Watershed

class TopologyValidator:
    def validate(self, watershed: Watershed) -> None:
        names = watershed.element_names()
        problems = []
        for link in watershed.topology:
            if link.name not in names:
                problems.append(f"Topology source not found in model: {link.name}")
            if link.ds_name and link.ds_name not in names:
                problems.append(f"Dangling downstream target: {link.name} -> {link.ds_name}")
        graph = {link.name: link.ds_name for link in watershed.topology if link.name in names}
        graph[watershed.outlet.name] = None
        for start in list(graph):
            seen = []
            cur = start
            while cur is not None:
                if cur in seen:
                    problems.append("Cycle: " + " -> ".join(seen + [cur]))
                    break
                seen.append(cur)
                cur = graph.get(cur)
                if cur is not None and cur not in graph:
                    problems.append(f"No graph entry for {cur}")
                    break
        if problems:
            raise ValueError("Topology validation failed:\n" + "\n".join(problems))
