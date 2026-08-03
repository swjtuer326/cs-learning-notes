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
# @Time    : 2023/7/18 10:46
# @Author  : chongqing.zeng@bigtpu.com
# @Project : PerfAI
import os

import pandas as pd

from definition.bm1684x_defs import dma_func_type_dict, data_type_dict
from definition.style import DetailsStyle, merge_format
from utils.utils import *
from shared_parser import (parse_records, apply_layer_map_simple,
                           apply_default_layer_map, filter_by_engine_id,
                           convert_dma_value, convert_cdma_value)


class DmaNode:
    def __init__(self, reg):
        self.datasize = int(reg['DMA data size(B)'])
        self.cycle = int(reg['Asic Cycle'])
        self.direction = reg['Direction']


class Dma(object):
    def __init__(self, core_id, writer):
        """
        Initial DMA object, equals to a DMA sheet in Excel.
        :param core_id: the id of current core
        :param writer: the writer of Excel to write
        """
        self.columns = ['Engine Id', 'Core Id', 'Global Idx', 'Cmd Id', 'Layer Id', 'Layer Name',
                        'Function Type', 'Function Name', 'DMA data size(B)', 'Start Cycle', 'End Cycle',
                        'Asic Cycle', 'Stall Cycle', 'DDR Bandwidth(GB/s)', 'L2M Bandwidth(GB/s)', 'Direction', 'Data Type',
                        'cmd_id_dep', 'cmd_special_function', 'src_start_addr', 'dst_start_addr',
                        'src_nsize', 'src_csize', 'src_hsize', 'src_wsize',
                        'dst_nsize', 'dst_csize', 'dst_hsize', 'dst_wsize',
                        'src_nstride', 'src_cstride', 'src_hstride', 'src_wstride',
                        'dst_nstride', 'dst_cstride', 'dst_hstride', 'dst_wstride',
                        'nchw_copy', 'stride_enable', 'src_data_format', 'cmd_type',
                        'index_csize', 'index_hsize', 'index_cstride', 'index_hstride',
                        'mask_start_addr_h8', 'mask_start_addr_l32', 'mask_data_format', 'localmem_mask_h32',
                        'localmem_mask_l32',
                        'fill_constant_en', 'constant_value', 'index', 'cmd_short', 'intr_en']
        self.reg_list = []
        self.core_id = str(core_id)
        self.height = None
        self.width = len(self.columns)
        self.dma_cycle = 0
        self.working_cycle = 0
        self.stall_cycle = 0
        self.stall_cycle_ratio = 0
        self.ddr_total_datasize = 0
        self.ddr_total_cycle = 0
        self.l2_total_datasize = 0
        self.l2_total_cycle = 0
        self.l2_avg_bandwidth = 0
        self.ddr_avg_bandwidth = 0
        self.wait_msg_total_time = 0
        self.perf_dict = {}
        self.chip_arch_dict = None
        self.sheet_name = None
        self.sheet_color = None
        self.writer = writer
        self.start_time = sys.maxsize
        self.end_time = 0
        self.dma_time = 0

    def load(self, reg_info_file, dma_layer_map):
        """
        Load data from external file.
        :param reg_info_file: file records register information, usually obtained by TPUPerf
        :return: None
        """
        if os.path.exists(reg_info_file) and os.path.getsize(reg_info_file) != 0:
            last_underscore_index = reg_info_file.rfind('_')
            core_id = int(reg_info_file[last_underscore_index + 1 : -4])

            self.chip_arch_dict, self.reg_list = parse_records(
                reg_info_file, '__TDMA_REG_INFO__', self.columns,
                skip_lines=0, has_func_type=False,
                value_converter=convert_dma_value
            )
            apply_layer_map_simple(self.reg_list, dma_layer_map, core_id)
        self.height = len(self.reg_list)

    def add_kpi_field(self, is_cdma=False):
        """
        Add some indicators which are convenient for performance analysis artificially.
        :return: None
        """
        for i in range(len(self.reg_list)):
            reg_dict = self.reg_list[i]
            name_key = (int(reg_dict['cmd_type']))
            sys_cmd_id = 7 if is_cdma else 6
            sys_wait_id = [4, 6] if is_cdma else [4]
            transfer_bytes = 0
            if reg_dict['cmd_type'] == sys_cmd_id:
                reg_dict['Data Type'] = 'None'
                # dma_sys do not transfer data
                reg_dict['Direction'] = '-'
                if reg_dict['cmd_special_function'] in sys_wait_id:
                    self.wait_msg_total_time += reg_dict['Asic Cycle']
            if isinstance(reg_dict['DMA data size(B)'], int) and reg_dict['DMA data size(B)'] > 0:
                transfer_bytes = reg_dict['DMA data size(B)']
            self.dma_cycle += int(reg_dict['Asic Cycle'])
            self.stall_cycle += int(reg_dict['Stall Cycle'])
            if 'DDR' in reg_dict['Direction'] and isinstance(reg_dict['DMA data size(B)'], int):
                if not is_cdma or transfer_bytes:
                    self.ddr_total_datasize += reg_dict['DMA data size(B)']
                    self.ddr_total_cycle += reg_dict['Asic Cycle']
            elif 'L2' in reg_dict['Direction'] and isinstance(reg_dict['DMA data size(B)'], int):
                if not is_cdma or transfer_bytes:
                    self.l2_total_datasize += reg_dict['DMA data size(B)']
                    self.l2_total_cycle += reg_dict['Asic Cycle']
            self.start_time = min(self.start_time, get_time_by_cycle(reg_dict['Start Cycle'], self.chip_arch_dict['DMA Frequency(MHz)']))
            self.end_time = max(self.start_time, get_time_by_cycle(reg_dict['End Cycle'], self.chip_arch_dict['DMA Frequency(MHz)']))
        self.dma_time = get_time_by_cycle(self.dma_cycle, self.chip_arch_dict['DMA Frequency(MHz)']) if self.chip_arch_dict else 0
        self.working_cycle = self.dma_cycle - self.wait_msg_total_time
        self.ddr_avg_bandwidth = get_ratio_float_2f(self.ddr_total_datasize,
                                                    get_time_by_cycle(self.ddr_total_cycle, self.chip_arch_dict['DMA Frequency(MHz)'])) \
                                                    if self.chip_arch_dict else 0
        self.l2_avg_bandwidth = get_ratio_float_2f(self.l2_total_datasize,
                                                    get_time_by_cycle(self.l2_total_cycle, self.chip_arch_dict['DMA Frequency(MHz)'])) \
                                                    if self.chip_arch_dict else 0
        self.perf_dict = {
            'totalDmaCycle': [self.working_cycle],
            'workingCycle': [self.working_cycle],
            'totalStallCycle': [self.stall_cycle],
            'stallCycleRatio': [get_ratio_str_2f_zero(self.stall_cycle, self.dma_cycle)],
            'totalDdrDataSize(B)': [self.ddr_total_datasize],
            'totalL2DataSize(B)': [self.l2_total_datasize],
            'ddrAvgBandwidth(GB/s)': [self.ddr_avg_bandwidth],
            'l2AvgBandwidth(GB/s)': [self.l2_avg_bandwidth],
            'waitMsgTotalTime': [self.wait_msg_total_time]
        }

    def pop_data(self):
        gdma_instance_map = dict()
        for reg in self.reg_list:
            gdma_instance_map[int(reg['Cmd Id'])] = DmaNode(reg)
        return gdma_instance_map

    def write(self, style=True, chip_arch=None, frozen=True):
        """
        Write register information and kpi field to Excel with styling.
        :return: None
        """
        if len(self.reg_list) <= 0:
            return
        # Determine present columns
        present_keys = set(self.reg_list[0].keys())
        self.columns = [col for col in self.columns if col in present_keys]
        hex_cols = frozenset(col for col in self.columns if 'addr' in col or 'mask' in col)
        is_simulator = self.chip_arch_dict['Platform'].lower() == 'simulator'

        # Create sheet via empty DataFrame then get xlsxwriter objects
        pd.DataFrame().to_excel(self.writer, index=False, sheet_name=self.sheet_name)
        workbook = self.writer.book
        worksheet = self.writer.sheets[self.sheet_name]

        # ---- Create format objects ----
        D = DetailsStyle
        fmt_title = merge_format(workbook, D.title_pattern, D.title_font)
        fmt_header = merge_format(workbook, D.title_header_pattern, D.title_header_font)
        fmt_header_border = merge_format(workbook, D.title_header_pattern, D.title_header_font, D.border, D.center_align)
        fmt_content_title = merge_format(workbook, D.title_content_pattern, D.title_font, D.border, D.center_align)
        fmt_content_header = merge_format(workbook, D.content_pattern, D.title_header_font)
        fmt_key = merge_format(workbook, D.key_content_pattern, D.border, D.right_align, D.title_font)
        fmt_normal_font = merge_format(workbook, D.title_font)

        # ---- Section 1: Labels ----
        worksheet.write(0, 0, 'Performance', fmt_title)
        worksheet.write(1, 0, 'Summary', fmt_title)

        # ---- Section 2: Summary (row 0-1, cols 2-11) ----
        perf_keys = list(self.perf_dict.keys())
        worksheet.write_row(0, 2, perf_keys, fmt_header_border)
        worksheet.write_row(1, 2, [self.perf_dict[key][0] for key in perf_keys], fmt_content_title)

        # ---- Section 3: Content header row (row 5) ----
        col_headers = list(self.columns)
        if is_simulator:
            col_headers = ['Simulator Cycle' if c == 'Asic Cycle' else c for c in col_headers]
        worksheet.write_row(5, 0, col_headers, fmt_content_header)

        # ---- Section 4: Content data ----
        offset = 1  # global_id offset
        content_end_cols = min(19 + offset, len(self.columns))

        row_offset = 6
        for row_idx, reg_dict in enumerate(self.reg_list):
            row_num = row_offset + row_idx
            key_row = []
            for col_name in self.columns[:content_end_cols]:
                val = reg_dict.get(col_name, None)
                if col_name in hex_cols:
                    val = hex(int(val)) if isinstance(val, int) else ''
                key_row.append(val)
            worksheet.write_row(row_num, 0, key_row, fmt_key)

            for ci, col_name in enumerate(self.columns[content_end_cols:], start=content_end_cols):
                val = reg_dict.get(col_name, None)
                if col_name in hex_cols:
                    val = hex(int(val)) if isinstance(val, int) else ''
                if val in (None, ''):
                    continue
                worksheet.write(row_num, ci, val, fmt_normal_font)

        worksheet.write(4, 0, '*Stall Cycle indicates the waiting time when TIU and DMA attempting to access a bank simultaneously.')

        # ---- Section 6: Column widths ----
        for ci, col_name in enumerate(col_headers):
            max_len = len(str(col_name))
            if ci < content_end_cols + 2:
                for row_idx in range(min(20, len(self.reg_list))):
                    val = self.reg_list[row_idx].get(self.columns[ci] if ci < len(self.columns) else '', '')
                    if val is not None and len(str(val)) <= 35:
                        max_len = max(max_len, len(str(val)))
            worksheet.set_column(ci, ci, max_len * 1.05)

        # ---- Section 7: Tab color & freeze panes ----
        if self.sheet_color:
            worksheet.set_tab_color('#' + self.sheet_color if not self.sheet_color.startswith('#') else self.sheet_color)
        if frozen:
            worksheet.freeze_panes(6, 0)

    @classmethod
    def set_style(cls, file_path, core_id, engine_type, sheet_color, chip_arch, frozen=True):
        """
        No-op: styling is now applied during write() via xlsxwriter.
        """
        pass


class Gdma(Dma):
    def __init__(self, core_id, writer, sheet_name):
        """
        Inherited from the dma class, initialize a gdma object.
        :param core_id: the id of current core
        :param writer: the writer of Excel to write
        :param sheet_name: the name of gdma sheet
        """
        super().__init__(core_id, writer)
        self.sheet_name = sheet_name + '_' + str(core_id)
        self.sheet_color = 'FFA500'

    def load(self, reg_info_file, gdma_layer_map):
        """
        Load gdma data from external file.
        :param gdma_layer_map:
        :param reg_info_file: file records DMA register information, usually obtained by TPUPerf
        :return: None
        """
        super().load(reg_info_file, gdma_layer_map)
        self.reg_list = filter_by_engine_id(self.reg_list, 1)
        return self.chip_arch_dict

    @classmethod
    def set_style(cls, file_path, core_id, engine_type='GDMA', sheet_color='FFA500', chip_arch = None, frozen=True):
        """
        No-op: styling is now applied during write() via xlsxwriter.
        """
        pass


class Sdma(Dma):
    def __init__(self, core_id, writer, sheet_name):
        """
        Inherited from the dma class, initialize a sdma object.
        :param core_id: the id of current core
        :param writer: the writer of Excel to write
        :param sheet_name: the name of sdma sheet
        """
        super().__init__(core_id, writer)
        self.sheet_name = sheet_name + '_' + str(core_id)
        self.sheet_color = 'D0CECE'

    def load(self, reg_info_file, sdma_layer_map):
        """
        Load data from external file.
        :param sdma_layer_map:
        :param reg_info_file: file records register information, usually obtained by TPUPerf
        :return: None
        """
        super().load(reg_info_file, sdma_layer_map)
        self.reg_list = filter_by_engine_id(self.reg_list, 3)
        return self.chip_arch_dict

    @classmethod
    def set_style(cls, file_path, core_id, engine_type='SDMA', sheet_color='D0CECE', chip_arch=None, frozen=True):
        """
        No-op: styling is now applied during write() via xlsxwriter.
        """
        pass


class Cdma(Dma):
    def __init__(self, core_id, writer, sheet_name):
        """
        Inherited from the dma class, initialize a cdma object.
        :param core_id: the id of current core
        :param writer: the writer of Excel to write
        :param sheet_name: the name of cdma sheet
        """
        super().__init__(core_id, writer)
        self.sheet_name = sheet_name + '_' + str(core_id)
        self.sheet_color = 'C0504D'

    def load(self, reg_info_file):
        """
        Load cdma data from external file.
        :param reg_info_file: file records DMA register information, usually obtained by TPUPerf
        :return: None
        """
        self.chip_arch_dict, self.reg_list = parse_records(
            reg_info_file, '__CDMA_REG_INFO__', self.columns,
            skip_lines=0, has_func_type=False,
            value_converter=convert_cdma_value
        )
        apply_default_layer_map(self.reg_list)
        self.height = len(self.reg_list)
        return self.chip_arch_dict

    @classmethod
    def set_style(cls, file_path, core_id, engine_type='CDMA', sheet_color='C0504D', chip_arch=None, frozen=True):
        """
        No-op: styling is now applied during write() via xlsxwriter.
        """
        pass
