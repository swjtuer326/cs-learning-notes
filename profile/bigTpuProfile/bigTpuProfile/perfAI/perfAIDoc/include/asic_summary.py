#!/usr/bin/env python
# -*- coding: utf-8 -*-
# ==============================================================================
#
# Copyright (C) 2022 sophon Technologies Inc.  All rights reserved.
#
# TPU-MLIR is licensed under the 2-Clause BSD License except for the
# third-party components.
#
# ==============================================================================
# @Time    : 2023/7/18 10:50
# @Author  : chongqing.zeng@bigtpu.com
# @Project : PerfAI
import pandas as pd
from numpy import transpose
from utils.utils import *
from definition.style import DetailsStyle, merge_format


class AsicSummary(object):
    sheet_name = 'Engine Summary'

    def __init__(self, writer, tius, gdmas, sdmas, cdmas, act_core_num):
        self.columns = ['CoreId', 'TiuWorkingRatio', 'Parallelism', 'Concurrency',
                        'totalTime(us)', 'totalTiuCycle', 'totalAlgCycle', 'totalAlgOps', 'totalUArchOps', 'uArchURate',
                        'totalGdmaCycle', 'totalDdrDataSize', 'totalL2DataSize', 'ddrAvgBandwidth', 'l2AvgBandwidth',
                        'totalSdmaCycle', 'totalDdrDataSize', 'ddrAvgBandwidth',
                        'totalcdmaCycle', 'totalDdrDataSize', 'totalL2DataSize', 'ddrAvgBandwidth', 'l2AvgBandwidth']
        self.writer = writer
        self.tius = tius
        self.gdmas = gdmas
        self.sdmas = sdmas
        self.cdmas = cdmas
        self.act_core_num = act_core_num
        self.sheet_color = ''
        self.data = []
        self.chip_arch = None

    def load(self, chip_arch):
        self.chip_arch = chip_arch
        core_ids, tiu_work_ratios, prallelisms, concurrencys, total_times, tiu_cycles, alg_cycles, alg_opss, uArch_opss, uArch_rates = \
            [], [], [], [], [], [], [], [], [], []
        gdma_cycles, gdma_ddr_datasizes, gdma_l2_datasizes, gdma_ddr_avg_bds, \
            gdma_l2_avg_bds = [], [], [], [], []
        sdma_cycles, sdma_ddr_datasizes, sdma_ddr_avg_bds = [], [], []
        cdma_cycles, cdma_ddr_datasizes, cdma_l2_datasizes, cdma_ddr_avg_bds, \
            cdma_l2_avg_bds = [], [], [], [], []
        gdma_ddr_cycles, gdma_l2_cycles, sdma_ddr_cycles, sdma_l2_cycles, cdma_ddr_cycles, cdma_l2_cycles = 0, 0, 0, 0, 0, 0
        tiu_frequency = int(chip_arch['TIU Frequency(MHz)'])
        dma_frequency = int(chip_arch['DMA Frequency(MHz)'])
        total_time = get_total_time(self.tius, self.gdmas, self.sdmas, self.cdmas)

        for core_id in range(0, self.act_core_num):
            core_ids.append(core_id)
            tiu_work_ratios.append(get_ratio_str_2f_zero(self.tius[core_id].tiu_time, total_time))
            if self.act_core_num > 1:
                prallelisms.append(get_ratio_str_2f_zero(self.tius[core_id].tiu_time + self.gdmas[core_id].working_cycle\
                                                    + self.sdmas[core_id].working_cycle, total_time))
            else:
                prallelisms.append(get_ratio_str_2f_zero(self.tius[core_id].tiu_time + self.gdmas[core_id].working_cycle, total_time))
            if self.tius[core_id].tiu_time > 0 and self.gdmas[core_id].working_cycle > 0:
                concurrencys.append(get_ratio_str_2f_zero(self.tius[core_id].tiu_time + self.gdmas[core_id].working_cycle - total_time,
                                                          min(self.tius[core_id].tiu_time, self.gdmas[core_id].working_cycle)))
            else:
                concurrencys.append('0.00%')
            total_times.append(total_time)
            tiu_cycles.append(self.tius[core_id].tiu_cycle)
            alg_cycles.append(self.tius[core_id].alg_total_cycle)
            alg_opss.append(self.tius[core_id].alg_total_ops)
            uArch_opss.append(self.tius[core_id].uArch_total_ops)
            uArch_rates.append(get_ratio_str_2f_zero(self.tius[core_id].alg_total_ops, self.tius[core_id].uArch_total_ops))

            gdma_cycles.append(self.gdmas[core_id].working_cycle)
            gdma_ddr_datasizes.append(self.gdmas[core_id].ddr_total_datasize)
            gdma_l2_datasizes.append(self.gdmas[core_id].l2_total_datasize)
            gdma_ddr_avg_bds.append(get_ratio_float_2f(self.gdmas[core_id].ddr_total_datasize,
                                                     get_time_by_cycle(self.gdmas[core_id].ddr_total_cycle, dma_frequency)))
            gdma_l2_avg_bds.append(get_ratio_float_2f(self.gdmas[core_id].l2_total_datasize,
                                                     get_time_by_cycle(self.gdmas[core_id].l2_total_cycle, dma_frequency)))
            gdma_ddr_cycles += self.gdmas[core_id].ddr_total_cycle
            gdma_l2_cycles += self.gdmas[core_id].l2_total_cycle

            sdma_cycles.append(self.sdmas[core_id].working_cycle)
            sdma_ddr_datasizes.append(self.sdmas[core_id].ddr_total_datasize)
            sdma_ddr_avg_bds.append(get_ratio_float_2f(self.sdmas[core_id].ddr_total_datasize,
                                                     get_time_by_cycle(self.sdmas[core_id].ddr_total_cycle, dma_frequency)))
            sdma_ddr_cycles += self.sdmas[core_id].ddr_total_cycle
            sdma_l2_cycles += self.sdmas[core_id].l2_total_cycle

            cdma_cycles.append(self.cdmas[core_id].working_cycle)
            cdma_ddr_datasizes.append(self.cdmas[core_id].ddr_total_datasize)
            cdma_l2_datasizes.append(self.cdmas[core_id].l2_total_datasize)
            cdma_ddr_avg_bds.append(get_ratio_float_2f(self.cdmas[core_id].ddr_total_datasize,
                                                     get_time_by_cycle(self.cdmas[core_id].ddr_total_cycle, dma_frequency)))
            cdma_l2_avg_bds.append(get_ratio_float_2f(self.cdmas[core_id].l2_total_datasize,
                                                     get_time_by_cycle(self.cdmas[core_id].l2_total_cycle, dma_frequency)))
            cdma_ddr_cycles += self.cdmas[core_id].ddr_total_cycle
            cdma_l2_cycles += self.cdmas[core_id].l2_total_cycle
        core_ids.append('Overall')
        tiu_work_ratios.append(get_ratio_str_2f_zero(get_time_by_cycle(max(tiu_cycles), tiu_frequency),  total_time))
        if self.act_core_num > 1:
            prallelisms.append(get_ratio_str_2f_zero(get_time_by_cycle(max(tiu_cycles), tiu_frequency) +
                                                     get_time_by_cycle(max(gdma_cycles) + max(sdma_cycles), dma_frequency), total_time))
        else:
            prallelisms.append(get_ratio_str_2f_zero(get_time_by_cycle(max(tiu_cycles), tiu_frequency) +
                                                     get_time_by_cycle(max(gdma_cycles), dma_frequency), total_time))
        tiu_max_time = get_time_by_cycle(max(tiu_cycles), tiu_frequency)
        gdma_max_time = get_time_by_cycle(max(gdma_cycles), dma_frequency)
        concurrencys.append(get_ratio_str_2f_zero(tiu_max_time + gdma_max_time - total_time, min(tiu_max_time, gdma_max_time)))
        uArch_rates.append(get_ratio_str_2f_zero(sum(alg_opss), sum(uArch_opss)))
        total_times.append(max(total_times))
        tiu_cycles.append(max(tiu_cycles))
        alg_cycles.append(max(alg_cycles))
        alg_opss.append(sum(alg_opss))
        uArch_opss.append(sum(uArch_opss))
        gdma_cycles.append(max(gdma_cycles))
        gdma_ddr_datasizes.append(sum(gdma_ddr_datasizes))
        gdma_l2_datasizes.append(sum(gdma_l2_datasizes))
        gdma_ddr_avg_bds.append(get_ratio_float_2f(gdma_ddr_datasizes[-1], get_time_by_cycle(gdma_ddr_cycles, dma_frequency)))
        gdma_l2_avg_bds.append(get_ratio_float_2f(gdma_l2_datasizes[-1], get_time_by_cycle(gdma_l2_cycles, dma_frequency)))
        sdma_cycles.append(max(sdma_cycles))
        sdma_ddr_datasizes.append(sum(sdma_ddr_datasizes))
        sdma_ddr_avg_bds.append(get_ratio_float_2f(sdma_ddr_datasizes[-1], get_time_by_cycle(sdma_ddr_cycles, dma_frequency)))
        cdma_cycles.append(max(cdma_cycles))
        cdma_ddr_datasizes.append(sum(cdma_ddr_datasizes))
        cdma_l2_datasizes.append(sum(cdma_l2_datasizes))
        cdma_ddr_avg_bds.append(get_ratio_float_2f(cdma_ddr_datasizes[-1], get_time_by_cycle(cdma_ddr_cycles, dma_frequency)))
        cdma_l2_avg_bds.append(get_ratio_float_2f(cdma_l2_datasizes[-1], get_time_by_cycle(cdma_l2_cycles, dma_frequency)))
        for idx in range(len(total_times)):
            total_times[idx] = cycle_to_us(total_times[idx], 1000)
        for idx in [len(total_times) - 1]:
            tiu_cycles[idx]  = cycle_to_us(tiu_cycles[idx], tiu_frequency, with_unit=True)
            alg_cycles[idx]  = cycle_to_us(alg_cycles[idx], tiu_frequency, with_unit=True)
            gdma_cycles[idx] = cycle_to_us(gdma_cycles[idx], dma_frequency, with_unit=True)
            sdma_cycles[idx] = cycle_to_us(sdma_cycles[idx], dma_frequency, with_unit=True)
            cdma_cycles[idx] = cycle_to_us(cdma_cycles[idx], dma_frequency, with_unit=True)
            gdma_ddr_datasizes[idx] = datasize_to_MB(gdma_ddr_datasizes[idx])
            gdma_l2_datasizes[idx] = datasize_to_MB(gdma_l2_datasizes[idx])
            sdma_ddr_datasizes[idx] = datasize_to_MB(sdma_ddr_datasizes[idx])
            cdma_ddr_datasizes[idx] = datasize_to_MB(cdma_ddr_datasizes[idx])
            cdma_l2_datasizes[idx] = datasize_to_MB(cdma_l2_datasizes[idx])
        self.data = transpose([core_ids, tiu_work_ratios, prallelisms, concurrencys, total_times, tiu_cycles, alg_cycles,
                            alg_opss, uArch_opss, uArch_rates, gdma_cycles,
                            gdma_ddr_datasizes, gdma_l2_datasizes, gdma_ddr_avg_bds,
                            gdma_l2_avg_bds, sdma_cycles, sdma_ddr_datasizes,
                            sdma_ddr_avg_bds, cdma_cycles,
                            cdma_ddr_datasizes, cdma_l2_datasizes, cdma_ddr_avg_bds,
                            cdma_l2_avg_bds]).tolist()

    def write(self, style=True):
        """
        Write summary information to Excel with styling.
        :return: None
        """
        df = pd.DataFrame(self.data, columns=self.columns, index=None)
        chip_arch = self.chip_arch

        # Write data via pandas first (for the data region starting at row 4)
        df.to_excel(self.writer, index=False, sheet_name=self.sheet_name, startrow=4, engine='xlsxwriter', float_format='%g')
        para_desc = '(totalTiuCycle + totalGdmaCycle + totalSdmaCycle) / totalTime, presents the parallelism among all engines.'\
        if self.act_core_num > 1 else '(totalTiuCycle + totalGdmaCycle) / totalTime, presents the parallelism among all engines.'
        kpi_desc = pd.DataFrame(
                {
                    'Field': [
                        'Tiu Working Ratio',
                        'Parallelism(%)',
                        'Concurrency(%)',
                        'Total Alg Cycle',
                        'Total Alg Ops',
                        'Total uArch Ops',
                        'uArch Urate'],
                    'Description': [
                        'totalTiuCycle / totalTime, indicates the percentage of time tiu execution takes.',
                        para_desc,
                        '(totalTiuCycle + totalGdmaCycle - totalTime) / min(totalTiuCycle, totalGdmaCycle), indicates the concurrency between tiu and gdma.',
                        'The time required to execute tiu instructions theoretically.',
                        'The theoretical OPs required to execute tiu instructions.',
                        'The actual number of OPs accounting for microarchitecture.',
                        'totalAlgOps / totalUArchOps, since the shape of tensor needs to be aligned in '
                        'micro-architecture, the actual ops will be greater than the algorithm value. '
                        "It's better to closer to 100%."
                        ]
                },
                index=None
            )
        kpi_desc.to_excel(self.writer, index=False, sheet_name=self.sheet_name, startrow=9+self.act_core_num, startcol=0,
                                        engine='xlsxwriter', float_format='%g')

        # Now apply styling via xlsxwriter
        if not style or not chip_arch:
            return

        workbook = self.writer.book
        worksheet = self.writer.sheets[self.sheet_name]
        D = DetailsStyle

        fmt_title = merge_format(workbook, D.title_pattern, D.title_font)
        fmt_header = merge_format(workbook, D.content_pattern, D.title_header_font)
        fmt_content_font = merge_format(workbook, D.title_font)
        fmt_border_center = merge_format(workbook, D.border, D.center_align)
        fmt_yellow = merge_format(workbook, D.yellow_light, D.title_font, D.center_align)
        fmt_red = merge_format(workbook, D.red_light, D.title_font, D.center_align)
        fmt_tiu = merge_format(workbook, D.tiu_pattern)
        fmt_gdma = merge_format(workbook, D.gdma_pattern)
        fmt_sdma = merge_format(workbook, D.sdma_pattern)
        fmt_cdma = merge_format(workbook, D.cdma_pattern)

        # Platform label
        worksheet.write(0, 0, 'Platform: ' + chip_arch['Chip Arch'], fmt_title)
        worksheet.merge_range(0, 0, 0, 1, 'Platform: ' + chip_arch['Chip Arch'], fmt_title)

        # Engine group headers (row 3, 0-based)
        fmt_tiu_title = merge_format(workbook, D.tiu_pattern, D.title_font)
        fmt_gdma_title = merge_format(workbook, D.gdma_pattern, D.title_font)
        fmt_sdma_title = merge_format(workbook, D.sdma_pattern, D.title_font)
        fmt_cdma_title = merge_format(workbook, D.cdma_pattern, D.title_font)
        worksheet.write(3, 7, 'TIU', fmt_tiu_title)
        worksheet.write(3, 13, 'GDMA', fmt_gdma_title)
        worksheet.write(3, 18, 'SDMA', fmt_sdma_title)
        worksheet.write(3, 23, 'CDMA', fmt_cdma_title)
        # Color bands for engine groups
        for w in range(5, 11):
            worksheet.write(3, w, '', fmt_tiu)
        for w in range(10, 17):
            worksheet.write(3, w, '', fmt_gdma)
        for w in range(16, 21):
            worksheet.write(3, w, '', fmt_sdma)
        for w in range(20, 26):
            worksheet.write(3, w, '', fmt_cdma)

        # Column widths
        for ci, col in enumerate(self.columns):
            max_len = len(str(col))
            for row in self.data:
                if ci < len(row) and row[ci] is not None:
                    max_len = max(max_len, len(str(row[ci])))
            worksheet.set_column(ci, ci, max_len * 1.05)

        worksheet.set_tab_color('#FFC7CE')

    @classmethod
    def set_style(cls, file_path, chip_arch):
        """
        No-op: styling is now applied during write() via xlsxwriter.
        """
        pass
