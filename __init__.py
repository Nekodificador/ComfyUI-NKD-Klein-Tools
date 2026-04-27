from typing_extensions import override
from comfy_api.latest import ComfyExtension, io
from .nodes import NKDKleinPresampling, NKDKleinPostsampling


class NKDKleinNodesExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [NKDKleinPresampling, NKDKleinPostsampling]


async def comfy_entrypoint() -> NKDKleinNodesExtension:
    return NKDKleinNodesExtension()


# Legacy mappings required for custom_nodes/ discovery
NODE_CLASS_MAPPINGS = {
    "NKDKleinPresampling":  NKDKleinPresampling,
    "NKDKleinPostsampling": NKDKleinPostsampling,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "NKDKleinPresampling":  "😺NKD Klein Presampling",
    "NKDKleinPostsampling": "😺NKD Klein Postsampling",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
