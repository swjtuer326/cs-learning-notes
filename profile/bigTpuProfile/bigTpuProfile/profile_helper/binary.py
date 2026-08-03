"""Binary readers for CDM profile blocks and fixed-size records."""

import ctypes as ct
import logging
import os
import struct
from collections import namedtuple

from .bmprofile_common import BlockType, DynExtra, DynExtraType


BlockItem = namedtuple("BlockItem", "type content")


class FixedItemWrapper:
    def __init__(self, **kwargs):
        self._fields_ = []
        for key, value in kwargs.items():
            self.add_kv(key, value)

    def __str__(self):
        values = [f"{key}:{getattr(self, key)}" for key in self._fields_]
        return ",".join(sorted(values))

    def add_kv(self, key, value):
        self._fields_.append(key)
        setattr(self, key, value)


def parse_fixed_length_items(raw_data, fixed_type):
    item_size = ct.sizeof(fixed_type)
    complete_size = len(raw_data) - (len(raw_data) % item_size)
    if complete_size != len(raw_data):
        logging.warning(
            "raw_data is incomplete for %s: got %d bytes, item size is %d",
            fixed_type.__name__,
            len(raw_data),
            item_size,
        )

    items = []
    for offset in range(0, complete_size, item_size):
        item_data = raw_data[offset:offset + item_size]
        raw_item = ct.cast(item_data, ct.POINTER(fixed_type)).contents
        item = FixedItemWrapper()
        for field in raw_item._fields_:
            field_name = field[0]
            setattr(item, field_name, getattr(raw_item, field_name))
        item._fields_ = [field[0] for field in raw_item._fields_]
        items.append(item)
    return items


def parse_monitor_bd(raw_data, archlib):
    return parse_fixed_length_items(raw_data, archlib.BDProfileFormat)


def parse_monitor_gdma(raw_data, archlib):
    return parse_fixed_length_items(raw_data, archlib.GDMAProfileFormat)


def parse_monitor_cdma(raw_data, archlib):
    return parse_fixed_length_items(raw_data, archlib.CDMAProfileFormat)


def parse_data_blocks(filename):
    if not os.path.isfile(filename):
        return None

    blocks = []
    with open(filename, "rb") as profile_file:
        while True:
            header = profile_file.read(8)
            if not header:
                break
            if len(header) != 8:
                raise ValueError(
                    f"{filename}: incomplete block header ({len(header)} bytes)"
                )
            block_type_value, block_length = struct.unpack("II", header)
            try:
                block_type = BlockType(block_type_value)
            except ValueError as exc:
                raise ValueError(
                    f"{filename}: unknown block type {block_type_value}"
                ) from exc
            content = profile_file.read(block_length)
            if len(content) != block_length:
                raise ValueError(
                    f"{filename}: block {block_type.name} declares "
                    f"{block_length} bytes, got {len(content)}"
                )
            blocks.append(BlockItem(block_type, content))
    return blocks


def parse_dyn_extra(raw_data, serialize=False):
    extra_data = [] if serialize else {}
    header_length = 12
    offset = 0

    while offset < len(raw_data):
        remaining = len(raw_data) - offset
        if remaining < header_length:
            logging.warning(
                "dynamic-extra data ends with an incomplete %d-byte header",
                remaining,
            )
            break

        profile_id, extra_type_value, extra_length = struct.unpack(
            "III", raw_data[offset:offset + header_length]
        )
        offset += header_length
        remaining = len(raw_data) - offset
        if remaining < extra_length:
            logging.warning(
                "dynamic-extra record %d declares %d bytes, got %d",
                profile_id,
                extra_length,
                remaining,
            )
            break

        content = raw_data[offset:offset + extra_length]
        offset += extra_length
        extra_item = DynExtra(
            profile_id,
            DynExtraType(extra_type_value),
            content,
        )
        if serialize:
            extra_data.append(extra_item)
        else:
            extra_data.setdefault(profile_id, []).append(extra_item)

    return extra_data


__all__ = [
    "FixedItemWrapper",
    "parse_data_blocks",
    "parse_dyn_extra",
    "parse_fixed_length_items",
    "parse_monitor_bd",
    "parse_monitor_cdma",
    "parse_monitor_gdma",
]
