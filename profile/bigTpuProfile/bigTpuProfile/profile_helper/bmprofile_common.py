"""Common binary definitions used by the CDM profile pipeline."""

import ctypes as ct
from collections import namedtuple
from enum import Enum
from itertools import chain


class Arch(Enum):
    UNKNOWN = -1
    bm1684 = 1
    bm1684x = 3
    bm1688 = 4
    AKS = 5
    AKSV = 6
    AKS_alias = 8800
    AKSV_alias = 140814


class BlockType(Enum):
    UNKNOWN = -1
    SUMMARY = 1
    COMPILER_LOG = 2
    MONITOR_BD = 3
    MONITOR_GDMA = 4
    DYN_DATA = 5
    DYN_EXTRA = 6
    FIRMWARE_LOG = 7
    COMMAND = 8
    BMLIB = 9
    BMLIB_EXTRA = 10
    MONITOR_SDMA = 11
    MONITOR_CDMA = 12
    BLOCK_DES_BDC = 13
    BLOCK_DES_GDMA = 14
    BLOCK_DES_SDMA = 15
    BLOCK_DES_CDMA = 16
    BLOCK_DES_KV = 17


class DynExtraType(Enum):
    STRING = 0
    BINARY = 1
    CUSTOM = 100


DynExtra = namedtuple("DynExtra", "profile_id type content")


class dictStructure(ct.Structure):
    """ctypes structure with mapping-style access used by generated defs."""

    _alias_ = {}

    def items(self):
        return zip(self.keys(), self.values())

    def _properties(self):
        attributes = self.__class__.__dict__
        return filter(
            lambda key: isinstance(attributes[key], property),
            attributes,
        )

    def keys(self):
        return chain(
            (field[0] for field in self._fields_),
            self._properties(),
        )

    def values(self):
        return (getattr(self, key) for key in self.keys())

    def __getattr__(self, key):
        if key in self._alias_:
            return getattr(self, self._alias_[key])
        return self.__dict__[key]

    def __setattr__(self, key, value):
        if key in self._alias_:
            super().__setattr__(self._alias_[key], value)
            return
        field_names = {field[0] for field in self._fields_}
        if key in field_names or isinstance(
            getattr(self.__class__, key, None), property
        ):
            super().__setattr__(key, value)
            return
        self.__dict__[key] = value

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def __repr__(self):
        return str(dict(self.items()))


__all__ = [
    "Arch",
    "BlockType",
    "DynExtra",
    "DynExtraType",
    "dictStructure",
]
