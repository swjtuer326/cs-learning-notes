#!/usr/bin/python3
# ==============================================================================
#
# Copyright (C) 2022 sophon Technologies Inc.  All rights reserved.
#
# TPU-MLIR is licensed under the 2-Clause BSD License except for the
# third-party components.
#
# ==============================================================================

import os
import json
import uuid
from pathlib import Path
from tqdm import tqdm
from itertools import chain
from copy import deepcopy
from perfetto.trace_builder.proto_builder import TraceProtoBuilder
from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import TrackEvent, TrackDescriptor


def get_realtime_from_cycle(cycle, frequency):
    return int(round(cycle / frequency * 1000, 2))


class PerfettoExporter:
    def __init__(self, parser):
        self.parser = parser

    def _get_necessary_event_data(self, info, engine):
        if engine == 0:
            base_keys = {"Function Name", "Function Type", "Cmd Id", "uArch Rate", "Global Idx", "des_cmd_short", "des_cmd_id_dep", "kernel_func", "batch_launch", "mode"}
            src_keys = None
            dst_keys = None
        elif engine in [1, 3, 4]:
            base_keys = {"Function Name", "Function Type", "Cmd Id", "DDR Bandwidth(GB/s)", "L2M Bandwidth(GB/s)", "Global Idx", "Direction", "cmd_id_dep", "DMA data size(B)", "kernel_func", "batch_launch", "mode"}
            src_keys = {"src_start_addr", "src_nsize", "src_csize", "src_hsize", "src_wsize", "src_nstride", "src_cstride", "src_hstride", "src_wstride"}
            dst_keys = {"dst_start_addr", "dst_nstride", "dst_cstride", "dst_hstride", "dst_wstride", "dst_nsize", "dst_csize", "dst_hsize", "dst_wsize"}
        else:
            raise ValueError(f"Not support parse engine {engine} now.")
        base_res = {}
        for source_key, output_key in (
            ("batch_launch", "Batch Launch"),
            ("kernel_func", "Kernel Function"),
            ("Function Type", "Function Type"),
        ):
            if source_key in info:
                base_res[output_key] = info[source_key]
        for key in info:
            if key in base_keys and key not in {"batch_launch", "kernel_func", "Function Type", "mode"}:
                base_res[key] = info[key]
                if key == "Function Name" and "mode" in info:
                    base_res["Mode"] = info["mode"]
        unnecessary_keys_for_sys = {"uArch Rate", "DDR Bandwidth(GB/s)", "L2M Bandwidth(GB/s)", "Direction", "DMA data size(B)"}
        if info["Function Type"] in ["sys", "SYS"]:
            for k in unnecessary_keys_for_sys:
                if k in base_res:
                    del base_res[k]
            return base_res
        if src_keys:
            base_res["src_info"] = {k: info[k] for k in info if k in src_keys}
        if dst_keys:
            base_res["dst_info"] = {k: info[k] for k in info if k in dst_keys}
        return base_res

    def to_perfetto(self, out_dir, tiu_freq=1000, trace_file=None):
        self.parser.tiu_freq = tiu_freq
        self.parser.archlib.TIU_ARCH["TIU Frequency(MHz)"] = tiu_freq
        p = self.parser

        TRUSTED_PACKET_SEQUENCE_ID = 42
        builder = TraceProtoBuilder()

        num_cores = p.num_cores
        min_start_cycle = 0
        core_has_sdma = [True if (core_id < len(p.sdma_events) and p.sdma_events[core_id]) else False for core_id in range(num_cores)]
        cdma_has_port = [bool(p.cdma_events[port]) for port in range(len(p.cdma_events))] if p.cdma_events else []

        # Helper to define a TrackDescriptor
        def define_custom_track(track_uuid, name, parent_track_uuid=None, child_ordering_mode=None, sibling_order_rank=None, is_counter=False):
            packet = builder.add_packet()
            desc = packet.track_descriptor
            desc.uuid = track_uuid
            desc.name = name
            if parent_track_uuid:
                desc.parent_uuid = parent_track_uuid
            if child_ordering_mode:
                desc.child_ordering = child_ordering_mode
            if sibling_order_rank is not None:
                desc.sibling_order_rank = sibling_order_rank
            if is_counter:
                desc.counter.SetInParent()

        # Helper to add a slice event
        def add_slice_event(start, end, event_track_uuid, name=None, debug_annotations=None, correlation_id=None, categories=None):
            packet = builder.add_packet()
            packet.timestamp = start
            packet.track_event.type = TrackEvent.TYPE_SLICE_BEGIN
            packet.track_event.track_uuid = event_track_uuid
            packet.trusted_packet_sequence_id = TRUSTED_PACKET_SEQUENCE_ID
            if name:
                packet.track_event.name = name
            if categories:
                packet.track_event.categories.extend(categories)
            if correlation_id:
                if type(correlation_id) == int:
                    packet.track_event.correlation_id = correlation_id
                elif type(correlation_id) ==str:
                    packet.track_event.correlation_id_str = correlation_id
            if debug_annotations:
                for key, value in debug_annotations.items():
                    annotation = packet.track_event.debug_annotations.add()
                    annotation.name = key
                    if isinstance(value, bool):
                        annotation.bool_value = value
                    elif isinstance(value, int):
                        annotation.int_value = value
                    elif isinstance(value, float):
                        annotation.double_value = value
                    elif isinstance(value, str):
                        annotation.string_value = value
                    elif isinstance(value, dict):
                        for extra_key, extra_val in value.items():
                            dict_annotation = annotation.dict_entries.add()
                            dict_annotation.name = extra_key
                            if isinstance(extra_val, int):
                                dict_annotation.int_value = extra_val
                            elif isinstance(extra_val, bool):
                                dict_annotation.bool_value = extra_val
                            elif isinstance(extra_val, float):
                                dict_annotation.double_value = extra_val
                            elif isinstance(extra_val, str):
                                dict_annotation.string_value = extra_val

            packet = builder.add_packet()
            packet.timestamp = end
            packet.track_event.type = TrackEvent.TYPE_SLICE_END
            packet.track_event.track_uuid = event_track_uuid
            packet.trusted_packet_sequence_id = TRUSTED_PACKET_SEQUENCE_ID
            if categories:
                packet.track_event.categories.extend(categories)

        # Helper to add a counter event (value sample at a timestamp)
        def add_counter_event(ts, value, counter_track_uuid):
            packet = builder.add_packet()
            packet.timestamp = ts
            packet.track_event.type = TrackEvent.TYPE_COUNTER
            packet.track_event.track_uuid = counter_track_uuid
            packet.track_event.double_counter_value = value
            packet.trusted_packet_sequence_id = TRUSTED_PACKET_SEQUENCE_ID

        def format_mode_category(modes):
            ordered_modes = []
            for mode in ("RVT", "TDI", "DES"):
                if mode in modes:
                    ordered_modes.append(mode)
            ordered_modes.extend(sorted(mode for mode in modes if mode not in ordered_modes))
            return "/".join(ordered_modes) if ordered_modes else None

        def merge_kernel_func_intervals(intervals):
            merged = []
            intervals.sort(key=lambda item: (item["start"], item["end"]))
            for interval in intervals:
                if (
                    merged
                    and merged[-1]["merge_key"] == interval["merge_key"]
                ):
                    merged[-1]["start"] = min(merged[-1]["start"], interval["start"])
                    merged[-1]["end"] = max(merged[-1]["end"], interval["end"])
                    merged[-1]["modes"].update(interval["modes"])
                    merged[-1]["batch_launches"].update(interval["batch_launches"])
                else:
                    merged.append(dict(interval))
            return merged

        def find_profile_disable_end_us(trace_events):
            ends = [
                event["ts"] + event.get("dur", 0)
                for event in trace_events
                if event.get("cat") == "tpudnn"
                and event.get("name") == "disable_profile"
                and "ts" in event
            ]
            return max(ends) if ends else None

        # Create track UUIDs for CDMA and each core's TIU,GDMA,SDMA threads
        track_uuids = [] #core_num + cdma
        engine_uuids = []
        cdma_uuids = []
        kernel_func_uuids = []
        cdma_kernel_func_uuids = []
        counter_uuids = []  # per core: (tiu_util_uuid, gdma_bw_uuid)
        for core_id in range(num_cores):
            track_uuids.append(uuid.uuid4().int & (1 << 63) - 1) # core main track
            kernel_func_uuids.append(uuid.uuid4().int & (1 << 63) - 1) # kernel function overview
            tmp =[]
            tmp.append(uuid.uuid4().int & (1 << 63) - 1) # tiu
            tmp.append(uuid.uuid4().int & (1 << 63) - 1) # gdma
            if core_has_sdma[core_id]:
                tmp.append(uuid.uuid4().int & (1 << 63) - 1) # sdma
            engine_uuids.append(tmp)
            tiu_util_uuid = uuid.uuid4().int & (1 << 63) - 1  # tiu utilization counter
            gdma_bw_uuid = uuid.uuid4().int & (1 << 63) - 1   # gdma bandwidth counter
            counter_uuids.append((tiu_util_uuid, gdma_bw_uuid))
        track_uuids.append(uuid.uuid4().int & (1 << 63) - 1) # cdma main track
        for port_id in range(len(p.cdma_events)):
            if cdma_has_port[port_id]:
                cdma_uuids.append(uuid.uuid4().int & (1 << 63) - 1) # cdma
                cdma_kernel_func_uuids.append(uuid.uuid4().int & (1 << 63) - 1) # kernel function overview
            else:
                cdma_uuids.append(-1)
                cdma_kernel_func_uuids.append(-1)

        # Add metadata events for cores
        for core_id in range(num_cores):
            define_custom_track(track_uuids[core_id], f"Core {core_id}", child_ordering_mode=TrackDescriptor.EXPLICIT)
            define_custom_track(kernel_func_uuids[core_id], "Kernel Function", parent_track_uuid=track_uuids[core_id], sibling_order_rank=5)
            define_custom_track(engine_uuids[core_id][0], "TIU", parent_track_uuid=track_uuids[core_id], sibling_order_rank=4)
            define_custom_track(counter_uuids[core_id][0], "TIU Utilization", parent_track_uuid=track_uuids[core_id], sibling_order_rank=3, is_counter=True)
            define_custom_track(engine_uuids[core_id][1], "GDMA", parent_track_uuid=track_uuids[core_id], sibling_order_rank=2)
            define_custom_track(counter_uuids[core_id][1], "GDMA Bandwidth", parent_track_uuid=track_uuids[core_id], sibling_order_rank=1, is_counter=True)
            if core_has_sdma[core_id]:
                define_custom_track(engine_uuids[core_id][2], "SDMA", parent_track_uuid=track_uuids[core_id], sibling_order_rank=0)
        if any(cdma_has_port):
            define_custom_track(track_uuids[-1], "CDMA")
        for port_id in range(len(p.cdma_events)):
            if cdma_has_port[port_id]:
                define_custom_track(cdma_uuids[port_id], f"CDMA port{port_id}", parent_track_uuid=track_uuids[-1], child_ordering_mode=TrackDescriptor.EXPLICIT)
                define_custom_track(cdma_kernel_func_uuids[port_id], f"CDMA port{port_id} Kernel Function", parent_track_uuid=track_uuids[-1], sibling_order_rank=1)

        # Merge torch ops
        pmu_estimated_start_us = 0
        pmu_tail_anchor_ns = None
        if trace_file:
            with open(trace_file) as f:
                trace = json.load(f)
            filter_list = ["PyTorch Profiler (0)", "enable_profile", "my_ops::enable_profile", "disable_profile", "my_ops::disable_profile"]
            profile_disable_end_us = find_profile_disable_end_us(trace.get("traceEvents", []))
            if (
                profile_disable_end_us is not None
                and p.tail_offset_ns is not None
                and p.pmu_tail_cycle is not None
            ):
                pmu_tail_anchor_ns = int(profile_disable_end_us * 1000 - p.tail_offset_ns)
            trace['traceEvents'] = [x for x in trace['traceEvents'] if x.get("name", None) not in filter_list]
            tpudnn_events = [e for e in trace['traceEvents'] if e.get('cat', None) == 'tpudnn']

            if len(tpudnn_events) > 0:
                pmu_estimated_start_us = tpudnn_events[0]['ts'] + tpudnn_events[0]['dur']
                python_op_track_uuid = uuid.uuid4().int & (1 << 63) - 1
                tpudnn_track_uuid = uuid.uuid4().int & (1 << 63) - 1
                define_custom_track(python_op_track_uuid, "cpu op")
                define_custom_track(tpudnn_track_uuid, "tpudnn op")
                for e in [ev for ev in trace['traceEvents'] if ev.get('ph') == 'X']:
                    add_slice_event(start=int(e['ts'] * 1000), end = int((e['ts'] + e['dur']) * 1000),
                                    event_track_uuid=tpudnn_track_uuid if e.get('cat', None) == 'tpudnn' else python_op_track_uuid,
                                    name=e['name'])

        # Convert events to Perfetto format
        if pmu_tail_anchor_ns is not None:
            pmu_relative_time = (
                pmu_tail_anchor_ns
                - get_realtime_from_cycle(p.pmu_tail_cycle - min_start_cycle, tiu_freq)
            )
        else:
            pmu_relative_time = int(pmu_estimated_start_us * 1000)
        kernel_func_intervals = {}
        cdma_kernel_func_keys = set()
        for item in chain.from_iterable(p.cdma_events):
            event, extra, meta = item
            event = deepcopy(event)
            if extra:
                event.update(extra)
            if meta:
                event.update(meta)
            kernel_func = event.get("kernel_func")
            if kernel_func:
                cdma_kernel_func_keys.add((kernel_func, event.get("batch_launch")))
        print(f"Generating perfetto pftrace...")
        for item in tqdm(chain.from_iterable(chain(p.bd_events, p.gdma_events, p.sdma_events, p.cdma_events))):
            event, extra, meta = item
            event = deepcopy(event)
            if extra:
                event.update(extra)
            if meta:
                event.update(meta)
            start_cycle = event.pop('Start Cycle')
            end_cycle = event.pop('End Cycle')
            engine_id = event.pop('Engine Id')
            core_id = event.pop('Core Id')
            duration = get_realtime_from_cycle(start_cycle - end_cycle, tiu_freq)
            if engine_id == 0:
                event_track_uuid = engine_uuids[core_id][0]
            elif engine_id == 1:
                event_track_uuid = engine_uuids[core_id][1]
            elif engine_id == 3:
                event_track_uuid = engine_uuids[core_id][2]
            elif engine_id == 4:
                event_track_uuid = cdma_uuids[event['Port']]
                kernel_func_track_uuid = cdma_kernel_func_uuids[event['Port']]
            else:
                raise ValueError(f"Unknown event type: {event['type']}")
            if engine_id != 4:
                kernel_func_track_uuid = kernel_func_uuids[core_id]

            compressed_event = self._get_necessary_event_data(event, engine_id)
            kernel_func = event.get("kernel_func")
            categories = [event['Function Type']]
            # slice_name = kernel_func if kernel_func else event.get('Function Type', event['Function Name'])
            slice_name = event.get('Function Name', event['Function Type'])
            start_ns = get_realtime_from_cycle(start_cycle - min_start_cycle, tiu_freq) + pmu_relative_time
            end_ns = get_realtime_from_cycle(end_cycle - min_start_cycle, tiu_freq) + pmu_relative_time
            kernel_func_key = (kernel_func, event.get("batch_launch"))
            show_kernel_func_overview = (
                kernel_func
                and kernel_func_track_uuid != -1
                and (engine_id == 4 or kernel_func_key not in cdma_kernel_func_keys)
            )
            if show_kernel_func_overview:
                mode = event.get("mode")
                func_scope_id = event.get("func_scope_id")
                merge_key = func_scope_id if func_scope_id is not None else kernel_func_key
                kernel_func_intervals.setdefault(kernel_func_track_uuid, []).append({
                    "start": start_ns,
                    "end": end_ns,
                    "kernel_func": kernel_func,
                    "batch_launch": event.get("batch_launch"),
                    "func_scope_id": func_scope_id,
                    "merge_key": merge_key,
                    "batch_launches": {event.get("batch_launch")} if event.get("batch_launch") is not None else set(),
                    "modes": {mode} if mode else set(),
                })
            add_slice_event(start=get_realtime_from_cycle(start_cycle - min_start_cycle, tiu_freq) + pmu_relative_time,
                            end=get_realtime_from_cycle(end_cycle - min_start_cycle, tiu_freq) + pmu_relative_time,
                            event_track_uuid=event_track_uuid,
                            name=slice_name,
                            debug_annotations=compressed_event,
                            correlation_id=None,
                            categories=categories)

            if engine_id == 0:
                uarch_rate_str = compressed_event.get("uArch Rate", "0.0%")
                try:
                    uarch_rate = float(str(uarch_rate_str).rstrip("%"))
                except ValueError:
                    uarch_rate = 0.0
                add_counter_event(start_ns, uarch_rate, counter_uuids[core_id][0])
                add_counter_event(end_ns, 0.0, counter_uuids[core_id][0])
            elif engine_id == 1:
                ddr_bw = compressed_event.get("DDR Bandwidth(GB/s)", 0)
                try:
                    ddr_bw = float(ddr_bw)
                except (ValueError, TypeError):
                    ddr_bw = 0.0
                add_counter_event(start_ns, ddr_bw, counter_uuids[core_id][1])
                add_counter_event(end_ns, 0.0, counter_uuids[core_id][1])

        for track_uuid, intervals in kernel_func_intervals.items():
            merged_intervals = merge_kernel_func_intervals(intervals)
            merged_intervals.sort(key=lambda item: (item["start"], item["end"]))

            for interval in merged_intervals:
                mode_category = format_mode_category(interval["modes"])
                debug_annotations = {"Kernel Function": interval["kernel_func"]}
                if interval.get("func_scope_id") is not None:
                    debug_annotations["Func Scope Id"] = interval["func_scope_id"]
                if interval["batch_launches"]:
                    batch_launches = sorted(interval["batch_launches"])
                    debug_annotations["Batch Launch"] = batch_launches[0] if len(batch_launches) == 1 else "/".join(map(str, batch_launches))
                if mode_category:
                    debug_annotations["Mode"] = mode_category
                add_slice_event(start=interval["start"],
                                end=interval["end"],
                                event_track_uuid=track_uuid,
                                name=interval["kernel_func"],
                                debug_annotations=debug_annotations,
                                correlation_id=None,
                                categories=[mode_category] if mode_category else None)

        out_dir_path = out_dir
        Path(out_dir_path).mkdir(parents=True, exist_ok=True)
        trace_file = os.path.join(out_dir_path, "perfetto.pftrace")
        if os.path.exists(trace_file):
            os.remove(trace_file)
        with open(trace_file, 'wb') as f:
            f.write(builder.serialize())
        print(f"the pftrace are generated successfully under: {trace_file}")
