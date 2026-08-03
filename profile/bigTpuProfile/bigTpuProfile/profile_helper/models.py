"""Data models shared by the profile parsing pipeline."""

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class ProfileIteration:
    dyn_data: Any = field(default_factory=dict)
    dyn_extra: Any = field(default_factory=list)
    monitor_gdma: List[Any] = field(default_factory=list)
    monitor_sdma: List[Any] = field(default_factory=list)
    monitor_cdma: List[Any] = field(default_factory=list)
    monitor_bd: List[Any] = field(default_factory=list)
    des_kv: Any = field(default_factory=dict)
    des_bdc: List[Any] = field(default_factory=list)
    des_gdma: List[Any] = field(default_factory=list)
    des_sdma: List[Any] = field(default_factory=list)


@dataclass
class ProfileResult:
    archlib: Any
    bd_events: List[Any]
    gdma_events: List[Any]
    sdma_events: List[Any]
    cdma_events: List[Any]
    num_cores: int
    input_dir: str
    tiu_freq: Optional[int] = None
    tail_offset_ns: Optional[int] = None
    pmu_tail_cycle: Optional[int] = None
