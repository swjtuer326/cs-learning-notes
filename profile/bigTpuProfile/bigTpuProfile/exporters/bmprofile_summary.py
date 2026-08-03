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
from pathlib import Path
from tqdm import tqdm
from copy import deepcopy
from decimal import Decimal

def get_realtime_from_cycle(cycle, frequency):
    return int(round(cycle / frequency * 1000, 2))

class SummaryExporter:
    def __init__(self, parser):
        self.parser = parser

    def gen_summary(self, out_dir=None, tiu_freq=1000):
        """Generate per-core summary data directly from PMU data, bypassing txt files.

        Computes Engine Summary metrics similar to AsicSummary, but directly from
        the parsed PMU events (bd_events, gdma_events, sdma_events, cdma_events) without
        going through the tiuRegInfo/tdmaRegInfo/cdmaRegInfo txt file roundtrip.

        Args:
            tiu_freq: TIU frequency in MHz
            out_dir: Output directory for txt file. If provided, writes summary.txt
        """
        p = self.parser
        tiu_frequency = int(p.archlib.TIU_ARCH["TIU Frequency(MHz)"])
        dma_frequency = int(p.archlib.DMA_ARCH["DMA Frequency(MHz)"])
        num_cores= p.num_cores
        if num_cores == 0:
            print("No core data available for summary.")
            return

        bd_sys_code = p.archlib.bd_sys_code
        dma_sys_code = p.archlib.dma_sys_code
        cdma_sys_code = p.archlib.cdma_sys_code

        per_core = []
        global_start_cycle = float('inf')
        global_end_cycle = 0

        print("Generating summary...")
        for core_id in tqdm(range(num_cores)):
            metrics = {}

            # === TIU ===
            tiu_cycle = 0
            alg_total_ops = 0
            uArch_total_ops = 0
            alg_total_cycle = 0

            if core_id < len(p.bd_events):
                for event in p.bd_events[core_id]:
                    info, extra, _ = event
                    merged = deepcopy(info)
                    if extra:
                        merged.update(extra)
                    asic_cycle = merged.get("Asic Cycle", 0)
                    des_tsk_typ = merged.get("des_tsk_typ", -1)
                    # des_tsk_eu_typ = merged.get("des_tsk_eu_typ", -1)
                    is_wait_msg = (des_tsk_typ == bd_sys_code)
                    if not is_wait_msg:
                        tiu_cycle += asic_cycle
                    alg_total_ops += merged.get("Alg Ops", 1)
                    uArch_total_ops += merged.get("uArch Ops", 1)
                    alg_total_cycle += merged.get("Alg Cycle", 0)
                    global_start_cycle = min(global_start_cycle, merged['Start Cycle'])
                    global_end_cycle = max(global_end_cycle, merged['End Cycle'])

            tiu_time = get_realtime_from_cycle(tiu_cycle, tiu_frequency)
            metrics['tiu_cycle'] = tiu_cycle
            metrics['tiu_time'] = tiu_time
            metrics['alg_total_ops'] = alg_total_ops
            metrics['uArch_total_ops'] = uArch_total_ops
            metrics['alg_total_cycle'] = alg_total_cycle

            # === GDMA ===
            gdma_cycle = 0
            gdma_wait_msg_time = 0
            gdma_ddr_datasize = 0
            gdma_l2_datasize = 0
            gdma_ddr_cycle = 0
            gdma_l2_cycle = 0

            if core_id < len(p.gdma_events):
                for event in p.gdma_events[core_id]:
                    info, extra, _ = event
                    merged = deepcopy(info)
                    if extra:
                        merged.update(extra)
                    asic_cycle = merged.get("Asic Cycle", 0)
                    gdma_cycle += asic_cycle
                    cmd_type = merged.get("cmd_type", merged.get("des_tsk_typ", -1))
                    cmd_special = merged.get("cmd_special_function", merged.get("des_tsk_eu_typ", -1))
                    is_wait = (cmd_type == dma_sys_code)
                    if is_wait:
                        gdma_wait_msg_time += asic_cycle
                    else:
                        direction = str(merged.get("Direction", ""))
                        datasize = merged.get("DMA data size(B)", 0)
                        if "DDR" in direction and datasize:
                            gdma_ddr_datasize += datasize
                            gdma_ddr_cycle += asic_cycle
                        elif "L2" in direction and datasize:
                            gdma_l2_datasize += datasize
                            gdma_l2_cycle += asic_cycle
                    global_start_cycle = min(global_start_cycle, merged['Start Cycle'])
                    global_end_cycle = max(global_end_cycle, merged['End Cycle'])

            gdma_working_cycle = gdma_cycle - gdma_wait_msg_time
            gdma_working_time = get_realtime_from_cycle(gdma_working_cycle, dma_frequency)
            metrics['gdma_cycle'] = gdma_cycle
            metrics['gdma_working_cycle'] = gdma_working_cycle
            metrics['gdma_working_time'] = gdma_working_time
            metrics['gdma_ddr_datasize'] = gdma_ddr_datasize
            metrics['gdma_l2_datasize'] = gdma_l2_datasize
            metrics['gdma_ddr_cycle'] = gdma_ddr_cycle
            metrics['gdma_l2_cycle'] = gdma_l2_cycle

            # === SDMA ===
            sdma_cycle = 0
            sdma_wait_msg_time = 0
            sdma_ddr_datasize = 0
            sdma_ddr_cycle = 0

            if core_id < len(p.sdma_events):
                for event in p.sdma_events[core_id]:
                    info, extra, _ = event
                    merged = deepcopy(info)
                    if extra:
                        merged.update(extra)
                    asic_cycle = merged.get("Asic Cycle", 0)
                    sdma_cycle += asic_cycle
                    cmd_type = merged.get("cmd_type", merged.get("des_tsk_typ", -1))
                    cmd_special = merged.get("cmd_special_function", merged.get("des_tsk_eu_typ", -1))
                    is_wait = (cmd_type == dma_sys_code and cmd_special == 4)
                    if is_wait:
                        sdma_wait_msg_time += asic_cycle
                    else:
                        direction = str(merged.get("Direction", ""))
                        datasize = merged.get("DMA data size(B)", 0)
                        if "DDR" in direction:
                            sdma_ddr_datasize += datasize
                            sdma_ddr_cycle += asic_cycle
                    global_start_cycle = min(global_start_cycle, merged['Start Cycle'])
                    global_end_cycle = max(global_end_cycle, merged['End Cycle'])

            sdma_working_cycle = sdma_cycle - sdma_wait_msg_time
            sdma_working_time = get_realtime_from_cycle(sdma_working_cycle, dma_frequency)
            metrics['sdma_cycle'] = sdma_cycle
            metrics['sdma_working_cycle'] = sdma_working_cycle
            metrics['sdma_working_time'] = sdma_working_time
            metrics['sdma_ddr_datasize'] = sdma_ddr_datasize
            metrics['sdma_ddr_cycle'] = sdma_ddr_cycle

            per_core.append(metrics)

        # === CDMA === (aggregate across all ports, shared across cores)
        cdma_cycle = 0
        cdma_wait_msg_time = 0
        cdma_ddr_datasize = 0
        cdma_l2_datasize = 0
        cdma_ddr_cycle = 0
        cdma_l2_cycle = 0

        for port_idx, cdma_events in enumerate(p.cdma_events):
            for event in cdma_events:
                info, extra, _ = event
                merged = deepcopy(info)
                if extra:
                    merged.update(extra)
                asic_cycle = merged.get("Asic Cycle", 0)
                cdma_cycle += asic_cycle
                cmd_type = merged.get("cmd_type", merged.get("des_tsk_typ", -1))
                cmd_special = merged.get("cmd_special_function", merged.get("des_tsk_eu_typ", -1))
                is_wait = (cmd_type == cdma_sys_code)
                if is_wait:
                    cdma_wait_msg_time += asic_cycle
                else:
                    direction = str(merged.get("Direction", ""))
                    datasize = merged.get("DMA data size(B)", 0)
                    if "DDR" in direction:
                        cdma_ddr_datasize += datasize
                        cdma_ddr_cycle += asic_cycle
                    if "L2" in direction:
                        cdma_l2_datasize += datasize
                        cdma_l2_cycle += asic_cycle
                global_start_cycle = min(global_start_cycle, merged['Start Cycle'])
                global_end_cycle = max(global_end_cycle, merged['End Cycle'])

        cdma_working_cycle = cdma_cycle - cdma_wait_msg_time
        cdma_working_time = get_realtime_from_cycle(cdma_working_cycle, dma_frequency)

        if global_start_cycle == float('inf'):
            print("No timing data available for summary.")
            return

        total_time = get_realtime_from_cycle(global_end_cycle - global_start_cycle, tiu_frequency)

        # === Output Summary ===
        def fmt_pct(x, y):
            return '{:.2f}%'.format(x / y * 100) if y > 0 else '0.00%'

        def fmt_bd(datasize, cycle, freq):
            time_ns = get_realtime_from_cycle(cycle, freq)
            return '{:.2f}'.format(datasize / time_ns) if time_ns > 0 else '0.00'

        def fmt_datasize(b):
            return '{:.2f}MiB'.format(b / (1 << 20)) if b > 0 else '0.00MiB'

        def cycle_to_us(cycles, frequency, with_unit=False):
            return str((Decimal(cycles / frequency)).quantize(Decimal("0.000"))) \
                + ('(us)' if with_unit else '')

        def datasize_to_MB(datasize):
            return str((Decimal(datasize / math.pow(2, 20))).quantize(Decimal("0.00"))) + 'MiB'

        header = f"{'CoreId':>7} | {'Parallelism(%)':>14} | {'totalTime(us)':>14} | {'TiuWorkingRatio':>15} | {'totalTiuCycle':>14} | " \
              f"{'uArchURate':>10} | {'totalGdmaCycle':>16} | {'GdmaDdrAvgBW(GB/s)':>18} | {'GdmaL2AvgBW(GB/s)':>18} | {'totalSdmaCycle':>16} | {'SdmaDdrAvgBW(GB/s)':>18}"
        header_width = len(header)
        terminal_width = len("|".join(header.split("|")[:-3]))
        lines = [
            "=" * header_width,
            "Summary",
            "=" * header_width,
            header,
            "-" * header_width
        ]

        for core_id, m in enumerate(per_core):
            if num_cores > 1:
                parallelism = fmt_pct(m['tiu_time'] + m['gdma_working_time'] + m['sdma_working_time'], total_time)
            else:
                parallelism = fmt_pct(m['tiu_time'] + m['gdma_working_time'], total_time)
            uarch_rate = fmt_pct(m['alg_total_ops'], m['uArch_total_ops'])

            row = f"{str(core_id):>7} | {parallelism:>14} | {total_time / 1000:>14.3f} | " \
                  f"{fmt_pct(m['tiu_time'], total_time):>15} | {m['tiu_cycle']:>14} | " \
                  f"{uarch_rate:>10} | {m['gdma_working_cycle']:>16} | " \
                  f"{fmt_bd(m['gdma_ddr_datasize'], m['gdma_ddr_cycle'], dma_frequency):>18} | " \
                  f"{fmt_bd(m['gdma_l2_datasize'], m['gdma_l2_cycle'], dma_frequency):>18} | " \
                  f"{m['sdma_working_cycle']:>16} | " \
                  f"{fmt_bd(m['sdma_ddr_datasize'], m['sdma_ddr_cycle'], dma_frequency):>18}"
            lines.append(row)

        # Overall row
        overall_tiu_cycle = max(m['tiu_cycle'] for m in per_core)
        overall_tiu_time = get_realtime_from_cycle(overall_tiu_cycle, tiu_frequency)
        overall_gdma_cycle = max(m['gdma_working_cycle'] for m in per_core)
        overall_gdma_time = get_realtime_from_cycle(overall_gdma_cycle, dma_frequency)
        overall_sdma_cycle = max(m['sdma_working_cycle'] for m in per_core)
        overall_sdma_time = get_realtime_from_cycle(overall_sdma_cycle, dma_frequency)
        overall_alg_ops = sum(m['alg_total_ops'] for m in per_core)
        overall_uarch_ops = sum(m['uArch_total_ops'] for m in per_core)
        overall_alg_cycle = max(m['alg_total_cycle'] for m in per_core)

        overall_tiu_time_sum = sum(m['tiu_time'] for m in per_core)
        overall_gdma_time_sum = sum(m['gdma_working_time'] for m in per_core)
        overall_sdma_time_sum = sum(m['sdma_working_time'] for m in per_core)
        overall_available_time = total_time * num_cores

        if num_cores > 1:
            overall_parallelism = fmt_pct(overall_tiu_time_sum + overall_gdma_time_sum + overall_sdma_time_sum, overall_available_time)
        else:
            overall_parallelism = fmt_pct(overall_tiu_time_sum + overall_gdma_time_sum, overall_available_time)
        overall_tiu_working_ratio = fmt_pct(overall_tiu_time_sum, overall_available_time)
        overall_uarch_rate = fmt_pct(overall_alg_ops, overall_uarch_ops)

        overall_gdma_ddr = sum(m['gdma_ddr_datasize'] for m in per_core)
        overall_gdma_l2 = sum(m['gdma_l2_datasize'] for m in per_core)
        overall_gdma_ddr_cycle = sum(m['gdma_ddr_cycle'] for m in per_core)
        overall_gdma_l2_cycle = sum(m['gdma_l2_cycle'] for m in per_core)
        overall_sdma_ddr = sum(m['sdma_ddr_datasize'] for m in per_core)
        overall_sdma_ddr_cycle = sum(m['sdma_ddr_cycle'] for m in per_core)

        ov_row = f"{'Overall':>7} | {overall_parallelism:>14} | {total_time / 1000:>14.3f} | " \
                 f"{overall_tiu_working_ratio:>15} | {cycle_to_us(overall_tiu_cycle, tiu_frequency, True):>14} | " \
                 f"{overall_uarch_rate:>10} | {cycle_to_us(overall_gdma_cycle, dma_frequency, True):>16} | " \
                 f"{fmt_bd(overall_gdma_ddr, overall_gdma_ddr_cycle, dma_frequency):>18} | " \
                 f"{fmt_bd(overall_gdma_l2, overall_gdma_l2_cycle, dma_frequency):>18} | " \
                 f"{cycle_to_us(overall_sdma_cycle, dma_frequency, True):>16} | " \
                 f"{fmt_bd(overall_sdma_ddr, overall_sdma_ddr_cycle, dma_frequency):>18}"
        lines.append("-" * header_width)
        lines.append(ov_row)
        lines.append("=" * header_width)

        for line in lines:
            if line.count("|") == 10:
                line = "|".join(line.split("|")[:-3])
            else:
                line = line[:terminal_width]
            print(line)

        if out_dir is not None:
            from pathlib import Path
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            summary_file = os.path.join(out_dir, "summary.txt")
            if os.path.exists(summary_file):
                os.remove(summary_file)
            with open(summary_file, 'w') as f:
                f.write('\n'.join(lines) + '\n')