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
# @Time    : 2023/8/7 11:55
# @Author  : chongqing.zeng@bigtpu.com
# @Project: PerfAI
import os, re
import json
from include.layer import LayerInfo
from include.summary import GlobalInfo, SubnetInfo, TensorInfo, StaticRunNode, jsonObj
from utils.utils import enum_cast, re_key_value, get_memory_type, get_layer_info_by_opcode
from src.common.common import *



class GlobalProfileParser:
    def __init__(self):
        self.in_dir = None
        self.json_filename = "tensor_location.json"
        self.mlir_filename = "final.mlir"
        self.subnet_list = []
        self.layer_list = []

    def _parse_mlir(self, ginfo):
        # update flops/net_name/quant_type from mlir_file
        with open(self.mlir_filename, encoding='utf-8') as f:
            content = f.read()
            match = re.search(
                r'module @(\w+) attributes {.*?module\.FLOPs = (\d+) : i64.*?module\.mode = "([^"]+)"',
                content,
            )
            if match:
                ginfo.net_name = match.group(1)
                ginfo.flops = eval(match.group(2))
                ginfo.quant_type = match.group(3).lower()
                print(f"Net Name: {ginfo.net_name}, FLOPs: {ginfo.flops}, Mode: {ginfo.quant_type}")
            else:
                print("No match found.")

    def parse(self, filename):
        json_file = os.path.join(filename, 'tensor_location.json')
        mlir_file = os.path.join(filename, 'final.mlir')
        if not os.path.exists(json_file):
            # logging.warning("'{}' does not exist, layer info is mssing.".format(json_file))
            return None
        elif not os.path.exists(mlir_file):
            # logging.warning("'{}' does not exist, layer info is mssing.".format(mlir_file))
            return None
        self.json_filename = json_file
        self.mlir_filename = mlir_file
        assert os.path.isfile(json_file) and os.path.isfile(mlir_file)

        ginfo = GlobalInfo()
        subnet_list = ginfo.subnet_list
        subnet_info = None
        layer_list = []
        layer_ins = {}  # tensor_id -> layer_id
        layer_outs = {} # tensor_id -> layer_id
        json_layer_list = []
        with open(self.json_filename, encoding='utf-8') as f:
            data = json.load(f)
            for d in data:
                json_layer = jsonObj()
                json_layer.flie_line = d['file-line']
                json_layer.subnet_id = d['subnet_id']
                json_layer.opcode = d['opcode']
                json_layer.core_id = d['core_id']
                json_layer.bd_ids = [d['tiu_dma_id(before)'][0] + 1, d['tiu_dma_id(after)'][0] + 1]
                json_layer.dma_ids = [d['tiu_dma_id(before)'][1] + 1, d['tiu_dma_id(after)'][1] + 1]
                json_layer.operands = d['operands']
                json_layer.results = d['results']
                json_layer_list.append(json_layer)
        self._parse_mlir(ginfo)
        tensor_id = 1
        for index, j_layer in enumerate(json_layer_list):
            layer_info = LayerInfo()
            layer_info.layer_id = index + 1
            layer_info.core_id = j_layer.core_id
            layer_info.is_local = True
            for idx, in_tensor in enumerate(j_layer.operands):
                if len(in_tensor.keys()) <= 0:
                    continue
                tensor = TensorInfo()
                tensor.tensor_id = tensor_id
                tensor.shape, tensor.dtype = get_memory_type(in_tensor['memory_type'])
                tensor.is_const = 1 if idx > 1 else 0 # Todo, Needs to infer from MLIR.
                tensor.address = in_tensor['address']
                # tensor.gsize = self.int_val("gsize")
                # tensor.nslice = self.int_val("nslice")
                # tensor.hslice = self.int_val("hslice")
                # Todo, global tensor slice info should diliverd by backend.
                if in_tensor['layout'] == 'continuous' and layer_info.is_local is True:
                    j_layer.is_local = False
                layer_ins[tensor_id] = layer_info.layer_id
                layer_info.in_tensors.append(tensor)
                tensor_id += 1
            for out_tensor in j_layer.results:
                if len(out_tensor.keys()) <= 0:
                    continue
                tensor = TensorInfo()
                tensor.tensor_id = tensor_id
                tensor.shape, tensor.dtype = get_memory_type(out_tensor['memory_type'])
                tensor.is_const = 0 # Todo, Needs to infer from MLIR.
                tensor.address = out_tensor['address']
                # tensor.gsize = self.int_val("gsize")
                # tensor.nslice = self.int_val("nslice")
                # tensor.hslice = self.int_val("hslice")
                # Todo, global tensor slice info should diliverd by backend.
                if out_tensor['layout'] == 'continuous' and layer_info.is_local is True:
                    j_layer.is_local = False #Todo
                layer_outs[tensor_id] = layer_info.layer_id
                layer_info.out_tensors.append(tensor)
                tensor_id += 1
            for b_id in range(j_layer.bd_ids[0], j_layer.bd_ids[1]):
                bd_node = StaticRunNode()
                bd_node.bd_id = b_id
                bd_node.core_id = j_layer.core_id
                layer_info.bd_nodes.append(bd_node)
            for g_id in range(j_layer.dma_ids[0], j_layer.dma_ids[1]):
                dma_node = StaticRunNode()
                dma_node.gdma_id = g_id
                dma_node.core_id = j_layer.core_id
                layer_info.gdma_nodes.append(dma_node)
            layer_info.engine_type, layer_info.layer_name = get_layer_info_by_opcode(j_layer.opcode)
            layer_info.layer_type = 'local' if layer_info.is_local else 'global'
            layer_list.append(layer_info)
        # Todo, No case involving multiple subnets yet.
        subnet_info = SubnetInfo()
        subnet_info.subnet_id = 1
        subnet_info.layer_info = []
        subnet_info.layer_list = layer_list
        subnet_list.append(subnet_info)
        return ginfo
