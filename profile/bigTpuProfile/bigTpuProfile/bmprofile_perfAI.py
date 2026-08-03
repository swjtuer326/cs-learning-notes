#!/usr/bin/python3
# ==============================================================================
#
# Copyright (C) 2022 sophon Technologies Inc.  All rights reserved.
#
# TPU-MLIR is licensed under the 2-Clause BSD License except for the
# third-party components.
#
# ==============================================================================

from .profile_helper.binary import (
    FixedItemWrapper,
    parse_data_blocks,
    parse_dyn_extra,
    parse_fixed_length_items,
    parse_monitor_bd,
    parse_monitor_cdma,
    parse_monitor_gdma,
)
from .profile_helper.bmprofile_common import Arch, BlockType
from .profile_helper.chip_registry import load_chip_profile
from .profile_helper.models import ProfileIteration, ProfileResult
from .profile_helper.matcher import omit_system_commands, pair_sections
from .profile_helper.normalizer import (
    adjust_command_order,
    adjust_send_retire_order,
    align_core_time,
    normalize_cdma_time,
    normalize_command_ids,
    normalize_time,
    shift_time_to_zero,
)
import os
import re
from typing import List
import glob
import struct as st
from tqdm import tqdm


def get_cmd_id(c):
    cmd_id = -1
    if hasattr(c, 'cmd_id'):
        cmd_id = c.cmd_id
    else:
        cmd_id = c.inst_id
    return cmd_id

class DesCommon(object):
    def __init__(self, cmd_num, id, cmd) -> None:
        self.cmd_num = cmd_num
        self.id = id
        self.cmd = cmd

    def parse(self, parser):
        if isinstance(self.cmd, bytes):
            self.cmd = parser.parse(self.cmd)
        return self.cmd

    def __repr__(self) -> str:
        return f"DesCommon(cmd_num={self.cmd_num}, id={self.id}, cmd_len={len(self.cmd)})"

class ProfileParser:
    global_filename = "global.profile"

    def __init__(self):
        self._reset_parse_state()

    def _reset_parse_state(self):
        self.archlib = None
        self.gdma_pairs = []
        self.sdma_pairs = []
        self.cdma_pairs = []
        self.bd_pairs = []
        self.profile_sync_points = []
        self.in_dir = None
        self.cdma_core_ids = []
        self.is_bmodel = False
        self.gdma_events = []
        self.sdma_events = []
        self.cdma_events = []
        self.bd_events = []
        self.num_cores = 8
        self.tail_offset_ns = None
        self.pmu_tail_cycle = None
        self.func_scope_id = 0

    def parse_cdma_pmu(self, file_list):
        self.cdma_pairs = [[] for _ in range(self.archlib.CDMA_NUM)]
        self.cdma_core_ids = [None for _ in range(self.archlib.CDMA_NUM)]
        for infile in file_list:
            match = re.search(r"cdma\d*_(\d+)\.profile$", infile)
            if match is None:
                raise ValueError(f"invalid CDMA profile filename: {infile}")
            idx = int(match.group(1))
            if idx >= len(self.cdma_pairs):
                raise ValueError(
                    f"CDMA port {idx} exceeds {self.archlib.CDMA_NUM} ports"
                )
            blocks = parse_data_blocks(infile)
            if blocks is None or blocks == []:
                continue
            blocks_factory = {
                BlockType.MONITOR_CDMA.value: (self.cdma_pairs[idx], self.__parse_monitor_cdma),
            }
            for block in blocks:
                item_list, item_func = blocks_factory.get(
                    block.type.value, (0, lambda x, y: 0))
                item_func(item_list, block.content)

    def parse_cmd(self, file_list):
        self.bdc_parser = self.archlib.BDCommandParser()
        self.gdma_parser = self.archlib.GDMACommandParser()
        self.cdma_parser = self.archlib.CDMACommandParser()
        print(
            f"Detected chip {self.archlib.name}"
        )
        print("Parsing...")
        core_id = 0
        for infile in tqdm(file_list):
            blocks = parse_data_blocks(infile)
            if blocks is None:
                continue
            item = ProfileIteration()
            for e in ["tiu", "gdma", "sdma"]:
                item.dyn_data.update({e: []})
            item.dyn_data["cdma"] = [[] for _ in range(self.archlib.CDMA_NUM)]

            blocks_factory = {
                # ============= pmu =============
                BlockType.MONITOR_BD.value: (item.monitor_bd, self.__parse_monitor_tiu),
                BlockType.MONITOR_GDMA.value: (item.monitor_gdma, self.__parse_monitor_gdma),
                BlockType.MONITOR_SDMA.value: (item.monitor_sdma, self.__parse_monitor_sdma),
                # BlockType.MONITOR_CDMA.value: (item.monitor_cdma, self.__parse_monitor_cdma),
                # ============= recorded during runtime =============
                BlockType.DYN_EXTRA.value: (item.dyn_extra, self.__parse_dyn_extra),
                BlockType.DYN_DATA.value: (item, self.__parse_dyn_data),
                # ============= tpudnn des map =============
                BlockType.BLOCK_DES_KV.value: (item.des_kv, self.__parse_kvdes_data),
                # ============= bmodel =============
                BlockType.BLOCK_DES_BDC.value: (item.des_bdc, lambda l, raw_data: l.extend(self.bdc_parser.parse(raw_data))),
                BlockType.BLOCK_DES_GDMA.value: (item.des_gdma, lambda l, raw_data: l.extend(self.gdma_parser.parse(raw_data))),
                BlockType.BLOCK_DES_SDMA.value: (item.des_sdma, lambda l, raw_data: l.extend(self.gdma_parser.parse(raw_data))),
            }
            for block in blocks:
                item_list, item_func = blocks_factory.get(
                    block.type.value, (0, lambda x, y: 0))
                item_func(item_list, block.content)
            if self.is_bmodel:
                self.__match_bmodel_sections(item)
            else:
                self.__match_pmu_sections(item, core_id)

            core_id += 1

    def parse(self, in_dir):
        self._reset_parse_state()

        def sort_key_func(filename):
            numbers = re.findall(r'\d+', filename)
            return [int(num) for num in numbers]
        self.in_dir = in_dir
        if not os.path.exists(in_dir):
            raise FileNotFoundError(in_dir)
        global_file_path = os.path.join(in_dir, self.global_filename)
        self.__parse_global_file(global_file_path)
        blocked_cmd = sorted(glob.glob(in_dir + "/cdmlib*.profile"), key=sort_key_func)
        cdma_cmd = sorted(glob.glob(in_dir + "/cdma*.profile"), key=sort_key_func)
        if cdma_cmd:
            self.parse_cdma_pmu(cdma_cmd)
        if blocked_cmd:
            self.parse_cmd(blocked_cmd)
        align_core_time(
            self.bd_pairs,
            self.gdma_pairs,
            self.sdma_pairs,
            self.cdma_pairs,
            self.profile_sync_points,
            self.cdma_core_ids,
        )
        shift_time_to_zero(
            self.bd_pairs,
            self.gdma_pairs,
            self.sdma_pairs,
            self.cdma_pairs,
        )
        self.__record_pmu_tail_cycle()
        # omit_sys after recording the tail cycle used for CPU/PMU alignment.
        omit_system_commands(
            self.cdma_pairs, self.archlib.cdma_sys_code
        )
        remain = any(self.cdma_pairs)
        omit_system_commands(
            self.bd_pairs, self.archlib.bd_sys_code, remain
        )
        omit_system_commands(
            self.gdma_pairs, self.archlib.dma_sys_code, remain
        )
        omit_system_commands(
            self.sdma_pairs, self.archlib.dma_sys_code, remain
        )
        num_cores = max(len(self.gdma_pairs), len(self.sdma_pairs), len(self.bd_pairs))
        self.num_cores = num_cores
        for idx in tqdm(range(num_cores)):
            if idx < len(self.gdma_pairs):
                self.gdma_events.append(self.__get_engine_info(idx, self.gdma_pairs[idx], self.archlib.EngineType.GDMA))
            if idx < len(self.sdma_pairs):
                self.sdma_events.append(self.__get_engine_info(idx, self.sdma_pairs[idx], self.archlib.EngineType.SDMA))
            if idx < len(self.bd_pairs):
                self.bd_events.append(self.__get_engine_info(idx, self.bd_pairs[idx], self.archlib.EngineType.BD))
        for idx, pair in enumerate(self.cdma_pairs):
            self.cdma_events.append(self.__get_engine_info(idx, pair, self.archlib.EngineType.CDMA))

        self.bd_pairs = []
        self.gdma_pairs = []
        self.sdma_pairs = []
        self.cdma_pairs = []
        return ProfileResult(
            archlib=self.archlib,
            bd_events=self.bd_events,
            gdma_events=self.gdma_events,
            sdma_events=self.sdma_events,
            cdma_events=self.cdma_events,
            num_cores=self.num_cores,
            input_dir=self.in_dir,
            tail_offset_ns=self.tail_offset_ns,
            pmu_tail_cycle=self.pmu_tail_cycle,
        )

    def __get_engine_info(self, idx, pairs, engine):
        g_idx = 0
        core_id = idx
        if engine in [self.archlib.EngineType.GDMA, self.archlib.EngineType.SDMA]:
            engin_id = 1
            if engine == self.archlib.EngineType.SDMA:
                engin_id = 3
            fn = self.__get_gdma_info
            arch = self.archlib.DMA_ARCH
        elif engine == self.archlib.EngineType.BD:
            engin_id = 0
            fn = self.__get_tiu_info
            arch = self.archlib.TIU_ARCH
        elif engine == self.archlib.EngineType.CDMA:
            engin_id = 4
            fn = self.__get_gdma_info
            arch = self.archlib.DMA_ARCH
            core_id = self.cdma_core_ids[idx]
        else:
            raise ValueError(f"Not support parse {self.archlib.EngineType(engine).name} now.")

        output = []
        for p in pairs:
            info, extra = fn(p, getattr(p, "cmd", None), engin_id)
            meta = {
                "Global Idx":g_idx,
                "Core Id":core_id
                }
            if hasattr(p, "func_name"):
                meta["kernel_func"] = p.func_name
            if hasattr(p, "func_scope_id"):
                meta["func_scope_id"] = p.func_scope_id
            if hasattr(p, "batch_idx"):
                meta["batch_launch"] = p.batch_idx
            if hasattr(p, "mode"):
                meta["mode"] = p.mode
            if engin_id == 4:
                meta["Port"] = idx
            output.append((info, extra, meta))
            g_idx += 1

        return output

    def __parse_dyn_data(self, item: dict, raw_data):
        # Notice:
        # pmu: bd gdma sdma start idx == 0, cdma strat idx == 1
        # des_cmd: strat idx == 1
        pio_tiu_cmd_id = 0
        pio_gdma_cmd_id = 0
        pio_sdma_cmd_id = 0
        pio_cdma_cmd_id = [0 for _ in range(self.archlib.CDMA_NUM)]
        tiu = item.dyn_data["tiu"]
        gdma = item.dyn_data["gdma"]
        sdma = item.dyn_data["sdma"]
        cdma = item.dyn_data["cdma"]
        tmp = parse_fixed_length_items(raw_data, self.archlib.ProfileFormat)
        dyn_extra_idx = 0
        # RVT command id wraparound
        def apply_cmd_id_wraparound():
            nonlocal pio_tiu_cmd_id, pio_gdma_cmd_id
            if not self.archlib.should_insert_wrap_commands(pio_tiu_cmd_id):
                return
            tiu_type, tiu_eu_type = self.archlib.spec.tiu_wrap_command
            dma_type, dma_eu_type = self.archlib.spec.dma_wrap_command
            tiu.append(FixedItemWrapper(
                type=self.archlib.DynRecordType.NODE_SET.value,
                engine=self.archlib.EngineType.BD.value,
                des_tsk_typ=tiu_type,
                des_tsk_eu_typ=tiu_eu_type,
                inst_id=pio_tiu_cmd_id,
                extra_info=0,
            ))
            gdma.append(FixedItemWrapper(
                type=self.archlib.DynRecordType.NODE_SET.value,
                engine=self.archlib.EngineType.GDMA.value,
                des_tsk_typ=dma_type,
                des_tsk_eu_typ=dma_eu_type,
                inst_id=pio_gdma_cmd_id,
                extra_info=0,
            ))
            pio_tiu_cmd_id, pio_gdma_cmd_id = 0, 0
        
        batch_idx, func_name, func_scope_id = None, None, None
        for idx in range(len(tmp)):
            node = tmp[idx]
            if (node is None):
                continue
            node_tpye = self.archlib.DynRecordType(node.type)
            # nomal pio tiu/gdma/sdma/vsdma/cdma node
            if node_tpye in [self.archlib.DynRecordType.NODE_SET,
                             self.archlib.DynRecordType.RVT_NODE_SET]:
                if batch_idx is not None:
                    node.batch_idx = batch_idx
                if func_name is not None:
                    node.func_name = func_name
                if func_scope_id is not None:
                    node.func_scope_id = func_scope_id
                engine = self.archlib.EngineType(node.engine)
                is_rvt_node = node_tpye == self.archlib.DynRecordType.RVT_NODE_SET
                node.mode = "RVT" if is_rvt_node else "TDI"
                if item.dyn_extra and not is_rvt_node:
                    node.detailed_cmd = item.dyn_extra[dyn_extra_idx].content
                    dyn_extra_idx += 1
                if engine == self.archlib.EngineType.BD:
                    node.inst_id = pio_tiu_cmd_id
                    pio_tiu_cmd_id += 1
                    tiu.append(node)
                    if is_rvt_node:
                        apply_cmd_id_wraparound()
                elif engine == self.archlib.EngineType.GDMA:
                    node.inst_id = pio_gdma_cmd_id
                    pio_gdma_cmd_id += 1
                    gdma.append(node)
                    if is_rvt_node:
                        apply_cmd_id_wraparound()
                elif engine in [self.archlib.EngineType.SDMA,
                                    self.archlib.EngineType.VSDMA]:
                    node.inst_id = pio_sdma_cmd_id
                    pio_sdma_cmd_id += 1
                    sdma.append(node)
                elif engine == self.archlib.EngineType.CDMA:
                    port = node.extra_info >> 8
                    node.port = port
                    node.inst_id = pio_cdma_cmd_id[port]
                    pio_cdma_cmd_id[port] += 1
                    cdma[port].append(node)
            # id reset
            elif node_tpye == self.archlib.DynRecordType.ID_RESET:
                engine = self.archlib.EngineType(node.engine)
                if engine == self.archlib.EngineType.BD:
                    pio_tiu_cmd_id = 0
                elif engine == self.archlib.EngineType.GDMA:
                    pio_gdma_cmd_id = 0
                elif engine in [self.archlib.EngineType.SDMA,
                                    self.archlib.EngineType.VSDMA]:
                    pio_sdma_cmd_id = 0
                elif engine == self.archlib.EngineType.CDMA:
                    pio_cdma_cmd_id[node.extra_info >> 8]  = 0
            # func
            elif node_tpye == self.archlib.DynRecordType.FUNC:
                _num = self.archlib.ProfileFormat.packed_num(node)
                _aligned_num = (_num + 3) // 4
                func_name = self.archlib.ProfileFormat.nodes_to_string(
                    tmp[idx + 1: idx + 1 + _aligned_num],
                    _num,
                )
                func_scope_id = self.func_scope_id
                self.func_scope_id += 1
                for i in range(idx, idx + 1 + _aligned_num):
                   tmp[i] = None 
            elif node_tpye == self.archlib.DynRecordType.FUNC_END:
                func_name = None
                func_scope_id = None
            elif node_tpye == self.archlib.DynRecordType.BATCH_IDX:
                batch_idx = self.archlib.ProfileFormat.packed_num(node)
            # dispatch des node
            elif self.archlib.ProfileFormat.is_des(node_tpye) :
                if batch_idx is not None:
                    node.batch_idx = batch_idx
                if func_name is not None:
                    node.func_name = func_name
                if func_scope_id is not None:
                    node.func_scope_id = func_scope_id
                node.mode = "DES"
                self.archlib.ProfileFormat.offset(node)
                self.archlib.ProfileFormat.cmd_num(node, tmp[idx + 1])
                tmp[idx + 1] = None
                pio_tiu_cmd_id = 0
                pio_gdma_cmd_id = 0
                pio_sdma_cmd_id = 0
                pio_cdma_cmd_id = [0 for _ in range(self.archlib.CDMA_NUM)]
                if node_tpye == self.archlib.DynRecordType.DES_TIU:
                    tiu.append(node)
                elif node_tpye == self.archlib.DynRecordType.DES_GDMA:
                    gdma.append(node)
                elif node_tpye == self.archlib.DynRecordType.DES_SDMA:
                    sdma.append(node)
                elif node_tpye == self.archlib.DynRecordType.DES_CDMA:
                    cdma[node.port].append(node)

    def __parse_kvdes_data(self, kv_data: List, raw_data):
        header_size = 12
        key, cmd_num, id = st.unpack(
            "III", raw_data[0:header_size])
        if key not in kv_data:
            kv_data[key] = {}
        kv_data[key].update({cmd_num: DesCommon(cmd_num, id, raw_data[header_size:])})

    def __parse_monitor_tiu(self, monitor_tiu: List, raw_data):
        tmp = parse_monitor_bd(raw_data, self.archlib)
        normalize_command_ids(tmp)
        normalize_time(tmp)
        monitor_tiu.append(tmp)

    def __parse_monitor_cdma(self, monitor_cdma: List, raw_data):
        tmp = parse_monitor_cdma(raw_data, self.archlib)
        normalize_command_ids(tmp)
        adjust_send_retire_order(tmp)
        normalize_cdma_time(tmp)
        adjust_command_order(tmp)
        monitor_cdma.extend(tmp)

    def __parse_monitor_dma_base(self, raw_data):
        tmp = parse_monitor_gdma(raw_data, self.archlib)
        normalize_command_ids(tmp)
        return tmp

    def __parse_monitor_gdma(self, monitor_gdma: List, raw_data):
        tmp = self.__parse_monitor_dma_base(raw_data)
        normalize_time(tmp)
        monitor_gdma.append(tmp)

    def __parse_monitor_sdma(self, monitor_sdma: List, raw_data):
        tmp = self.__parse_monitor_dma_base(raw_data)
        adjust_send_retire_order(tmp)
        normalize_time(tmp)
        adjust_command_order(tmp)
        monitor_sdma.append(tmp)

    def __parse_dyn_extra(self, dyn_extra_data: List, raw_data):
        tmp = parse_dyn_extra(raw_data, True)
        dyn_extra_data.extend(tmp)

    def __parse_global_file(self, filename):
        if not os.path.isfile(filename):
            raise FileNotFoundError(filename)
        max_record_num = 0
        with open(filename, encoding="utf-8") as profile_file:
            for raw_line in profile_file:
                line = raw_line.strip()
                if not line:
                    continue
                if "bmodel" in line:
                    self.is_bmodel = True
                    continue
                if "=" not in line:
                    continue
                key, value = map(str.strip, line.split("=", 1))
                if key == "arch":
                    self.archlib = load_chip_profile(Arch(int(value)))
                elif key == "max_record_num":
                    max_record_num = int(value)
                elif key == "tail_offset_ns":
                    self.tail_offset_ns = int(value)
                elif key.endswith('_record'):
                    record_num = int(value)
                    if max_record_num and record_num >= max_record_num:
                        raise ValueError(f"{key} exceed max_record_num, set max_record_num larger than {max_record_num}.")
                    elif (record_num < 2):
                        raise ValueError(f"{key} profile raw data is invalid, please check if the profile is correct.")
        if self.archlib is None:
            raise ValueError(f"{filename} does not declare an arch")

    def __record_pmu_tail_cycle(self):
        tail_cycle = None
        for engine_pairs in (
            self.bd_pairs,
            self.gdma_pairs,
            self.sdma_pairs,
            self.cdma_pairs,
        ):
            for records in engine_pairs:
                if records:
                    record_tail = records[-1].inst_end_time
                    tail_cycle = record_tail if tail_cycle is None else max(tail_cycle, record_tail)
        self.pmu_tail_cycle = tail_cycle

    def __match_pmu_sections(self, item, core_id):
        init_num = self.archlib.profile_init_cmd_num
        if item.monitor_bd and len(item.monitor_bd[0]):
            # get alignment point
            wait_point = item.monitor_bd[0][init_num - 1]
            self.profile_sync_points.append(wait_point.inst_end_time)
        tiu_cmd = item.dyn_data["tiu"][init_num:]
        gdma_cmd = item.dyn_data["gdma"][init_num:]
        sdma_cmd = item.dyn_data["sdma"][init_num:]
        cdma_cmd = item.dyn_data["cdma"]

        tiu_pmu = item.monitor_bd[0][init_num:]
        gdma_pmu = item.monitor_gdma[0][init_num:]
        sdma_pmu = item.monitor_sdma[0][init_num:]
        # first, match cdma and omit sdma send
        if not all(not sub for sub in cdma_cmd) or core_id == self.archlib.CORE_NUM - 1:
            for port in range(len(self.cdma_pairs)):
                if len(self.cdma_pairs[port]) > 1:
                    # tx_wait (wait, nop)
                    cdma_wait_point = self.cdma_pairs[port][0]
                    sdma_send_point = sdma_pmu[0]
                    self.cdma_pairs[port] = self.cdma_pairs[port][init_num:]
                    _cdma_cmd = cdma_cmd[port][init_num:]
                    # align cdma wait with sdma send
                    offset = cdma_wait_point.inst_end_time - sdma_send_point.inst_end_time
                    for c in self.cdma_pairs[port]:
                        c.inst_start_time -= offset
                        c.inst_end_time -= offset
                    # vsdma_send
                    sdma_pmu = sdma_pmu[1:]
                    sdma_cmd = sdma_cmd[1:]
                    pair_sections(
                        self.cdma_pairs[port],
                        _cdma_cmd,
                        get_cmd_id,
                        descriptor_map=item.des_kv,
                        command_parser=self.cdma_parser,
                    )
                    self.cdma_core_ids[port] = core_id
        # todo bmodel if len(tiu_cmd) == 0?
        # second match the rest engine
        pair_sections(
            tiu_pmu,
            tiu_cmd,
            get_cmd_id,
            descriptor_map=item.des_kv,
            command_parser=self.bdc_parser,
        )
        pair_sections(
            gdma_pmu,
            gdma_cmd,
            get_cmd_id,
            descriptor_map=item.des_kv,
            command_parser=self.gdma_parser,
        )
        pair_sections(
            sdma_pmu,
            sdma_cmd,
            get_cmd_id,
            descriptor_map=item.des_kv,
            command_parser=self.gdma_parser,
        )
        self.bd_pairs.append(tiu_pmu)
        self.gdma_pairs.append(gdma_pmu)
        self.sdma_pairs.append(sdma_pmu)

    def __match_bmodel_sections(self, item):
        init_num = self.archlib.profile_init_cmd_num
        if item.monitor_bd and len(item.monitor_bd[0]):
            # get alignment point
            wait_point = item.monitor_bd[0][init_num - 1]
            self.profile_sync_points.append(wait_point.inst_end_time)
        tiu_pmu = item.monitor_bd[0][init_num:]
        gdma_pmu = item.monitor_gdma[0][init_num:]
        sdma_pmu = item.monitor_sdma[0][init_num:]

        pair_sections(
            tiu_pmu, item.des_bdc, get_cmd_id, drop_unmatched=True
        )
        pair_sections(
            gdma_pmu, item.des_gdma, get_cmd_id, drop_unmatched=True
        )
        pair_sections(
            sdma_pmu, item.des_sdma, get_cmd_id, drop_unmatched=True
        )

        self.bd_pairs.append(tiu_pmu)
        self.gdma_pairs.append(gdma_pmu)
        self.sdma_pairs.append(sdma_pmu)

    def __get_gdma_info(self, monitor_info, reg_info, engine_id=1):
        if reg_info is None:
            return self.archlib.get_dma_info_dyn(monitor_info, reg_info, engine_id)
        if hasattr(reg_info, "extra_info"):
            if hasattr(reg_info, 'detailed_cmd') and engine_id != 4:
                _reg_info = self.gdma_parser.parse(reg_info.detailed_cmd)[0]
                return self.archlib.get_dma_info(monitor_info, _reg_info, engine_id)
            return self.archlib.get_dma_info_dyn(monitor_info, reg_info, engine_id)
        else:
            return self.archlib.get_dma_info(monitor_info, reg_info, engine_id)

    def __get_tiu_info(self, monitor_info, reg_info, engine_id=0):
        if reg_info is None:
            return self.archlib.get_tiu_info_dyn(monitor_info, reg_info)
        if hasattr(reg_info, "extra_info"):
            if hasattr(reg_info, 'detailed_cmd'):
                _reg_info = self.bdc_parser.parse(reg_info.detailed_cmd)[0]
                return self.archlib.get_tiu_info(monitor_info, _reg_info)
            return self.archlib.get_tiu_info_dyn(monitor_info, reg_info)
        else:
            return self.archlib.get_tiu_info(monitor_info, reg_info)
