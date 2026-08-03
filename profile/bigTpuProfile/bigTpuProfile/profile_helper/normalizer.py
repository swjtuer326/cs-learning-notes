"""Normalization helpers for raw PMU records."""

import itertools
import math


UINT32_MODULUS = 1 << 32


def normalize_command_ids(records, observe_wrap=None):
    delta_id = 0
    last_id = 0
    for record in records:
        if last_id > 65000 and record.inst_id < 1000:
            delta_id += 1 << 16
        if observe_wrap is not None:
            observe_wrap(last_id, record.inst_id)
        last_id = record.inst_id
        record.inst_id += delta_id


def normalize_time(records):
    last_time = 0
    delta_time = 0
    for record in records:
        current_time = record.inst_start_time + delta_time
        if current_time < last_time:
            delta_time += UINT32_MODULUS
        record.inst_start_time += delta_time
        record.inst_end_time += delta_time
        if record.inst_end_time < record.inst_start_time:
            record.inst_end_time += UINT32_MODULUS
        last_time = record.inst_end_time


def normalize_cdma_time(records):
    last_start = 0
    last_end = 0
    delta_time = 0
    for record in records:
        current_start = record.inst_start_time + delta_time
        current_end = record.inst_end_time + delta_time
        if current_start < last_start and current_end < last_end:
            delta_time += UINT32_MODULUS
        record.inst_start_time += delta_time
        record.inst_end_time += delta_time
        if record.inst_end_time < record.inst_start_time:
            record.inst_end_time += UINT32_MODULUS
        last_start = record.inst_start_time
        last_end = record.inst_end_time


def adjust_send_retire_order(records):
    for index in range(1, len(records) - 1):
        previous = records[index - 1]
        current = records[index]
        following = records[index + 1]
        current_gap = current.inst_start_time - previous.inst_end_time
        following_gap = following.inst_start_time - previous.inst_end_time
        swap = (
            following_gap < current_gap
            and current_gap > 0
            and following_gap > 0
        )
        if (
            current_gap < 0
            and following_gap > 0
            and following.inst_start_time - following.inst_end_time > 0
        ):
            swap = True
        if swap:
            records[index], records[index + 1] = (
                records[index + 1],
                records[index],
            )


def adjust_command_order(records):
    length = len(records)
    for index, record in enumerate(records):
        if index == 0 or record.inst_id >= records[index - 1].inst_id:
            continue
        if index >= 2 and record.inst_id - records[index - 2].inst_id == 1:
            records[index], records[index - 1] = (
                records[index - 1],
                records[index],
            )
        elif (
            index + 1 < length
            and records[index + 1].inst_id - records[index - 1].inst_id == 1
        ):
            records[index], records[index + 1] = (
                records[index + 1],
                records[index],
            )


def align_core_time(
    bd_pairs,
    gdma_pairs,
    sdma_pairs,
    cdma_pairs,
    sync_points,
    cdma_core_ids,
):
    if len(sync_points) != len(bd_pairs):
        raise ValueError(
            "profile synchronization points do not match TIU core records"
        )
    if not sync_points:
        return

    base_cycle = sync_points[0]
    for core_id, (bd_pair, gdma_pair, cycle) in enumerate(
        zip(bd_pairs, gdma_pairs, sync_points)
    ):
        if core_id == 0:
            continue
        delta_cycle = cycle - base_cycle
        for record in itertools.chain(bd_pair, gdma_pair):
            record.inst_start_time -= delta_cycle
            record.inst_end_time -= delta_cycle

    for core_id, (sdma_pair, cycle) in enumerate(zip(sdma_pairs, sync_points)):
        if core_id == 0:
            continue
        delta_cycle = cycle - base_cycle
        for record in sdma_pair:
            record.inst_start_time -= delta_cycle
            record.inst_end_time -= delta_cycle

    for port, cdma_pair in enumerate(cdma_pairs):
        core_id = cdma_core_ids[port]
        if core_id is None or not cdma_pair:
            continue
        delta_cycle = sync_points[core_id] - base_cycle
        for record in cdma_pair:
            record.inst_start_time -= delta_cycle
            record.inst_end_time -= delta_cycle


def shift_time_to_zero(bd_pairs, gdma_pairs, sdma_pairs, cdma_pairs):
    start_cycle = math.inf
    for engine_pairs in (bd_pairs, gdma_pairs, sdma_pairs, cdma_pairs):
        for records in engine_pairs:
            if records:
                start_cycle = min(start_cycle, records[0].inst_start_time)
    if start_cycle == math.inf:
        return

    for engine_pairs in (bd_pairs, gdma_pairs, sdma_pairs, cdma_pairs):
        for records in engine_pairs:
            for record in records:
                record.inst_start_time -= start_cycle
                record.inst_end_time -= start_cycle
                if record.inst_start_time < 0 or record.inst_end_time < 0:
                    raise ValueError("profile timestamp normalization became negative")
