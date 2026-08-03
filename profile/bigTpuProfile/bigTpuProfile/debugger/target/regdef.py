"""Select the independently generated register-definition module by chip."""

from importlib import import_module
from types import ModuleType


REGDEF_MODULE_BY_CHIP = {
    "AKS": ".regdef_aks",
    "AKSV": ".regdef_aksv",
}


def get_regdef(chip: str) -> ModuleType:
    try:
        module_name = REGDEF_MODULE_BY_CHIP[chip.upper()]
    except KeyError as exc:
        supported = ", ".join(sorted(REGDEF_MODULE_BY_CHIP))
        raise ValueError(
            f"unsupported AKS-family chip {chip!r}; expected one of: {supported}"
        ) from exc
    return import_module(module_name, package=__package__)
