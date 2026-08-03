"""Shared register info file parsing utilities for perfAIDoc."""
import os
import math


# ─── Value converters for Doc mode ──────────────────────────────────

def convert_tiu_value(attr, val):
    """Doc TIU value converter: numeric strings → int."""
    return int(val) if val.isnumeric() else val


def convert_dma_value(attr, val):
    """Doc DMA value converter: int (except burst/width), bandwidth → float."""
    if val.isnumeric() and 'burst' not in attr.lower() and 'width' not in attr.lower():
        return int(val)
    if 'bandwidth' in attr.lower():
        try:
            return float(val)
        except ValueError:
            pass
    return val


def convert_cdma_value(attr, val):
    """Doc CDMA value converter: int (except burst/width), no float conversion."""
    if val.isnumeric() and 'burst' not in attr.lower() and 'width' not in attr.lower():
        return int(val)
    return val


# ─── Header parsing ─────────────────────────────────────────────────

def parse_header(filepath, header_end_tag):
    """
    Parse chip architecture header from a register info file.
    Reads key-value lines until the first occurrence of header_end_tag.

    Returns:
        (chip_args_dict, linecount) where linecount includes the tag line.
    """
    chip_args = {}
    linecount = 0
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        return chip_args, linecount
    with open(filepath, "r") as f:
        for line in f:
            linecount += 1
            if "\t" in line:
                fields = line.split(': ')
                attr = fields[0][1:]
                val = fields[1][:-1]
                chip_args[attr] = val
            if header_end_tag in line:
                break
    return chip_args, linecount


# ─── Record parsing ─────────────────────────────────────────────────

def _collect_fields(rows, tag):
    """Collect all field names appearing in records (after first tag occurrence)."""
    field_set = set()
    seen_tag = False
    for row in rows:
        if tag in row:
            seen_tag = True
        if "\t" in row and seen_tag:
            attr = row.split(': ')[0][1:]
            field_set.add(attr)
    return field_set


def parse_records(filepath, tag, columns, skip_lines=0,
                  has_func_type=False, value_converter=None):
    """
    Parse register info records from a txt file.

    Two modes:
    - skip_lines > 0: records start immediately after a pre-parsed header
    - skip_lines == 0: chip arch header parsed inline

    Args:
        filepath: path to the register info txt file
        tag: record delimiter (e.g. '__TIU_REG_INFO__', '__TDMA_REG_INFO__')
        columns: default column list for field initialization
        skip_lines: number of header lines to skip
        has_func_type: True for TIU files (have function type lines without tab)
        value_converter: callable(attr, val) → converted value, or None

    Returns:
        (chip_arch_dict, records) where:
        - chip_arch_dict is None when skip_lines > 0 or no __CHIP_ARCH_ARGS__ found
        - records is list of dicts, one per instruction
    """
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        return None, []

    with open(filepath) as f:
        rows = f.readlines()[skip_lines:]

    field_set = _collect_fields(rows, tag)
    field_list = list(field_set) if len(field_set) >= len(columns) else columns

    chip_arch_dict = None
    records = []
    reg_dict = dict.fromkeys(field_list, '')
    reg_count = 0
    has_data = False

    for row in rows:
        # Chip arch marker (only in Doc mode)
        if "__CHIP_ARCH_ARGS__" in row and skip_lines == 0:
            chip_arch_dict = dict()
            continue

        if tag in row:
            reg_count += 1
            if has_data:
                records.append(reg_dict)
            reg_dict = dict.fromkeys(field_list, '')
            has_data = False
        elif has_func_type and "\t" not in row and (reg_count > 0 or skip_lines > 0):
            # Function type lines (TIU only): non-tab lines like "CONV"
            reg_dict['Function Type'] = row[:-2]
            has_data = True
        elif reg_count == 0 and chip_arch_dict is not None:
            # Chip arch data lines (Doc mode, before first tag)
            fields = row.split(': ')
            if len(fields) >= 2:
                attr = fields[0][1:]
                val = fields[1][:-1]
                chip_arch_dict[attr] = val
        elif "\t" in row:
            # Record field lines
            fields = row.split(': ')
            attr = fields[0][1:]
            val = fields[1][:-1]
            if value_converter:
                val = value_converter(attr, val)
            reg_dict[attr] = val
            has_data = True

    # Last record
    if has_data:
        records.append(reg_dict)

    if chip_arch_dict is not None and 'Platform' not in chip_arch_dict:
        chip_arch_dict['Platform'] = 'pmu'

    return chip_arch_dict, records


# ─── Layer mapping ──────────────────────────────────────────────────

def apply_layer_map_simple(records, layer_map, core_id):
    """
    Apply layer mapping (Doc style): (cmd_id, core_id) → [layer_id, layer_name].
    Modifies records in-place.
    """
    for reg_dict in records:
        k = int(reg_dict['Cmd Id'])
        if (k, core_id) in layer_map:
            layer_id_name = layer_map[(k, core_id)]
        else:
            layer_id_name = ['-', '-']
        reg_dict['Layer Id'] = layer_id_name[0]
        reg_dict['Layer Name'] = layer_id_name[1]


def apply_layer_map_full(records, layer_map, core_id):
    """
    Apply layer mapping (Web style):
    (cmd_id, core_id) → [layer_id, layer_name, subnet_id, subnet_type, file_line, is_local].
    Modifies records in-place.
    """
    for reg_dict in records:
        k = int(reg_dict['Cmd Id'])
        layer_info = ['-', '-', '-', '-', '-', False]
        if (k, core_id) in layer_map:
            layer_info = layer_map[(k, core_id)]
            if all(isinstance(x, float) and math.isnan(x) for x in layer_info):
                layer_info = ['-', '-', '-', '-', '-', False]
        reg_dict['Layer Id'] = int(layer_info[0]) if layer_info[0] != '-' else '-'
        reg_dict['Layer Name'] = layer_info[1]
        reg_dict['Subnet Id'] = int(layer_info[2]) if layer_info[0] != '-' else '-'
        reg_dict['Subnet Type'] = layer_info[3]
        reg_dict['File Line'] = int(layer_info[4]) if layer_info[0] != '-' else '-'
        reg_dict['is_local'] = int(layer_info[5])


def apply_default_layer_map(records):
    """Apply default layer mapping ['-', '-'] to all records (e.g. for CDMA)."""
    for reg_dict in records:
        reg_dict['Layer Id'] = '-'
        reg_dict['Layer Name'] = '-'


def filter_by_engine_id(records, engine_id):
    """Filter records to only those matching the given Engine Id value."""
    return [r for r in records if r.get('Engine Id') == engine_id]
