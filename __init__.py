from typing_extensions import override
from comfy_api.latest import ComfyExtension, io
from .nodes import NKDKleinPresampling, NKDKleinPostsampling
from .ref_schedule import NKDKleinReferenceWeight
from .prompt_builder import NKDKleinPromptBuilder

WEB_DIRECTORY = "./js"


class NKDKleinNodesExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [
            NKDKleinPresampling,
            NKDKleinPostsampling,
            NKDKleinReferenceWeight,
            NKDKleinPromptBuilder,
        ]


async def comfy_entrypoint() -> NKDKleinNodesExtension:
    return NKDKleinNodesExtension()


# Legacy mappings required for custom_nodes/ discovery
NODE_CLASS_MAPPINGS = {
    "NKDKleinPresampling":  NKDKleinPresampling,
    "NKDKleinPostsampling": NKDKleinPostsampling,
    "NKDKleinReferenceWeight": NKDKleinReferenceWeight,
    "NKDKleinPromptBuilder": NKDKleinPromptBuilder,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "NKDKleinPresampling":  "😺NKD Klein Presampling",
    "NKDKleinPostsampling": "😺NKD Klein Postsampling",
    "NKDKleinReferenceWeight": "😺NKD Klein Reference Weight",
    "NKDKleinPromptBuilder": "😺NKD Klein Prompt Builder",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
