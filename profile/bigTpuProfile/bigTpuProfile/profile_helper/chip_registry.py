"""Chip metadata and adapters for generated profile-definition modules."""

from importlib import import_module
import sys
from types import ModuleType
from typing import Dict, NamedTuple, Optional, Tuple


class ChipProfileSpec(NamedTuple):
    name: str
    arch_values: Tuple[int, ...]
    profile_module: str
    target: str
    command_info_has_sdma: bool
    command_id_wrap_max: Optional[int]
    tiu_wrap_command: Optional[Tuple[int, int]]
    dma_wrap_command: Optional[Tuple[int, int]]


REQUIRED_PROFILE_API = (
    "arch_name",
    "CORE_NUM",
    "CDMA_NUM",
    "BDProfileFormat",
    "GDMAProfileFormat",
    "CDMAProfileFormat",
    "ProfileFormat",
    "DynRecordType",
    "EngineType",
    "BDCommandParser",
    "GDMACommandParser",
    "CDMACommandParser",
    "DMA_ARCH",
    "TIU_ARCH",
    "get_dma_info",
    "get_dma_info_dyn",
    "get_tiu_info",
    "get_tiu_info_dyn",
    "bd_sys_code",
    "dma_sys_code",
    "cdma_sys_code",
    "profile_init_cmd_num",
)


class ChipProfile:
    """Read-only facade over one generated ``*_defs.py`` module."""

    def __init__(self, spec: ChipProfileSpec, module: ModuleType):
        self.spec = spec
        self.module = module
        self._validate()

    def _validate(self):
        missing = [name for name in REQUIRED_PROFILE_API if not hasattr(self.module, name)]
        if missing:
            raise RuntimeError(
                f"{self.spec.name} profile definitions are missing: "
                f"{', '.join(missing)}"
            )
        if self.module.arch_name.upper() != self.spec.name:
            raise RuntimeError(
                f"{self.spec.profile_module} declares arch_name="
                f"{self.module.arch_name!r}, expected {self.spec.name!r}"
            )

    def __getattr__(self, name):
        return getattr(self.module, name)

    @property
    def name(self):
        return self.spec.name

    def should_insert_wrap_commands(self, next_tiu_id: int) -> bool:
        return (
            self.spec.command_id_wrap_max is not None
            and self.spec.tiu_wrap_command is not None
            and self.spec.dma_wrap_command is not None
            and next_tiu_id == self.spec.command_id_wrap_max
        )


CHIP_SPECS = (
    ChipProfileSpec(
        name="AKS",
        arch_values=(5, 8800),
        profile_module="AKS_defs",
        target="AKS",
        command_info_has_sdma=True,
        command_id_wrap_max=None,
        tiu_wrap_command=None,
        dma_wrap_command=None,
    ),
    ChipProfileSpec(
        name="AKSV",
        arch_values=(6, 140814),
        profile_module="AKSV_defs",
        target="AKSV",
        command_info_has_sdma=False,
        command_id_wrap_max=0x3FFFF,
        tiu_wrap_command=(15, 3),
        dma_wrap_command=(6, 3),
    ),
)

CHIP_SPEC_BY_ARCH: Dict[int, ChipProfileSpec] = {
    value: spec for spec in CHIP_SPECS for value in spec.arch_values
}
CHIP_SPEC_BY_NAME: Dict[str, ChipProfileSpec] = {
    spec.name: spec for spec in CHIP_SPECS
}


def get_chip_spec_by_arch(arch) -> ChipProfileSpec:
    value = getattr(arch, "value", arch)
    try:
        return CHIP_SPEC_BY_ARCH[int(value)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"unsupported profile architecture: {arch!r}") from exc


def get_chip_spec(name: str) -> ChipProfileSpec:
    try:
        return CHIP_SPEC_BY_NAME[name.upper()]
    except (AttributeError, KeyError) as exc:
        supported = ", ".join(sorted(CHIP_SPEC_BY_NAME))
        raise ValueError(
            f"unsupported chip {name!r}; expected one of: {supported}"
        ) from exc


def load_chip_profile(arch) -> ChipProfile:
    spec = get_chip_spec_by_arch(arch)
    from . import binary, bmprofile_common
    from ..debugger import target_common

    sys.modules.setdefault("profile_helper.bmprofile_common", bmprofile_common)
    sys.modules.setdefault("profile_helper.binary", binary)
    sys.modules.setdefault("debugger.target_common", target_common)
    module = import_module(f"{__package__}.{spec.profile_module}")
    return ChipProfile(spec, module)
