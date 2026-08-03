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
# @Time    : 2023/8/7 11:26
# @Author  : chongqing.zeng@bigtpu.com
# @Project: PerfAI
import os, glob, re
import pandas as pd
from tqdm import tqdm

from include.dma import Gdma, Sdma, Cdma
from include.instr_world import InstrWorld
from include.asic_summary import AsicSummary
from include.tiu import Tiu
from utils.utils import get_instr_cols, get_instr_reg_list, get_active_cores, get_total_time
from include.summary import GlobalInfo


def palace_holder(writer, splited, g_info):
    if g_info is not None:
        sheet_name = 'Summary'
        df = pd.DataFrame()
        df.to_excel(writer, index=False, sheet_name=sheet_name, engine='xlsxwriter', float_format='%g')
        sheet_name = 'Layer by Layer Info'
        df.to_excel(writer, index=False, sheet_name=sheet_name, engine='xlsxwriter', float_format='%g')
    sheet_name = 'Engine Summary'
    df = pd.DataFrame()
    df.to_excel(writer, index=False, sheet_name=sheet_name, engine='xlsxwriter', float_format='%g')
    if not splited:
        sheet_name = 'Instr World'
        df.to_excel(writer, index=False, sheet_name=sheet_name, engine='xlsxwriter', float_format='%g')


def get_engine_layer(g_info):
    tiu_layer_map = dict()
    gdma_layer_map = dict()
    if isinstance(g_info, GlobalInfo):
        for layer_info in g_info.subnet_list[0].layer_list:
            for tiu_node in layer_info.bd_nodes:
                k = tiu_node.bd_id
                c = tiu_node.core_id
                if (k,c) in tiu_layer_map.keys():
                    print('ERROR! Tiu id is not unique.')
                    assert 0
                else:
                    tiu_layer_map[(k,c)] = [layer_info.layer_id, layer_info.layer_name]
            for gdma_node in layer_info.gdma_nodes:
                k = gdma_node.gdma_id
                c = gdma_node.core_id
                if (k,c) in gdma_layer_map.keys():
                    print('ERROR! Gdma id is not unique.')
                    assert 0
                else:
                    gdma_layer_map[(k,c)] = [layer_info.layer_id, layer_info.layer_name]
    return tiu_layer_map, gdma_layer_map


def _load_core_engines(core_id, tiu_reg_file, dma_reg_file, tiu_layer_map, gdma_layer_map):
    """Load and compute KPI for one core's TIU, GDMA, SDMA engines (no writer needed).
    Parses DMA file only once and splits records for GDMA/SDMA."""
    cur_tiu_reg_file = tiu_reg_file + '_' + str(core_id) + '.txt'
    cur_dma_reg_file = dma_reg_file + '_' + str(core_id) + '.txt'
    # tiu
    tiu_instance = Tiu(core_id, None)
    chip_arch = tiu_instance.load(cur_tiu_reg_file, tiu_layer_map)
    tiu_instance.add_kpi_field()
    # Parse DMA file once, then split by engine_id for GDMA(1) and SDMA(3)
    gdma_instance = Gdma(core_id, None, 'GDMA')
    dma_chip_arch = None
    if os.path.exists(cur_dma_reg_file) and os.path.getsize(cur_dma_reg_file) != 0:
        from shared_parser import parse_records, apply_layer_map_simple, filter_by_engine_id, convert_dma_value
        last_underscore_index = cur_dma_reg_file.rfind('_')
        file_core_id = int(cur_dma_reg_file[last_underscore_index + 1 : -4])
        dma_chip_arch, all_dma_records = parse_records(
            cur_dma_reg_file, '__TDMA_REG_INFO__', gdma_instance.columns,
            skip_lines=0, has_func_type=False, value_converter=convert_dma_value
        )
        apply_layer_map_simple(all_dma_records, gdma_layer_map, file_core_id)
        gdma_instance.chip_arch_dict = dma_chip_arch
        gdma_instance.reg_list = filter_by_engine_id(all_dma_records, 1)
        gdma_instance.height = len(gdma_instance.reg_list)
        gdma_instance.add_kpi_field()
        # sdma reuses same parsed records
        sdma_instance = Sdma(core_id, None, 'SDMA')
        sdma_instance.chip_arch_dict = dma_chip_arch
        sdma_instance.reg_list = filter_by_engine_id(all_dma_records, 3)
        sdma_instance.height = len(sdma_instance.reg_list)
        sdma_instance.add_kpi_field()
    else:
        gdma_instance.add_kpi_field()
        sdma_instance = Sdma(core_id, None, 'SDMA')
        sdma_instance.add_kpi_field()
    return core_id, tiu_instance, gdma_instance, sdma_instance, chip_arch or dma_chip_arch


def generate_details(input_fold, out_file, g_info, writer, core_num=8, split_instr_world=False):
    tiu_reg_file = 'tiuRegInfo'
    dma_reg_file = 'tdmaRegInfo'
    cdma_reg_file = 'cdmaRegInfo'
    tiu_reg_file = input_fold + tiu_reg_file
    dma_reg_file = input_fold + dma_reg_file
    cdma_reg_file = input_fold + cdma_reg_file
    act_core_num = 0
    act_core_num = max(act_core_num, get_active_cores(tiu_reg_file, core_num))
    act_core_num = max(act_core_num, get_active_cores(dma_reg_file, core_num))
    act_core_num = max(act_core_num, get_active_cores(cdma_reg_file, core_num))
    if not act_core_num:
        print('Error, No engine reg info file input! Please check your input.')
        exit(-1)
    print('Generating data for ' + out_file)
    palace_holder(writer, split_instr_world, g_info)
    instr_cols, instr_reg_list, reg_list = [], [], []
    tiu_instances, gdma_instances, sdma_instances, cdma_instances = [], [], [], []
    tiu_instance_map, gdma_instance_map = dict(), dict()
    tiu_layer_map, gdma_layer_map = get_engine_layer(g_info)
    chip_arch_act = None
    file_names = sorted(glob.glob(cdma_reg_file + '_*.txt'))

    # Phase 1: Load all cores sequentially (CPU-bound, ThreadPoolExecutor adds GIL contention)
    core_results = {}
    for core_id in range(act_core_num):
        result = _load_core_engines(core_id, tiu_reg_file, dma_reg_file, tiu_layer_map, gdma_layer_map)
        cid, tiu_inst, gdma_inst, sdma_inst, arch = result
        core_results[cid] = (tiu_inst, gdma_inst, sdma_inst, arch)

    # Phase 2: Write sheets sequentially (writer is not thread-safe)
    for core_id in tqdm(range(act_core_num)):
        tiu_instance, gdma_instance, sdma_instance, arch = core_results[core_id]
        if chip_arch_act is None and arch is not None:
            chip_arch_act = arch
        # Set writer and write
        tiu_instance.writer = writer
        tiu_instance.write()
        if str(core_id) == '0':
            tiu_instance_map = tiu_instance.pop_data(core_id)
        else:
            tiu_instance_map.update(tiu_instance.pop_data(core_id))
        gdma_instance.writer = writer
        gdma_instance.write()
        if str(core_id) == '0':
            gdma_instance_map = gdma_instance.pop_data()
        sdma_instance.writer = writer
        sdma_instance.write()
        # cdma
        if chip_arch_act['Chip Arch'] == 'AKS' or chip_arch_act['Chip Arch'] == 'AKSV':
            reg_list += tiu_instance.reg_list + gdma_instance.reg_list + sdma_instance.reg_list
            cdma_instance = None
            if  act_core_num and core_id == act_core_num - 1 and file_names:
                for f in file_names:
                    port = eval(re.search(rf"{cdma_reg_file}_(\d+)\.txt", f).group(1))
                    cdma_instance = Cdma(port, writer, 'CDMA')
                    if os.path.exists(f) and os.path.getsize(f):
                        tmp_chip_arch = cdma_instance.load(f)
                        chip_arch_act = chip_arch_act if chip_arch_act else tmp_chip_arch
                        cdma_instance.add_kpi_field(True)
                        cdma_instance.write()
                    reg_list += cdma_instance.reg_list
                    cdma_instances.append(cdma_instance)
            else:
                cdma_instance = Cdma(core_id, writer, 'CDMA')
                cdma_instances.append(cdma_instance)
        else:
            cdma_instance = Cdma(core_id, writer, 'CDMA')
            if act_core_num:
                cur_cdma_reg_file = cdma_reg_file + '_' + str(core_id) + '.txt'
                if os.path.exists(cur_cdma_reg_file) and os.path.getsize(cur_cdma_reg_file):
                    tmp_chip_arch = cdma_instance.load(cur_cdma_reg_file)
                    chip_arch_act = chip_arch_act if chip_arch_act else tmp_chip_arch
                    cdma_instance.add_kpi_field(True)
                    cdma_instance.write()
                reg_list += tiu_instance.reg_list + gdma_instance.reg_list + sdma_instance.reg_list + cdma_instance.reg_list
            else:
                reg_list += tiu_instance.reg_list + gdma_instance.reg_list + sdma_instance.reg_list
            cdma_instances.append(cdma_instance)
        instr_cols = get_instr_cols(tiu_instance.columns, gdma_instance.columns)
        tiu_instances.append(tiu_instance)
        gdma_instances.append(gdma_instance)
        sdma_instances.append(sdma_instance)

    if act_core_num:
        # instr world
        instr_reg_list = get_instr_reg_list(reg_list, instr_cols)
        instr_instance = InstrWorld(instr_reg_list, instr_cols, writer, split_instr_world)
        instr_instance.write(out_file)
        # summary
        summary_instance = AsicSummary(writer, tiu_instances, gdma_instances, sdma_instances, cdma_instances, act_core_num)
        summary_instance.load(chip_arch_act)
        summary_instance.write()
    chip_arch_act['concurrency'] = summary_instance.data[-1][3]
    chip_arch_act['total_time(us)'] = summary_instance.data[-1][4]
    return tiu_instance_map, gdma_instance_map, chip_arch_act


def generate_divided_details(input_fold, g_info, core_num=8):
    output_fold_summary = input_fold + 'Summary'
    output_fold_tiu = input_fold + 'Tiu'
    output_fold_gdma = input_fold + 'Gdma'
    output_fold_sdma = input_fold + 'Sdma'
    output_fold_cdma = input_fold + 'Cdma'
    if not os.path.exists(output_fold_summary):
            os.makedirs(output_fold_summary)
    if not os.path.exists(output_fold_tiu):
            os.makedirs(output_fold_tiu)
    if not os.path.exists(output_fold_gdma):
            os.makedirs(output_fold_gdma)
    if not os.path.exists(output_fold_sdma):
            os.makedirs(output_fold_sdma)
    if not os.path.exists(output_fold_cdma):
            os.makedirs(output_fold_cdma)
    output_file_summary = output_fold_summary + '/PerAI_output_summary.xlsx'
    tiu_reg_file = 'tiuRegInfo'
    dma_reg_file = 'tdmaRegInfo'
    cdma_reg_file = 'cdmaRegInfo'
    tiu_reg_file = input_fold + tiu_reg_file
    dma_reg_file = input_fold + dma_reg_file
    cdma_reg_file = input_fold + cdma_reg_file
    act_core_num = 0
    act_core_num = max(act_core_num, get_active_cores(tiu_reg_file, core_num))
    act_core_num = max(act_core_num, get_active_cores(dma_reg_file, core_num))
    act_core_num = max(act_core_num, get_active_cores(cdma_reg_file, core_num))
    if not act_core_num:
        print('Error, No engine reg info file input! Please check your input.')
        exit(-1)
    print('Generating data for ' + input_fold)
    instr_cols, instr_reg_list, reg_list = [], [], []
    tiu_instances, gdma_instances, sdma_instances, cdma_instances = [], [], [], []
    tiu_instance_map, gdma_instance_map = dict(), dict()
    tiu_layer_map, gdma_layer_map = get_engine_layer(g_info)
    chip_arch_act = None
    for core_id in tqdm(range(act_core_num)):
        # tiu
        cur_tiu_reg_file = tiu_reg_file + '_' + str(core_id) + '.txt'
        output_file_tiu = output_fold_tiu + f'/PerAI_output_tiu_{core_id}.xlsx'
        with pd.ExcelWriter(output_file_tiu, engine='xlsxwriter') as tiu_writer:
            tiu_instance = Tiu(core_id, tiu_writer)
            chip_arch = tiu_instance.load(cur_tiu_reg_file, tiu_layer_map)
            chip_arch_act = chip_arch_act if chip_arch_act else chip_arch
            tiu_instance.add_kpi_field()
            tiu_instance.write()
        if str(core_id) == '0':
            tiu_instance_map = tiu_instance.pop_data()
        # gdma
        cur_dma_reg_file = dma_reg_file + '_' + str(core_id) + '.txt'
        output_file_gdma = output_fold_gdma + f'/PerAI_output_gdma_{core_id}.xlsx'
        with pd.ExcelWriter(output_file_gdma, engine='xlsxwriter') as gdma_writer:
            gdma_instance = Gdma(core_id, gdma_writer, 'GDMA')
            tmp_chip_arch = gdma_instance.load(cur_dma_reg_file, gdma_layer_map)
            chip_arch_act = chip_arch_act if chip_arch_act else tmp_chip_arch
            gdma_instance.add_kpi_field()
            gdma_instance.write()
        if str(core_id) == '0':
            gdma_instance_map = gdma_instance.pop_data()
        # sdma
        cur_dma_reg_file = dma_reg_file + '_' + str(core_id) + '.txt'
        output_file_sdma = output_fold_sdma + f'/PerAI_output_sdma_{core_id}.xlsx'
        with pd.ExcelWriter(output_file_sdma, engine='xlsxwriter') as sdma_writer:
            sdma_instance = Sdma(core_id, sdma_writer, 'SDMA')
            sdma_instance.load(cur_dma_reg_file, gdma_layer_map)
            sdma_instance.add_kpi_field()
            sdma_instance.write()
        # cdma
        output_file_cdma = output_fold_cdma + f'/PerAI_output_cdma_{core_id}.xlsx'
        with pd.ExcelWriter(output_file_cdma, engine='xlsxwriter') as cdma_writer:
            cdma_instance = Cdma(core_id, cdma_writer, 'CDMA')
        if act_core_num:
            cur_cdma_reg_file = cdma_reg_file + '_' + str(core_id) + '.txt'
            if os.path.exists(cur_cdma_reg_file) and os.path.getsize(cur_cdma_reg_file):
                tmp_chip_arch = cdma_instance.load(cur_cdma_reg_file)
                chip_arch_act = chip_arch_act if chip_arch_act else tmp_chip_arch
                cdma_instance.add_kpi_field(True)
                cdma_instance.write()
            reg_list += tiu_instance.reg_list + gdma_instance.reg_list + sdma_instance.reg_list + cdma_instance.reg_list
        else:
            reg_list += tiu_instance.reg_list + gdma_instance.reg_list + sdma_instance.reg_list
        instr_cols = get_instr_cols(tiu_instance.columns, gdma_instance.columns)
        tiu_instances.append(tiu_instance)
        gdma_instances.append(gdma_instance)
        sdma_instances.append(sdma_instance)
        cdma_instances.append(cdma_instance)

    if act_core_num:
        # # instr world
        # instr_reg_list = get_instr_reg_list(reg_list, instr_cols)
        # instr_instance = InstrWorld(instr_reg_list, instr_cols, writer, split_instr_world)
        # instr_instance.write(out_file)
        # summary
        with pd.ExcelWriter(output_file_summary, engine='xlsxwriter') as summary_writer:
            summary_instance = AsicSummary(summary_writer, tiu_instances, gdma_instances, sdma_instances, cdma_instances, act_core_num)
            summary_instance.load(chip_arch_act)
            summary_instance.write()
    return tiu_instance_map, gdma_instance_map, chip_arch_act
