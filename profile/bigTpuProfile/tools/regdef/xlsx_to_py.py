#!/usr/bin/env python3
"""Generate one complete regdef module for one chip from register workbooks."""

import argparse
import keyword
import math
import re
from pathlib import Path
from time import gmtime, strftime

import numpy as np
import pandas
from jinja2 import Template

from chip_configs import CHIP_SHEET_MAPS, REGISTER_ALIASES


MATCH_ILLEGAL = re.compile("[^0-9A-Za-z_]")

CTYPE_TEMPLATE = Template(
    """
class {{class_name}}_reg(atomic_reg):
    OP_NAME = "{{op_name}}"
    _fields_ = [{% for field, field_length in fields %}
        ("{{field}}", ctypes.c_uint64, {{field_length}}),
        {%- endfor %}
    ]
    {% for key in valid_key %}
    {{key}}: int
    {%- endfor %}

    length: int = {{length}}

    {% for raw, valid in invalid_key %}
    @property
    def {{valid}}(self) -> int:
        return self["{{raw}}"]
    {%- endfor %}
"""
)

TAIL_TEMPLATE = Template(
    """

op_class_dic: Dict[str, Type[atomic_reg]] = {
    {% for cmd_type, class_name in cmd %}
    "{{cmd_type}}": {{class_name}}_reg,
    {%- endfor %}
}
"""
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate an independent register-definition module."
    )
    parser.add_argument("--chip", required=True, help="Chip name written to the header")
    parser.add_argument("--tiu-xlsx", required=True, type=Path)
    parser.add_argument("--dma-xlsx", required=True, type=Path)
    parser.add_argument(
        "--cdma-xlsx",
        type=Path,
        help="CDMA workbook. Omit only when the chip has no CDMA definitions.",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        type=Path,
        help="Output module, for example regdef_aks.py",
    )
    return parser.parse_args()


def pd_to_dict(df):
    columns = {str(column).strip().lower(): column for column in df.columns}

    def find_column(prefix, *, exclude=()):
        prefix = prefix.lower()
        exclude = tuple(item.lower() for item in exclude)
        for normalized, original in columns.items():
            if prefix in normalized and not any(item in normalized for item in exclude):
                return original
        raise ValueError(f"missing {prefix!r} column; found: {list(columns)}")

    field_column = find_column("field")
    length_column = find_column("length")
    high_column = find_column("high", exclude=("sw",))
    low_column = find_column("low", exclude=("sw",))

    parsed = df.copy()
    parsed["_length"] = pandas.to_numeric(parsed[length_column], errors="coerce")
    parsed["_high"] = pandas.to_numeric(parsed[high_column], errors="coerce")
    parsed["_low"] = pandas.to_numeric(parsed[low_column], errors="coerce")
    parsed = parsed.dropna(subset=[field_column, "_length", "_high", "_low"])

    fields = []
    expected_low = 0

    def append_reserved(start, end):
        while start <= end:
            boundary = ((start // 64) + 1) * 64
            field_end = min(end + 1, boundary)
            fields.append((f"reserved_{start}_{field_end - 1}", field_end))
            start = field_end

    for _, row in parsed.iterrows():
        field = str(row[field_column]).replace("des_", "")
        length = int(row["_length"])
        high = int(row["_high"])
        low = int(row["_low"])
        if high - low + 1 != length:
            raise ValueError(
                f"{field}: high={high}, low={low}, length={length} are inconsistent"
            )
        if low < expected_low:
            raise ValueError(
                f"{field}: overlaps previous field at bit {low}; "
                f"expected bit {expected_low} or later"
            )
        if low > expected_low:
            append_reserved(expected_low, low - 1)
        if low // 64 != high // 64:
            if "rsv" not in field.lower() and "reserved" not in field.lower():
                raise ValueError(
                    f"{field}: non-reserved field crosses a 64-bit boundary "
                    f"({low}..{high})"
                )
            append_reserved(low, high)
        else:
            fields.append((field, high + 1))
        expected_low = high + 1

    if not fields:
        raise ValueError("no physical register fields found")
    return fields


def read_sheets(workbook: Path, sheet_map):
    if not workbook.is_file():
        raise FileNotFoundError(workbook)
    available = set(pandas.ExcelFile(workbook).sheet_names)
    missing = [sheet for sheet in sheet_map if sheet not in available]
    if missing:
        raise ValueError(f"{workbook} is missing sheets: {', '.join(missing)}")
    return pandas.read_excel(workbook, sheet_name=list(sheet_map))


def collect_registers(args):
    chip = args.chip.upper()
    try:
        tiu_sheets, dma_sheets, cdma_sheets = CHIP_SHEET_MAPS[chip]
    except KeyError as exc:
        supported = ", ".join(sorted(CHIP_SHEET_MAPS))
        raise ValueError(
            f"unsupported chip {args.chip!r}; expected one of: {supported}"
        ) from exc

    registers = {}
    sources = [
        ("TIU", args.tiu_xlsx, tiu_sheets),
        ("DMA", args.dma_xlsx, dma_sheets),
    ]
    if cdma_sheets and args.cdma_xlsx is None:
        raise ValueError(f"{chip} requires --cdma-xlsx")
    if not cdma_sheets and args.cdma_xlsx is not None:
        raise ValueError(f"{chip} does not define CDMA registers")
    if args.cdma_xlsx is not None:
        sources.append(("CDMA", args.cdma_xlsx, cdma_sheets))

    for engine, workbook, sheet_map in sources:
        for sheet_name, frame in read_sheets(workbook, sheet_map).items():
            op_name = sheet_map[sheet_name] or sheet_name
            if op_name in registers:
                raise ValueError(f"duplicate register {op_name!r} from {engine}")
            try:
                registers[op_name] = normalize_definition(
                    op_name, pd_to_dict(frame)
                )
            except ValueError as exc:
                raise ValueError(
                    f"{workbook}:{sheet_name}: {exc}"
                ) from exc

    for op_name, source_op_name in REGISTER_ALIASES.get(chip, {}).items():
        if op_name in registers:
            raise ValueError(f"duplicate register alias {op_name!r}")
        try:
            registers[op_name] = registers[source_op_name]
        except KeyError as exc:
            raise ValueError(
                f"register alias {op_name!r} references unknown "
                f"source register {source_op_name!r}"
            ) from exc
    return registers


def normalize_definition(op_name, definition):
    """Apply documented errata needed to expose unambiguous field names."""

    aliases = {
        "nchw_copy(tie1)": "nchw_copy",
        "src_nstride/constant_value": "src_nstride",
        "src_nstride/constant": "src_nstride",
        "source_start_addr_ext_h10": "src_start_addr_ext_h10",
        "src_cstride(move length)": "cmd_length",
        "src_start_addr_l32/constant_value": "src_start_addr_l32",
    }
    definition = [(aliases.get(field, field), high) for field, high in definition]

    if op_name in {"DMA_lossy_compress", "DMA_lossy_decompress"}:
        size_fields = {f"src_{axis}size" for axis in "nchw"}
        seen = set()
        normalized = []
        for field, high in definition:
            if field in size_fields and field in seen:
                field = field.replace("src_", "dst_", 1)
            seen.add(field)
            normalized.append((field, high))
        definition = normalized

    return definition


def make_identifier(name):
    identifier = MATCH_ILLEGAL.sub("_", str(name))
    identifier = re.sub("_+", "_", identifier).strip("_")
    if not identifier:
        identifier = "field"
    if identifier[0].isdigit():
        identifier = f"_{identifier}"
    if keyword.iskeyword(identifier):
        identifier = f"{identifier}_"
    return identifier


def unique_identifiers(names):
    seen = {}
    identifiers = []
    for name in names:
        base = make_identifier(name)
        index = seen.get(base, 0)
        seen[base] = index + 1
        identifiers.append(base if index == 0 else f"{base}_{index}")
    return identifiers


def render_register(op_name, definition):
    field_keys, high_bits = zip(*definition)
    field_keys = list(field_keys)
    high_bits = list(high_bits)

    slot_length = math.ceil(high_bits[-1] / 128) * 128
    if high_bits[-1] < slot_length:
        start = high_bits[-1]
        while start < slot_length:
            field_end = min(slot_length, ((start // 64) + 1) * 64)
            field_keys.append(f"reserved_{start}_{field_end - 1}")
            high_bits.append(field_end)
            start = field_end

    missing_boundaries = [
        64 * index
        for index in range(1, math.ceil(slot_length / 64))
        if 64 * index not in high_bits
    ]
    if missing_boundaries:
        raise ValueError(
            f"{op_name}: fields cross ctypes 64-bit storage boundaries: "
            f"{missing_boundaries}"
        )

    fields = list(zip(field_keys, np.diff(high_bits, prepend=0)))
    valid_key = unique_identifiers(field_keys)
    invalid_key = [
        (key, valid)
        for key, valid in zip(field_keys, valid_key)
        if key != valid and valid.isidentifier()
    ]
    class_name = make_identifier(op_name)
    source = CTYPE_TEMPLATE.render(
        op_name=op_name,
        class_name=class_name,
        fields=fields,
        valid_key=valid_key,
        invalid_key=invalid_key,
        length=slot_length,
    )
    return source, class_name


def generate(args):
    registers = collect_registers(args)
    source_files = [args.tiu_xlsx, args.dma_xlsx]
    if args.cdma_xlsx is not None:
        source_files.append(args.cdma_xlsx)

    header = f"""# ==============================================================================
#
# Copyright (C) 2022 sophon Technologies Inc.  All rights reserved.
#
# TPU-MLIR is licensed under the 2-Clause BSD License except for the
# third-party components.
#
# ==============================================================================
#
# automatically generated by {Path(__file__).name}
# time: {strftime('%Y-%m-%d %H:%M:%S', gmtime())}
# chip: {args.chip.upper()}
# source files: {', '.join(str(path) for path in source_files)}
# this file should not be changed manually.

from typing import Dict, Type
import ctypes
from ..target_common import atomic_reg

"""

    classes = []
    commands = []
    for op_name, definition in registers.items():
        source, class_name = render_register(op_name, definition)
        classes.append(source)
        commands.append((op_name, class_name))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        header + "\n".join(classes) + TAIL_TEMPLATE.render(cmd=commands),
        encoding="utf-8",
    )


if __name__ == "__main__":
    generate(parse_args())
