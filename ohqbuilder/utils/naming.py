def site_to_model_name(site: str) -> str:
    return site.rstrip("/").split("/")[-1].replace("-", "_")

def subbasin_name(i: int) -> str:
    return f"Subbasin_{i}"

def reach_name(i: int) -> str:
    return f"Reach_{i}"

def junction_name(i: int) -> str:
    return f"Junction_{i}"
