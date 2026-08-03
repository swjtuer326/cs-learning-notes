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
# @Time    : 2023/7/18 10:39
# @Author  : chongqing.zeng@bigtpu.com
# @Project : PerfAI
import pandas as pd

from definition.bm1684x_defs import data_type_dict, tiu_func_name_dict
from utils.utils import *
from definition.style import DetailsStyle, merge_format
from shared_parser import parse_records, apply_layer_map_simple, convert_tiu_value


class TiuNode:
    def __init__(self, reg):
        self.alg_ops = int(reg['Alg Ops'])
        self.uarch_ops = int(reg['uArch Ops'])
        self.alg_cycle = int(reg['Alg Cycle'])
        self.cycle = int(reg['Asic Cycle'])
        self.des_tsk_typ = int(reg['des_tsk_typ'])
        self.des_opd1_h = reg['des_opd1_h'] if 'des_opd1_h' in reg.keys() else None
        self.des_opd1_w = reg['des_opd1_w'] if 'des_opd1_w' in reg.keys() else None
        self.des_opd1_h_str = reg['des_opd1_h_str'] if 'des_opd1_h_str' in reg.keys() else None
        self.des_opd1_w_str = reg['des_opd1_w_str'] if 'des_opd1_w_str' in reg.keys() else None


class Tiu(object):

    def __init__(self, core_id, writer):
        """
        Initial Tiu object, equals to a tiu sheet in Excel.
        :param core_id: the id of current core
        :param writer: the writer of Excel to write
        """
        self.writer = writer
        self.columns = ['Engine Id', 'Core Id', 'Global Idx', 'Cmd Id', 'Layer Id', 'Layer Name', 'Function Type', 'Function Name',
                        'Alg Cycle', 'Asic Cycle', 'Start Cycle', 'End Cycle', 'Avg Cycle Last 200', 'Alg Ops',
                        'uArch Ops', 'uArch Rate', 'Data Type', 'des_cmd_id_dep',
                        'des_res0_n', 'des_res0_c', 'des_res0_h', 'des_res0_w',
                        'des_res0_n_str', 'des_res0_c_str', 'des_res0_h_str', 'des_res0_w_str',
                        'des_opd0_n', 'des_opd0_c', 'des_opd0_h', 'des_opd0_w',
                        'des_opd0_n_str', 'des_opd0_c_str', 'des_opd0_h_str', 'des_opd0_w_str',
                        'des_opd1_n', 'des_opd1_c', 'des_opd1_h', 'des_opd1_w', 'des_opd1_n_str', 'des_opd1_c_str', 'des_opd1_h_str',
                        'des_opd1_w_str',
                        'des_opd2_n_str', 'des_res0_addr', 'des_res1_addr', 'des_opd0_addr', 'des_opd1_addr',
                        'des_opd2_addr',
                        'des_opd3_addr', 'des_tsk_typ', 'des_tsk_eu_typ', 'des_cmd_short',
                        'des_opt_res0_prec', 'des_opt_opd0_prec', 'des_opt_opd1_prec', 'des_opt_opd2_prec',
                        'des_short_opd0_str',
                        'des_opt_opd0_const', 'des_opt_opd1_const', 'des_opt_opd2_const', 'des_opt_opd3_const',
                        'des_opt_opd4_const', 'des_opt_opd5_const',
                        'des_opt_res_add', 'des_opt_res0_sign', 'des_opt_opd0_sign', 'des_opt_opd1_sign',
                        'des_opt_opd2_sign',
                        'des_opd0_rt_pad', 'des_opd1_x_ins0', 'des_opd0_up_pad', 'des_opd0_lf_pad', 'des_opt_left_tran',
                        'des_pad_mode', 'des_opd0_y_ins0', 'des_opd1_y_ins0',
                        'des_short_res0_str', 'des_short_opd1_str', 'des_sym_range', 'des_opt_rq', 'des_op_code',
                        'des_opt_kernel_rotate', 'des_res_op_x_str', 'des_res_op_y_str', 'des_opd0_x_ins0',
                        'des_tsk_opd_num', 'des_opd0_dn_pad', 'des_intr_en', 'des_opt_relu', 'des_pwr_step']
        self.reg_list = []
        self.perf_dict = dict()
        self.stati_list = []
        self.core_id = str(core_id)
        self.total_instr = 0
        self.sheet_name = 'TIU_' + str(core_id)
        self.height = None
        self.width = len(self.columns)
        self.sheet_color = '008000'
        # The architecture parameters of the AKSV are set to the initial values
        # It will be changed as the chip architecture parameters change
        self.detail_spec = {
                            'Platform': ['simulator'],
                            'CHIP ARCH': ['AKS'],
                            'Core Num': ['64'],
                            'NPU Num': ['64'],
                            'Cube IC Align(8bits)': ['32'],
                            'Cube OHOW Align': ['8'],
                            'Vector OHOW Align(8bits)': ['128'],
                            'Tiu Frequency(MHz)': ['1000'],
                            'DMA Frequency(MHz)': ['1000'],
                            'Dram Bandwidth': ['8533'],
                            'TPU Lmem Size(MiB)': ['16777216']}
        self.kpi_desc = pd.DataFrame({'Field': [
            'uArch Rate',
            'Data Type',
            'Avg Cycle Last 200'],
            'Description': [
                'Alg Ops / uArch Ops, since the shape of tensor needs to be aligned in '
                'micro-architecture, the actual ops will be greater than the algorithm value. '
                "It's better to closer to 100%.",
                'Data type transform, usually represents opd0->res0.',
                'The average cycle taken to execute the last 200 tiu commands. Consecutive short '
                'commands will increase the burden on PMU, leading to a decrease in performance']}, index=None)
        self.summary = dict()
        self.tiu_cycle = 0
        self.start_time = sys.maxsize
        self.end_time = 0
        self.tiu_time = 0
        self.alg_total_cycle = 0
        self.alg_total_ops = 0
        self.uArch_total_ops = 0
        self.wait_msg_time = 0
        self.chip_arch_dict = None

    def load(self, reg_info_file, tiu_layer_map):
        """
        Load data from external file.
        :param tiu_layer_map:
        :param reg_info_file: file records register information, usually obtained by TPUPerf
        :return: None
        """
        chip_arch_dict = None
        if os.path.exists(reg_info_file) and os.path.getsize(reg_info_file) != 0:
            last_underscore_index = reg_info_file.rfind('_')
            core_id = int(reg_info_file[last_underscore_index + 1 : -4])

            chip_arch_dict, self.reg_list = parse_records(
                reg_info_file, '__TIU_REG_INFO__', self.columns,
                skip_lines=0, has_func_type=True,
                value_converter=convert_tiu_value
            )
            apply_layer_map_simple(self.reg_list, tiu_layer_map, core_id)

            if chip_arch_dict:
                self.detail_spec = {
                    'Platform': [chip_arch_dict['Platform']],
                    'CHIP ARCH': [chip_arch_dict['Chip Arch']],
                    'Core Num': [chip_arch_dict['Core Num']],
                    'NPU Num': [chip_arch_dict['NPU Num']],
                    'Cube IC Align(8bits)': [chip_arch_dict['Cube IC Align(8bits)']],
                    'Cube OHOW Align': [chip_arch_dict['Cube OHOW Align(8bits)']],
                    'Vector OHOW Align(8bits)': [chip_arch_dict['Vector OHOW Align(8bits)']],
                    'TIU Frequency(MHz)': [chip_arch_dict['TIU Frequency(MHz)']],
                    'DMA Frequency(MHz)': [chip_arch_dict['DMA Frequency(MHz)']],
                    'DDR Frequency(GHz)': [chip_arch_dict['DDR Frequency(GHz)']],
                    'TPU Lmem Size(MiB)': [chip_arch_dict['TPU Lmem Size(MiB)']]}
        self.height = len(self.reg_list)
        self.chip_arch_dict = chip_arch_dict
        return chip_arch_dict

    def add_kpi_field(self):
        """
        Add some indicators which are convenient for performance analysis artificially.
        :return: None
        """
        for i in range(len(self.reg_list)):
            reg_dict = self.reg_list[i]
            continous_gap = 200
            if i < continous_gap - 1 :
                reg_dict['Avg Cycle Last 200'] = round(int(reg_dict['End Cycle']) / int(reg_dict['Cmd Id']))
            else:
                reg_dict['Avg Cycle Last 200'] = round((reg_dict['End Cycle'] - self.reg_list[i-199]['Start Cycle']) / continous_gap)
            if not (reg_dict['des_tsk_typ'] == 15 and reg_dict['des_tsk_eu_typ'] == 9):
                # wait msg time do not add to tiu cycles
                self.tiu_cycle += int(reg_dict['Asic Cycle'])
            self.alg_total_cycle += int(reg_dict['Alg Cycle'])
            self.alg_total_ops += int(reg_dict['Alg Ops'])
            self.uArch_total_ops += int(reg_dict['uArch Ops'])
            if isinstance(reg_dict['des_opt_opd0_prec'], int):
                reg_dict['Data Type'] = data_type_dict[reg_dict['des_opt_opd0_prec']] + \
                                        ' -> ' + data_type_dict[reg_dict['des_opt_res0_prec']]
            else:
                reg_dict['Data Type'] = data_type_dict[reg_dict['des_opt_res0_prec']] + \
                                        ' -> ' + data_type_dict[reg_dict['des_opt_res0_prec']]
            if int(reg_dict['des_tsk_typ']) == 15 and int(reg_dict['des_tsk_eu_typ']) == 9:
                self.wait_msg_time += int(reg_dict['Asic Cycle'])
            self.start_time = min(self.start_time, get_time_by_cycle(reg_dict['Start Cycle'], self.chip_arch_dict['TIU Frequency(MHz)']))
            self.end_time = max(self.start_time, get_time_by_cycle(reg_dict['End Cycle'], self.chip_arch_dict['TIU Frequency(MHz)']))
            if reg_dict['Function Type'] not in self.perf_dict.keys():
                func_dict = {
                    'Function Name': reg_dict['Function Type'],
                    'Instr Num': 1,
                    'Alg Ops': int(reg_dict['Alg Ops']),
                    'Alg Ops Ratio': 0,
                    'Alg Cycle': int(reg_dict['Alg Cycle']),
                    'Alg Cycle Ratio': 0,
                    'uArch Ops': int(reg_dict['uArch Ops']),
                    'uArch URate': 0,
                    'uArch Ops Ratio': 0,
                    'Asic Cycle': int(reg_dict['Asic Cycle']),
                    'Asic Cycle Ratio': 0
                }
                self.perf_dict[reg_dict['Function Type']] = func_dict
            else:
                func_dict = self.perf_dict[reg_dict['Function Type']]
                func_dict['Instr Num'] += 1
                func_dict['Alg Ops'] += int(reg_dict['Alg Ops'])
                func_dict['uArch Ops'] += int(reg_dict['uArch Ops'])
                func_dict['Alg Cycle'] += int(reg_dict['Alg Cycle'])
                func_dict['Asic Cycle'] += int(reg_dict['Asic Cycle'])
                self.perf_dict[reg_dict['Function Type']] = func_dict
            self.total_instr += 1
        self.tiu_time = get_time_by_cycle(self.tiu_cycle, self.chip_arch_dict['TIU Frequency(MHz)']) if self.chip_arch_dict else 0

    def pop_data(self, core_id):
        tiu_instance_map = dict()
        for reg in self.reg_list:
            tiu_instance_map[(int(reg['Cmd Id']), core_id)] = TiuNode(reg)
        return tiu_instance_map

    def write(self, style=True, frozen=False):
        """
        Write register information and kpi field to Excel, with optional styling.
        :return: None
        """
        if len(self.reg_list) <= 0:
            return
        # Determine present columns without creating a DataFrame
        present_keys = set(self.reg_list[0].keys())
        new_cols = [col for col in self.columns if col in present_keys]
        self.columns = new_cols
        hex_cols = frozenset(col for col in self.columns if 'addr' in col or 'mask' in col)
        self.summary = {
            'totalTiuCycle': [self.tiu_cycle],
            'totalAlgCycle': [self.alg_total_cycle],
            'algTotalOps': [self.alg_total_ops],
            'totalUArchOps': [self.uArch_total_ops],
            'uArchURate': [get_ratio_str_2f_zero(self.alg_total_ops, self.uArch_total_ops)],
            'waitMsgTotalTime': [self.wait_msg_time]
        }
        for func in self.perf_dict.keys():
            tmp_func_dict = self.perf_dict[func]
            tmp_func_dict['Function Name'] = func
            tmp_func_dict['Alg Ops Ratio'] = get_ratio_str_2f_zero(tmp_func_dict['Alg Ops'], self.alg_total_ops)
            tmp_func_dict['Alg Cycle Ratio'] = get_ratio_str_2f_zero(tmp_func_dict['Alg Cycle'], self.alg_total_cycle)
            tmp_func_dict['uArch URate'] = get_ratio_str_2f_zero(tmp_func_dict['Alg Ops'], tmp_func_dict['uArch Ops'])
            tmp_func_dict['uArch Ops Ratio'] = get_ratio_str_2f_zero(tmp_func_dict['uArch Ops'], self.uArch_total_ops)
            tmp_func_dict['Asic Cycle Ratio'] = get_ratio_str_2f_zero(tmp_func_dict['Asic Cycle'], self.tiu_cycle)
            self.perf_dict[func] = tmp_func_dict
            self.stati_list.append(tmp_func_dict)
        self.perf_dict['Overall'] = {
            'Function Name': 'Overall',
            'Instr Num': self.total_instr,
            'Alg Ops': self.alg_total_ops,
            'Alg Ops Ratio': '100.00%',
            'Alg Cycle': self.alg_total_cycle,
            'Alg Cycle Ratio': '100.00%',
            'uArch Ops': self.uArch_total_ops,
            'uArch URate': get_ratio_str_2f_zero(self.alg_total_ops, self.uArch_total_ops),
            'uArch Ops Ratio': '100.00%',
            'Asic Cycle': self.tiu_cycle,
            'Asic Cycle Ratio': '100.00%'
        }
        self.stati_list.append(self.perf_dict['Overall'])
        is_simulator = self.chip_arch_dict['Platform'].lower() == 'simulator'
        if is_simulator:
            for d in self.stati_list:
                d['Simulator Cycle'] = d.pop('Asic Cycle')
                d['Simulator Cycle Ratio'] = d.pop('Asic Cycle Ratio')

        if len(self.reg_list) <= 0:
            return

        # Create sheet via empty DataFrame then get xlsxwriter objects
        pd.DataFrame().to_excel(self.writer, index=False, sheet_name=self.sheet_name)
        workbook = self.writer.book
        worksheet = self.writer.sheets[self.sheet_name]

        # ---- Create format objects ----
        D = DetailsStyle
        fmt_title = merge_format(workbook, D.title_pattern, D.title_font)
        fmt_header = merge_format(workbook, D.title_header_pattern, D.title_header_font)
        fmt_header_border = merge_format(workbook, D.title_header_pattern, D.title_header_font, D.border)
        fmt_content_title = merge_format(workbook, D.title_content_pattern, D.title_font)
        fmt_content_title_border = merge_format(workbook, D.title_content_pattern, D.title_font, D.border, D.center_align)
        fmt_content_title_left = merge_format(workbook, D.title_content_pattern, D.title_font, D.left_align)
        fmt_content_header = merge_format(workbook, D.content_pattern, D.title_header_font)
        fmt_key = merge_format(workbook, D.key_content_pattern, D.border, D.right_align, D.title_font)
        fmt_normal_font = merge_format(workbook, D.title_font)

        # ---- Section 1: Labels ----
        # row 0 in the xlsxwriter worksheet
        worksheet.write(0, 0, 'Performance', fmt_title)
        worksheet.write(1, 0, 'Summary', fmt_title)
        worksheet.write(0, 14, 'Detail Spec', fmt_title)
        worksheet.write(5, 0, 'Statistics', fmt_title)
        worksheet.write(3, 14, 'Description', fmt_title)

        # ---- Section 2: Summary (row 0-1, cols 1-7) ----
        summary_keys = list(self.summary.keys())
        worksheet.write_row(0, 1, summary_keys, fmt_header)
        worksheet.write_row(1, 1, [self.summary[key][0] for key in summary_keys], fmt_content_title)

        # ---- Section 3: Detail Spec (row 0-1, cols 15-25) ----
        detail_keys = list(self.detail_spec.keys())
        worksheet.write_row(0, 15, detail_keys, fmt_header)
        worksheet.write_row(1, 15, [self.detail_spec[key][0] for key in detail_keys], fmt_content_title)

        # ---- Section 4: KPI Description (row 3-8, cols 15-16) ----
        kpi_fields = self.kpi_desc['Field'].tolist()
        kpi_descs = self.kpi_desc['Description'].tolist()
        worksheet.write(3, 15, 'Field', fmt_header)
        worksheet.write(3, 16, 'Description', fmt_header)
        for i in range(len(kpi_fields)):
            worksheet.write(4 + i, 15, kpi_fields[i], fmt_content_title)
            worksheet.write(4 + i, 16, kpi_descs[i], fmt_content_title_left)
        # merge description cells (cols 16-29, rows 4-8)
        desc_fmt = merge_format(workbook, D.title_content_pattern, D.title_font, D.left_align)
        for r in range(4, 4 + len(kpi_fields)):
            worksheet.merge_range(r, 16, r, 29, kpi_descs[r - 4], desc_fmt)

        # ---- Section 5: Statistics (row 5 onwards, cols 1-13) ----
        perf_start_row = 5
        stati_keys = list(self.stati_list[0].keys()) if self.stati_list else []
        worksheet.write_row(perf_start_row, 1, stati_keys, fmt_header)
        for row_idx, func_dict in enumerate(self.stati_list):
            worksheet.write_row(
                perf_start_row + 1 + row_idx,
                1,
                [func_dict.get(key, '') for key in stati_keys],
                fmt_content_title,
            )

        perf_df_len = len(self.stati_list)
        content_start_rows = perf_df_len + 8  # 0-based row for content header

        # ---- Section 6: Content header row ----
        col_headers = list(self.columns)
        if is_simulator:
            col_headers = ['Simulator Cycle' if c == 'Asic Cycle' else c for c in col_headers]
        worksheet.write_row(content_start_rows - 1, 0, col_headers, fmt_content_header)

        # ---- Section 7: Content data rows ----
        offset = 1  # global_id offset
        content_end_cols = min(19 + offset, len(self.columns))

        row_offset = content_start_rows
        for row_idx, reg_dict in enumerate(self.reg_list):
            row_num = row_offset + row_idx
            key_row = []
            for col_name in self.columns[:content_end_cols]:
                val = reg_dict.get(col_name, '')
                if col_name in hex_cols:
                    val = hex(int(val)) if isinstance(val, int) else ''
                key_row.append(val)
            worksheet.write_row(row_num, 0, key_row, fmt_key)

            for ci, col_name in enumerate(self.columns[content_end_cols:], start=content_end_cols):
                val = reg_dict.get(col_name, '')
                if col_name in hex_cols:
                    val = hex(int(val)) if isinstance(val, int) else ''
                if val in (None, ''):
                    continue
                worksheet.write(row_num, ci, val, fmt_normal_font)

        # ---- Section 9: Column widths ----
        # Compute max content width for each column
        for ci, col_name in enumerate(col_headers):
            max_len = len(str(col_name))
            if ci < content_end_cols + 6:
                # sample first 30 data rows for width
                for row_idx in range(min(30, len(self.reg_list))):
                    val = self.reg_list[row_idx].get(self.columns[ci] if ci < len(self.columns) else '', '')
                    if val is not None and len(str(val)) <= 35:
                        max_len = max(max_len, len(str(val)))
            else:
                # for far-right columns, just use header length
                if len(self.reg_list) > 0:
                    val = self.reg_list[0].get(self.columns[ci] if ci < len(self.columns) else '', '')
                    if val is not None:
                        max_len = max(max_len, len(str(val)))
            worksheet.set_column(ci, ci, max_len * 1.05)

        # ---- Section 10: Borders for header areas ----
        if style:
            # Summary borders
            for w in range(1, 1 + len(summary_keys)):
                worksheet.write(0, w, list(self.summary.keys())[w - 1], fmt_header_border)
                worksheet.write(1, w, list(self.summary.values())[w - 1][0],
                                merge_format(workbook, D.title_content_pattern, D.title_font, D.border, D.center_align))
            # Statistics borders
            for row_idx in range(perf_df_len):
                for col_idx in range(len(stati_keys)):
                    pass  # already written with format above

        # ---- Section 11: Tab color & freeze panes ----
        worksheet.set_tab_color('#008000')
        if frozen:
            worksheet.freeze_panes(content_start_rows, 0)

    @classmethod
    def set_style(cls, file_path, core_id, frozen=False):
        """
        No-op: styling is now applied during write() via xlsxwriter.
        """
        pass
