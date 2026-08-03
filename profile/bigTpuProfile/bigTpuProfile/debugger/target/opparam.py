from __future__ import annotations

# ==============================================================================
#
# Copyright (C) 2022 sophon Technologies Inc.  All rights reserved.
#
# TPU-MLIR is licensed under the 2-Clause BSD License except for the
# third-party components.
#
# ==============================================================================


import numpy as np
from typing import Callable, Dict, Union, Tuple, List, Any, TYPE_CHECKING
from ..target_common import (
    MType,
    DType,
    Scalar,
    ExtEnum,
    Layout,
    get_dtype,
    ValueType,
    atomic_reg,
)

from .memmap import *

if TYPE_CHECKING:
    from .context import AKSContext

opparam_converter: Dict[
    str,
    Callable[[atomic_reg], Tuple[List[ValueType], Dict[str, Any], List[ValueType]]],
] = {}


def get_opparam_converter_with_context(context, opparam_converter: dict):
    def wrap(fn):
        def outer(cmd):
            return fn(context, cmd)

        return outer

    new_convert = {}
    for k, v in opparam_converter.items():
        new_convert[k] = wrap(v)  # equals to: new_convert[k] = v(context, cmd)
    return new_convert


def opparam_converter_regitstry(sheet_name):
    def add_converter(fun):
        if sheet_name in opparam_converter:
            raise KeyError(f"{sheet_name} have already registered.")
        opparam_converter[sheet_name] = fun
        return fun

    return add_converter


def get_value(
    context: "AKSContext",
    cmd_type: ["tiu", "dma"] = "tiu",
    address=-1,
    shape=None,
    stride=None,
    dtype=None,
    layout=None,
    is_const=False,
):
    if dtype is None:
        raise ValueError("The dtype of this tensor is invalid.")
    _dtype = dtype
    if not isinstance(dtype, DType):
        _dtype = get_dtype(*dtype)
    if is_const:
        return Scalar(address, _dtype)
    else:
        _layout = layout
        if not isinstance(layout, ExtEnum):
            _layout = Layout(layout)
        return context.MemRef(address, shape, _dtype, stride, _layout)


class TGCR:
    def __init__(self):
        self.regs = dict(
            T5=0,
            T6=0,
            T32=0,
            T33=0,
            T127=0,
        )

    def setter(self, index, value):
        self.regs["T" + str(index)] = value

    def getter(self, index):
        return int(self.regs["T" + str(index)])


tgcr = TGCR()


@opparam_converter_regitstry("sCONV")
def sCONV_converter(context: "AKSContext", reg: sCONV_reg):
    INT8 = 0
    FP16 = 1
    FP32 = 2
    INT16 = 3
    INT32 = 4
    BF16 = 5
    INT4 = 6
    FP8 = 7
    CONV_TF32 = 2

    opd0 = dict(
        address=reg.opd0_addr,
        shape=(reg.res0_n, reg.opd0_c, reg.opd0_h, reg.opd0_w),
        stride=[reg[f"opd0_{i}_str"] for i in "nchw"],
        dtype=(reg.opt_opd0_prec, reg.opt_opd0_sign),
        layout=reg.short_opd0_str,
    )
    opd1 = dict(
        address=reg.opd1_addr,
        shape=[reg.res0_c, reg.opd0_c, reg.opd1_h, reg.opd1_w],
        dtype=(reg.opt_opd0_prec, reg.opt_opd1_sign),
        is_const=reg.opt_opd1_const,
        layout=Layout.alignIC,
    )
    opd2 = dict(
        address=reg.opd2_addr,
        is_const=reg.opt_opd2_const,
        shape=[1, reg.res0_c, 1, 1],
        layout=Layout.compact,
        dtype=(DType.i32, reg.opt_opd2_sign),
    )
    opd3 = dict(
        address=reg.opd3_addr,
        is_const=reg.opt_opd3_const,
        shape=[1, reg.res0_c, 1, 2],
        layout=Layout.compact,
        dtype=(reg.opt_opd0_prec, reg.opt_opd0_sign),
    )
    opd4 = dict(
        address=tgcr.getter(5),
        is_const=reg.opt_opd4_const,
        shape=[1, reg.res0_c, 1, 1],
        layout=Layout.compact,
        dtype=(DType.i16, 1),
    )
    opd5 = dict(
        address=tgcr.getter(6),
        shape=[1, reg.res0_c, 1, 2],
        is_const=reg.opt_opd5_const,
        layout=Layout.compact,
        dtype=(DType.i32, 1),
    )
    res0 = dict(
        address=reg.res0_addr,
        shape=[reg[f"res0_{i}"] for i in "nchw"],
        dtype=(
            reg.opt_res0_prec,
            reg.opt_opd0_sign or reg.opt_opd1_sign or reg.opt_opd2_sign,
        ),
        layout=Layout.alignEU,
    )
    opds = [opd0, opd1, opd2, opd3, opd4, opd5]
    opd1["layout"] = Layout.alignIC

    results = [get_value(context, **res0)]

    attr = dict(
        kernel=[reg.opd1_h, reg.opd1_w],
        stride=[reg.res_op_y_str, reg.res_op_x_str],
        in_zero=[reg.opd0_y_ins0, reg.opd0_x_ins0],
        ke_zero=[reg.opd1_y_ins0, reg.opd1_x_ins0],
        opt_kernel_rotate=bool(reg.opt_kernel_rotate),
        pad_mode=reg.pad_mode,
        pad=[reg[f"opd0_{x}_pad"] for x in ("up", "dn", "lf", "rt")],
        opt_res_add=bool(reg.opt_res_add),
        do_relu=bool(reg.opt_relu),
        sym_range=bool(reg.sym_range),
        do_rq=bool(reg.opt_rq),
        round_mode=reg.opd2_n_str,
    )
    if not bool(reg.opt_rq):
        opds.remove(opd4)
        opds.remove(opd5)
    else:
        if bool(reg.opt_opd4_const):
            opds.remove(opd4)
            attr["kzp"] = int(tgcr.getter(5))
        if bool(reg.opt_opd5_const):
            opds.remove(opd5)
            attr["multiplier"] = tgcr.getter(6)
            attr["shift"] = int(np.binary_repr(tgcr.getter(32), width=32)[-8:-1], 2)
            attr["yzp"] = int(np.binary_repr(tgcr.getter(33), width=32)[-15:-1], 2)
    operands = [get_value(context, **x) for x in opds]
    return (results, attr, operands)


@opparam_converter_regitstry("sCONV_BW")
def sCONV_BW_converter(context: "AKSContext", reg: sCONV_BW_reg):
    INT8 = 0
    FP16 = 1
    FP32 = 2
    INT16 = 3
    INT32 = 4
    BF16 = 5
    INT4 = 6
    FP8 = 7

    opd0 = dict(
        address=reg.opd0_addr,
        shape=(reg.opd0_n, reg.opd0_c, reg.opd0_h, reg.opd0_w),
        stride=[reg[f"opd0_{i}_str"] for i in "nchw"],
        dtype=(reg.opt_opd0_prec, reg.opt_opd0_sign),
        layout=reg.short_opd0_str,
    )
    opd1 = dict(
        address=reg.opd1_addr,
        is_const=reg.opt_opd1_const,
        shape=[reg.res0_c, reg.opd0_c, reg.opd1_h, reg.opd1_w],
        dtype=(reg.opt_opd1_prec, reg.opt_opd1_sign),
        layout=Layout.alignIC,
    )
    opd2 = dict(
        address=reg.opd2_addr,
        is_const=reg.opt_opd2_const,
        shape=[1, reg.res0_c, 1, 1],
        dtype=(reg.opt_opd2_prec, reg.opt_opd2_sign),
        layout=Layout.compact,
    )
    res0 = dict(
        address=reg.res0_addr,
        shape=(reg.opd0_n, reg.res0_c, reg.res0_h, reg.res0_w),
        dtype=(reg.opt_res0_prec, reg.opt_res0_sign),
        layout=Layout.alignEU,
    )
    opds = [opd0, opd1, opd2]
    opd1["layout"] = Layout.alignIC

    results = [get_value(context, **res0)]

    attr = dict(
        kernel=[reg.opd1_h, reg.opd1_w],
        stride=[reg.res_op_y_str, reg.res_op_x_str],
        in_zero=[reg.opd0_y_ins0, reg.opd0_x_ins0],
        ke_zero=[reg.opd1_y_ins0, reg.opd1_x_ins0],
        opt_kernel_rotate=bool(reg.opt_kernel_rotate),
        pad_mode=reg.pad_mode,
        pad=[reg[f"opd0_{x}_pad"] for x in ("up", "dn", "lf", "rt")],
        opt_res_add=bool(reg.opt_res_add),
        do_relu=bool(reg.opt_relu),
        sym_range=bool(reg.sym_range),
        do_rq=bool(reg.opt_rq),
        round_mode=reg.opd2_n_str,
    )

    operands = [get_value(context, **x) for x in opds]
    return (results, attr, operands)


@opparam_converter_regitstry("sMM")
def sMM_converter(context: "AKSContext", reg: sMM_reg):
    L_row = reg.opd0_n
    L_col = reg.opd0_w * (reg.opd0_c - 1) + reg.opd1_w
    R_col = reg.res0_c * reg.res0_w
    opd0 = dict(
        address=reg.opd0_addr,
        dtype=(reg.opt_opd0_prec, reg.opt_opd0_sign),
        shape=(L_row, L_col),
        layout=Layout.matrix(reg.opd0_w),
        is_const=reg.opt_opd0_const,
    )
    if reg.opt_left_tran:
        L_row, L_col = L_col, L_row
    res0 = dict(
        address=reg.res0_addr,
        dtype=(reg.opt_res0_prec, 1),
        shape=(L_row, R_col),
        layout=Layout.matrix(reg.res0_w),
    )
    opd1 = dict(
        address=reg.opd1_addr,
        dtype=(reg.opt_opd0_prec, reg.opt_opd1_sign),
        shape=(L_col, R_col),
        layout=Layout.matrix(reg.res0_w),
    )
    opd2 = dict(
        address=reg.opd2_addr,
        is_const=reg.opt_opd2_const,
        shape=(1, R_col),
        layout=Layout.matrix(reg.res0_w),
    )
    attr = dict(
        l_trans=bool(reg.opt_left_tran),
        res_add=bool(reg.opt_res_add),
        do_relu=bool(reg.opt_relu),
        sym_range=bool(reg.sym_range),
        do_rq=bool(reg.opt_rq),
        round_mode=reg.opd2_n_str,
        multiplier=tgcr.getter(6),
        shift=int(np.binary_repr(tgcr.getter(32), width=32)[-7:-1], 2),
        yzp=int(np.binary_repr(tgcr.getter(33), width=32)[-16:-1], 2),
    )
    assert reg.tsk_eu_typ == 1  # mm_normal
    if reg.opt_opd0_prec == DType.f32:
        opd2["dtype"] = opd0["dtype"]  # bias
    else:
        opd2["dtype"] = (DType.i8, 1)  # shift

    operands = [get_value(context, **x) for x in (opd0, opd1, opd2)]
    results = [get_value(context, **res0)]

    return (results, attr, operands)


@opparam_converter_regitstry("sMM2")
def sMM2_converter(context: "AKSContext", reg: sMM2_reg):
    L_row = reg.res0_c
    L_col = reg.opd1_c
    l_trans, r_trans = False, False
    if reg.tsk_eu_typ == 5:
        L_col = reg.opd1_w
        l_trans, r_trans = False, True
    elif reg.tsk_eu_typ == 6:
        L_row = reg.opd1_w
        L_col = reg.res0_c
        l_trans, r_trans = True, True
    opd0 = dict(
        address=reg.opd0_addr,
        dtype=(reg.opt_opd0_prec, reg.opt_opd0_sign),
        shape=(L_row, L_col),
        layout=Layout.matrix2,
        is_const=reg.opt_opd0_const,
    )
    res0 = dict(
        address=reg.res0_addr,
        dtype=(reg.opt_res0_prec, 1),
        shape=(reg.res0_c, reg.res0_w),
        layout=Layout.matrix2,
    )
    opd1 = dict(
        address=reg.opd1_addr,
        dtype=(reg.opt_opd0_prec, reg.opt_opd1_sign),
        shape=(reg.opd1_c, reg.opd1_w),
        layout=Layout.matrix2,
    )
    opd2 = dict(
        address=reg.opd2_addr,
        is_const=reg.opt_opd2_const,
        shape=(1, info.NPU_NUM, 1, reg.res0_w),
        layout=Layout.alignEU,
    )
    opd4 = dict(
        address=tgcr.getter(5),
        is_const=reg.opt_opd4_const,
        dtype=DType.si16,
        shape=(1, info.NPU_NUM, 1, reg.res0_w),
        layout=Layout.alignEU,
    )
    opd5 = dict(
        address=tgcr.getter(6),
        is_const=reg.opt_opd5_const,
        dtype=DType.si32,
        shape=(1, info.NPU_NUM, reg.res0_w, 2),
    )
    attr = dict(
        l_trans=bool(l_trans),
        r_trans=bool(r_trans),
        res_add=bool(reg.opt_res_add),
        do_relu=bool(reg.opt_relu),
        sym_range=bool(reg.sym_range),
        do_rq=bool(reg.opt_rq),
        round_mode=reg.opd2_n_str,
    )
    opds = []
    if reg.tsk_eu_typ in [4, 5]:
        opds = [opd0, opd1, opd2, opd4, opd5]
    else:
        opd2["shape"] = (1, reg.res0_w, 1, 1)
        opd2["layout"] = Layout.compact
        opd4["shape"] = (1, reg.res0_w, 1, 1)
        opd4["layout"] = Layout.compact
        opd5["shape"] = (1, reg.res0_w, 1, 2)
        opd5["layout"] = Layout.compact
    if reg.opt_opd0_prec in (0, 6):
        opd2["dtype"] = (DType.i32, reg.opt_opd2_sign)
        opd5["dtype"] = DType.i32
        opds = [opd0, opd1, opd2, opd4, opd5]
        if reg.opt_opd4_const:
            opds.remove(opd4)
            attr["zp"] = int(np.binary_repr(tgcr.getter(5), width=32)[-16:-1], 2)
        if reg.opt_opd5_const:
            opds.remove(opd5)
            attr["multiplier"] = tgcr.getter(6)
            attr["shift"] = int(np.binary_repr(tgcr.getter(32), width=32)[-8:-1], 2)
            attr["yzp"] = int(np.binary_repr(tgcr.getter(33), width=32)[-16:-1], 2)
    else:
        opd2["dtype"] = DType.f32
        opds = [opd0, opd1, opd2]
    operands = [get_value(context, **x) for x in opds]
    results = [get_value(context, **res0)]

    return (results, attr, operands)


@opparam_converter_regitstry("sCMP")
def sCMP_converter(context: "AKSContext", reg: sCMP_reg):
    shape = tuple(reg[f"res0_{d}"] for d in "nchw")
    opd0 = dict(
        address=reg.opd0_addr,
        dtype=(reg.opt_opd0_prec, reg.opt_opd0_sign),
        shape=shape,
        layout=reg.short_opd0_str,
        is_const=reg.opt_opd0_const,
    )
    res0 = dict(
        address=reg.res0_addr,
        dtype=opd0["dtype"],
        shape=shape,
        layout=Layout.alignEU,
    )
    res1 = dict(
        address=reg.res1_addr,
        dtype=DType(reg.opt_opd2_prec),
        shape=shape,
        layout=Layout.alignEU,
    )
    opd1 = dict(
        address=reg.opd1_addr,
        dtype=opd0["dtype"],
        shape=shape,
        layout=reg.short_opd1_str,
        is_const=reg.opt_opd1_const,
    )
    opd2 = dict(
        address=reg.opd2_addr,
        dtype=DType(reg.opt_opd2_prec),
        shape=shape,
        layout=Layout.alignEU,
        is_const=reg.opt_opd2_const,
    )
    opd3 = dict(
        address=reg.opd3_addr,
        dtype=DType(reg.opt_opd2_prec),
        shape=shape,
        layout=Layout.alignEU,
        is_const=reg.opt_opd3_const,
    )

    rets = [res0, res1]
    if reg.short_opd0_str == Layout.stride:
        opd0["stride"] = (0, 0, 0, 0)
    if reg.short_opd1_str == Layout.stride:
        opd1["stride"] = (0, 0, 0, 0)
    if reg.tsk_eu_typ in (23, 24, 26):
        res0["dtype"] = res1["dtype"]
        rets = [res0]

    operands = [get_value(context, **x) for x in (opd0, opd1, opd2, opd3)]
    results = [get_value(context, **x) for x in rets]

    return (results, {}, operands)


@opparam_converter_regitstry("sSFU")
def sSFU_converter(context: "AKSContext", reg: sSFU_reg):
    shape = tuple(reg[f"res0_{d}"] for d in "nchw")
    opd0 = dict(
        address=reg.opd0_addr,
        dtype=(reg.opt_opd0_prec, 1),
        shape=shape,
        layout=Layout.alignEU,
    )
    res0 = dict(
        address=reg.res0_addr,
        dtype=(reg.opt_res0_prec, 1),
        shape=shape,
        layout=Layout.alignEU,
    )
    opd1 = dict(
        address=reg.opd1_addr,
        dtype=(reg.opt_opd0_prec, 1),
        shape=(1, min(shape[1], 64), 1, reg.opd1_n),
        stride=(0, 0, reg.opd1_n, 1),
        layout=Layout.stride,
    )
    attr = {}
    opd_num = 1
    if reg.tsk_eu_typ == 17:
        attr = dict(rsqrt_n=reg.opd2_n_str + 1)
    elif reg.tsk_eu_typ in [12, 13]:
        opd_num = 2

    operands = [get_value(context, **x) for x in (opd0, opd1)[:opd_num]]
    results = [get_value(context, **res0)]

    return (results, attr, operands)


@opparam_converter_regitstry("sLIN")
def sLIN_converter(context: "AKSContext", reg: sLIN_reg):
    shape = tuple(reg[f"res0_{d}"] for d in "nchw")
    c = shape[1]
    opd0 = dict(
        address=reg.opd0_addr,
        dtype=(reg.opt_res0_prec, 1),
        shape=shape,
        layout=Layout.alignEU,
    )
    res0 = dict(
        address=reg.res0_addr,
        dtype=(reg.opt_res0_prec, 1),
        shape=shape,
        layout=Layout.alignEU,
    )
    opd1 = dict(
        address=reg.opd1_addr,
        dtype=opd0["dtype"],
        shape=(1, c, 1, 1),
        layout=Layout.compact,
        is_const=reg.opt_opd1_const,
    )
    opd2 = dict(
        address=reg.opd2_addr,
        dtype=opd0["dtype"],
        shape=(1, c, 1, 1),
        layout=Layout.compact,
        is_const=reg.opt_opd2_const,
    )

    opd_num = 2
    if reg.tsk_eu_typ == 1:
        opd_num = 3

    operands = [get_value(context, **x) for x in (opd0, opd1, opd2)[:opd_num]]
    results = [get_value(context, **res0)]

    return (results, {}, operands)


@opparam_converter_regitstry("sVC")
def sVC_converter(context: "AKSContext", reg: sVC_reg):
    n = (reg.opd0_c - 1) * reg.opd0_w + reg.opd1_w
    opd0 = dict(
        address=reg.opd0_addr,
        dtype=(reg.opt_opd0_prec, reg.opt_opd0_sign),
        shape=(1, reg.opd0_c, 1, reg.opd0_w),
        layout=Layout.alignEU,
    )

    res0 = dict(
        address=reg.res0_addr,
        dtype=(reg.opt_res0_prec, 1),
        shape=(n, reg.res0_c, 1, reg.res0_w),
        layout=Layout.alignEU,
    )
    opd1 = dict(
        address=reg.opd1_addr,
        dtype=(reg.opt_opd1_prec, reg.opt_opd1_sign),
        shape=(1, reg.res0_c, 1, reg.res0_w),
        layout=Layout.alignEU,
    )
    attr = {}
    if reg.tsk_eu_typ == 23:
        attr = dict(round_mode=reg.opd2_n_str)

    operands = [get_value(context, **x) for x in (opd0, opd1)]
    results = [get_value(context, **res0)]

    return (results, attr, operands)


def restore_org_shape(operand_def):
    shape = list(operand_def["shape"])
    if operand_def["layout"] == Layout.stride:
        stride = operand_def["stride"]
        assert len(shape) == len(stride)
        for i, t in enumerate(stride):
            if t == 0:
                shape[i] = 1
    return shape


@opparam_converter_regitstry("sAR")
def sAR_converter(context: "AKSContext", reg: sAR_reg):
    n, c, h, w = (reg[f"res0_{d}"] for d in "nchw")
    # round mm
    opd0 = dict(
        address=reg.opd0_addr,
        dtype=(reg.opt_opd0_prec, reg.opt_opd0_sign),
        shape=(n, c, h, w),
        stride=tuple(reg[f"opd0_{d}_str"] for d in "nchw"),
        layout=reg.short_opd0_str,
        is_const=reg.opt_opd0_const,
    )
    res0 = dict(
        address=reg.res0_addr,
        dtype=(reg.opt_res0_prec, reg.opt_opd0_sign or reg.opt_opd1_sign),
        shape=(n, c, h, w),
        stride=tuple(reg[f"res0_{d}_str"] for d in "nchw"),
        layout=reg.short_res0_str,
    )
    opd1 = dict(
        address=reg.opd1_addr,
        dtype=(reg.opt_opd1_prec, reg.opt_opd1_sign),
        shape=(n, c, h, w),
        stride=tuple(reg[f"opd1_{d}_str"] for d in "nchw"),
        layout=reg.short_opd1_str,
        is_const=reg.opt_opd1_const,
    )
    opd2 = dict(
        address=reg.opd2_addr,
        dtype=(reg.opt_opd2_prec, reg.opt_opd2_sign),
        shape=(1, c, 1, 1),
        layout=Layout.compact,
        is_const=reg.opt_opd2_const,
    )

    if reg.tsk_eu_typ == 14:  # DATA_CONVERT
        res0["dtype"] = (reg.opt_res0_prec, reg.opt_opd2_sign)

    if reg.tsk_eu_typ == 12:
        attr = dict(iter=reg.opd2_n_str + 1)
    else:
        attr = dict(round_mode=reg.opd2_n_str)

    opd0["shape"] = restore_org_shape(opd0)
    opd1["shape"] = restore_org_shape(opd1)

    opd_num = reg.tsk_opd_num
    operands = [get_value(context, **x) for x in (opd0, opd1, opd2)[:opd_num]]
    results = [get_value(context, **res0)]
    return (results, attr, operands)


@opparam_converter_regitstry("sPorD")
def sPorD_converter(context: "AKSContext", reg: sPorD_reg):
    n, c, h, w = (reg[f"res0_{d}"] for d in "nchw")
    # round mm
    opd0 = dict(
        address=reg.opd0_addr,
        dtype=(reg.opt_opd0_prec, reg.opt_opd0_sign),
        shape=(n, c, reg.opd0_h, reg.opd0_w),
        layout=Layout.alignEU,
    )
    res0 = dict(
        address=reg.res0_addr,
        dtype=(reg.opt_res0_prec, reg.opt_opd0_sign),
        shape=(n, c, h, w),
        layout=Layout.alignEU,
    )
    opd1 = dict(
        address=reg.opd1_addr,
        dtype=(reg.opt_opd0_prec, reg.opt_opd1_sign),
        shape=(1, c, reg.opd1_h, reg.opd1_w),
        layout=Layout.compact,
        is_const=reg.opt_opd1_const,
    )
    opd2 = dict(
        address=reg.opd2_addr,
        dtype=(reg.opt_opd0_prec, reg.opt_opd2_sign),
        shape=(1, c, 1, 1),
        layout=Layout.compact,
        is_const=reg.opt_opd2_const,
    )
    # padding
    opd3 = dict(
        address=reg.opd3_addr,
        dtype=(reg.opt_opd0_prec, reg.opt_opd0_sign),
        shape=(1, c, 1, 2),
        layout=Layout.compact,
        is_const=reg.opt_opd3_const,
    )

    opd5 = dict(
        address=tgcr.getter(6),
        dtype=DType.si32,
        shape=(1, c, 1, 2),
        layout=Layout.compact,
        is_const=reg.opt_opd5_const,
    )

    opds = []
    if reg.tsk_eu_typ in [0, 4, 3, 1]:
        # if reg.tsk_eu_typ in [0, 1]:
        if reg.tsk_eu_typ == 0:
            opds = [opd0, opd1, opd2, opd3, opd5]
        elif reg.tsk_eu_typ == 1:
            opds = [opd0, opd1, opd3, opd5]
        else:
            opds = [opd0, opd3, opd5]
    else:
        opd3["shape"] = (1, c, 1, 4)
        opd3["dtype"] = DType.ui16
        if reg.tsk_eu_typ in [5, 6]:
            opds = [opd0, opd1, opd2, opd3, opd5]
        else:
            opds = [opd0, opd2, opd3, opd5]
    attr = dict(
        kernel=[reg.opd1_h, reg.opd1_w],
        stride=[reg.res_op_y_str, reg.res_op_x_str],
        in_zero=[reg.opd0_y_ins0, reg.opd0_x_ins0],
        ke_zero=[reg.opd1_y_ins0, reg.opd1_x_ins0],
        opt_kernel_rotate=bool(reg.opt_kernel_rotate),
        pad_mode=reg.pad_mode,
        pad=[reg[f"opd0_{x}_pad"] for x in ("up", "dn", "lf", "rt")],
        round_mode=reg.opd2_n_str,
        shift=np.uint32([reg.res1_addr]).view(np.int8)[0],
    )
    if not bool(reg.opt_rq):
        opds.remove(opd5)
    else:
        if bool(reg.opt_opd5_const):
            opds.remove(opd5)
            attr["multiplier"] = tgcr.getter(6)
            attr["shift"] = int(np.binary_repr(tgcr.getter(32), width=32)[-8:-1], 2)
            attr["yzp"] = int(np.binary_repr(tgcr.getter(33), width=32)[-16:-1], 2)

    operands = [get_value(context, **x) for x in opds]
    results = [get_value(context, **res0)]

    return (results, attr, operands)


def cw_trans_reg_format(context, reg: sCW_sBC_reg):
    n, c, h, w = (reg[f"res0_{d}"] for d in "nchw")
    opd0 = dict(
        address=reg.opd0_addr,
        dtype=DType(reg.opt_res0_prec),
        shape=(n, w, h, c),
        layout=Layout.alignEU,
    )
    res0 = dict(
        address=reg.res0_addr,
        dtype=DType(reg.opt_res0_prec),
        shape=(n, c, h, w),
        layout=Layout.alignEU,
    )

    if reg.tsk_eu_typ == 0:  # cw_ts
        opd0["layout"] = Layout.alignLine

    operands = [get_value(context, **opd0)]
    results = [get_value(context, **res0)]

    return (results, {}, operands)


def bc_reg_format(context, reg: sCW_sBC_reg):
    n, c, h, w = (reg[f"res0_{d}"] for d in "nchw")
    opd0 = dict(
        address=reg.opd0_addr,
        dtype=DType(reg.opt_res0_prec),
        shape=(n, c, h, w),
        layout=Layout.alignEU,
    )
    res0 = dict(
        address=reg.res0_addr,
        dtype=DType(reg.opt_res0_prec),
        shape=(n, c, h, w),
        layout=Layout.alignEU,
    )

    if reg.tsk_eu_typ == 3:
        opd0["shape"] = (n, 1, h, w)
    if reg.tsk_eu_typ == 4:
        res0["shape"] = (1, c, 1, w)
    if reg.tsk_eu_typ == 5:
        res0["shape"] = (1, c, 1, 1)
        res0["layout"] = Layout.compact

    operands = [get_value(context, **opd0)]
    results = [get_value(context, **res0)]

    return (results, {}, operands)


@opparam_converter_regitstry("sCW&sBC")
def sCW_sBC_converter(context: "AKSContext", reg: sCW_sBC_reg):
    if reg.tsk_eu_typ in (0, 1):
        return cw_trans_reg_format(context, reg)
    else:
        return bc_reg_format(context, reg)


@opparam_converter_regitstry("sRQ&sDQ")
def sRQ_sDQ_converter(context: "AKSContext", reg: sRQ_sDQ_reg):
    n, c, h, w = (reg[f"res0_{d}"] for d in "nchw")
    opd0 = dict(
        address=reg.opd0_addr,
        dtype=(reg.opt_opd0_prec, reg.opt_opd0_sign),
        shape=(n, c, h, w),
        layout=Layout.alignEU,
    )
    res0 = dict(
        address=reg.res0_addr,
        dtype=(reg.opt_res0_prec, reg.opt_opd2_sign),
        shape=(n, c, h, w),
        layout=Layout.alignEU,
    )
    opd1 = dict(
        address=reg.opd1_addr,
        is_const=reg.opt_opd1_const,
        layout=Layout.compact,
    )
    opds = []
    if reg.tsk_eu_typ == 0:  # rq_0
        opd1["dtype"] = DType.f32
        opd1["shape"] = (1, c, 1, 1)
        opd2 = dict(opd1)
        opd2["address"] += 4
        if opd1["is_const"]:
            opd2["address"] = reg.opd2_addr
        opds = [opd0, opd1, opd2]
    elif reg.tsk_eu_typ == 1:  # rq_1
        opd1["dtype"] = DType.si32
        opd1["shape"] = (1, c, 1, 1)
        opd2 = dict(opd1)
        opd2["address"] += 4
        opd3 = dict(opd2)
        opd3["address"] += 4
        if opd1["is_const"]:
            opd2["address"] = reg.opd2_addr
            opd2["dtype"] = DType.si8
            opd3["address"] = reg.opd2_addr // 2**8
            opd3["dtype"] = DType.si16
        opds = [opd0, opd1, opd2, opd3]
    elif reg.tsk_eu_typ == 3:  # dq_0
        opd1["dtype"] = DType.si16
        opd1["shape"] = (1, c, 1, 1)
        opd2 = dict(opd1)
        opd2["address"] += 4
        opd2["shape"] = (1, c, 1, 1)
        opd2["dtype"] = DType.f32
        if opd1["is_const"]:
            opd2["address"] = reg.opd2_addr
        opds = [opd0, opd1, opd2]
    elif reg.tsk_eu_typ == 4:  # dq_1
        opd1["dtype"] = DType.si32  # multiplier
        opd1["shape"] = (1, c, 1, 1)
        opd2 = dict(opd1)  # shift
        opd2["dtype"] = DType.si8
        opd2["address"] += 4
        opd3 = dict(opd2)  # yzp
        opd3["address"] += 4
        opds = [opd0, opd1, opd2, opd3]
        if opd1["is_const"]:
            opd1["dtype"] = (DType.i16, reg.opt_opd0_sign)  # multiplier
            opd2["address"] = reg.opd2_addr // 2**16  # shift
            opd2["dtype"] = DType.si16
            opd3["address"] = reg.opd2_addr  # scale
            opd3["dtype"] = DType.si8
            opds = [opd0, opd1, opd3, opd2]
    else:
        raise KeyError("Should not be here.")
    attr = dict(round_mode=reg.opd2_n_str)
    operands = [get_value(context, **x) for x in opds]
    results = [get_value(context, **res0)]

    return (results, attr, operands)


@opparam_converter_regitstry("sSG")
def sSG_converter(context: "AKSContext", reg: sSG_reg):
    n, c, h, w = (reg[f"res0_{d}"] for d in "nchw")
    opd0 = dict(
        address=reg.opd0_addr,
        dtype=DType(reg.opt_opd0_prec),
        layout=reg.short_opd0_str,
    )
    res0 = dict(
        address=reg.res0_addr,
        dtype=DType(reg.opt_res0_prec),
        shape=(n, c, h, w),
        layout=Layout.alignEU,
    )
    opd1 = dict(
        address=reg.opd0_addr,
        dtype=(reg.opt_opd1_prec, 0),
        layout=Layout.alignEU,
    )
    opds = [opd0, opd1]
    rets = [res0]
    if reg.tsk_eu_typ in [0, 3]:
        opd0["shape"] = (n, c, 1, reg.opd0_w)
        opd1["shape"] = (1, reg.opd1_c, 1, 1)
        opd1["layout"] = Layout.compact
    elif reg.tsk_eu_typ in [1, 4]:
        opd0["shape"] = (n, c, reg.opd0_h, reg.opd0_w)
        opd1["shape"] = (1, reg.opd1_c, 1, 1)
        opd1["layout"] = Layout.compact
    elif reg.tsk_eu_typ in [5, 6, 13, 14]:
        opd0["shape"] = (1, c, reg.opd0_h, reg.opd0_w)
        if reg.short_opd0_str != 0:
            opd0["layout"] = Layout.T4
        opd1["shape"] = (n, c, 1, reg.opd1_w)
        opd1["layout"] = Layout.alignEU
    elif reg.tsk_eu_typ in [8, 15]:
        opd0["shape"] = (1, c, 1, reg.opd0_w)
        if reg.short_opd0_str != 0:
            opd0["layout"] = Layout.T4
        opd1["shape"] = (n, c, 1, reg.opd1_w)
        res1 = dict(
            address=reg.res1_addr,
            dtype=DType.si16,
            shape=(n, c, 1, 1),
            layout=Layout.compact,
        )
        rets = [res0, res1]
    elif reg.tsk_eu_typ in [9, 16]:
        opd0["shape"] = (n, c, 1, reg.opd0_w)
        opds = [opd0]
        res1 = dict(
            address=reg.res1_addr,
            dtype=DType.si16,
            shape=(n, c, 1, 1),
            layout=Layout.compact,
        )
        rets = [res0, res1]
    else:
        raise KeyError("Should not be here.")

    attr = dict(
        limit_enable=bool(reg.opt_opd3_const),
        fill_const=bool(reg.opt_opd2_const),
        const_value=reg.opd2_addr,
    )
    operands = [get_value(context, **x) for x in opds]
    results = [get_value(context, **x) for x in rets]

    return (results, attr, operands)


@opparam_converter_regitstry("sSGL")
def sSGL_converter(context: "AKSContext", reg: sSGL_reg):
    n, c, h, w = (reg[f"res0_{d}"] for d in "nchw")
    opd0 = dict(
        address=reg.opd0_addr,
        dtype=DType(reg.opt_res0_prec),
        layout=Layout.stride,
    )
    res0 = dict(
        address=reg.res0_addr,
        dtype=DType(reg.opt_res0_prec),
        shape=(n, c, h, w),
        layout=Layout.stride,
    )
    opd1 = dict(
        address=reg.opd0_addr,
        dtype=(reg.opt_opd1_prec, 0),
        layout=Layout.compact,
    )
    opd0["shape"] = (1, c, reg.opd0_h, w)
    if reg.short_opd0_str == 3:
        opd0["layout"] = Layout.alignLine
    else:
        opd0["layout"] = Layout.T4

    rets = [res0]
    assert reg.tsk_eu_typ in [17, 18]
    opd1_h = reg.res0_h if reg.tsk_eu_typ == 17 else reg.opd0_h
    opd1["shape"] = (n, c, opd1_h, 1)
    res0["layout"] = Layout.alignLine

    attr = dict(
        limit_enable=bool(reg.opt_opd3_const),
        fill_const=bool(reg.opt_opd2_const),
        const_value=reg.opd2_addr,
    )

    operands = [get_value(context, **x) for x in (opd0, opd1)]
    results = [get_value(context, **x) for x in rets]

    return (results, attr, operands)


@opparam_converter_regitstry("SYS_TR_ACC")
def SYS_TR_ACC_converter(context: "AKSContext", reg):
    def int2bin(x, width):
        return np.binary_repr(x, width=width)

    des_imm0 = int2bin(reg.imm0, 32)
    des_imm0 = int(des_imm0, 2)
    des_imm1 = int2bin(reg.imm1, 64)
    des_imm1_h32 = int(des_imm1[0:32], 2)
    des_imm1_l32 = int(des_imm1[32:64], 2)
    tgcr.setter(reg.reg_idx0, des_imm0)
    tgcr.setter(reg.reg_idx1, des_imm1_l32)
    tgcr.setter(reg.reg_idx2, des_imm1_h32)
    attrs = dict(
        reg_idx0=reg.reg_idx0,
        reg_idx1=reg.reg_idx1,
        reg_idx2=reg.reg_idx2,
        des_imm0=des_imm0,
        des_imm1_h32=des_imm1_h32,
        des_imm1_l32=des_imm1_l32,
    )
    return ([], attrs, [])


@opparam_converter_regitstry("LAR")
def LAR_converter(context: "AKSContext", reg):
    shape = (1, reg.res0_c, 1, reg.res0_w)
    result = dict(
        address=reg.res0_addr,
        dtype=DType(reg.opt_res0_prec),
        shape=shape,
        layout=Layout.stride,
        stride=(0, reg.res0_w, 0, 1),
    )
    operands = [
        dict(
            address=reg.opd0_addr,
            dtype=(reg.opt_opd0_prec, reg.opt_opd0_sign),
            shape=shape,
            layout=Layout.stride,
            stride=(0, reg.res0_w, 0, reg.opd0_w_str),
        )
    ]
    if reg.tsk_opd_num >= 2:
        operands.append(
            dict(
                address=reg.opd1_addr,
                dtype=(reg.opt_opd1_prec, reg.opt_opd1_sign),
                shape=shape,
                layout=Layout.stride,
                stride=(0, reg.res0_w, 0, reg.opd1_w_str),
            )
        )
    if reg.tsk_opd_num >= 3:
        operands.append(
            dict(
                address=reg.opd2_addr,
                dtype=DType(reg.opt_res0_prec),
                shape=shape,
                layout=Layout.compact,
                is_const=bool(reg.opt_opd2_const),
            )
        )

    attrs = {
        "round_mode": reg.opd2_n_str,
        "start_lane": reg.start_tid,
    }
    return (
        [get_value(context, **result)],
        attrs,
        [get_value(context, **operand) for operand in operands],
    )


@opparam_converter_regitstry("SYS")
def SYS_converter(context: "AKSContext", reg):
    des_imm = reg.imm
    msg_id = des_imm & 0x1FF
    cnt = (des_imm >> 16) & 0x7F
    attrs = {}
    if reg.tsk_eu_typ in (8, 9):
        attrs = dict(
            msg_id=msg_id,
            cnt=cnt,
        )
    elif reg.tsk_eu_typ == 31:
        attrs = dict()
    elif reg.tsk_eu_typ not in {0, 1, 2, 3, 4, 5, 8, 9, 30, 31}:
        raise KeyError(f"sys cmd with tsk_eu_typ {reg.tsk_eu_typ} Should not be here.")
    return ([], attrs, [])


@opparam_converter_regitstry("RAND")
def RAND_converter(context: "AKSContext", reg):
    shape = (1, reg.res0_c, 1, reg.res0_w)
    result = dict(
        address=reg.res0_addr,
        dtype=DType(reg.opt_res0_prec),
        shape=shape,
        layout=Layout.compact,
    )
    operands = []
    if reg.tsk_eu_typ == 2:
        states = dict(
            address=reg.opd0_addr,
            dtype=DType.ui32,
            shape=(1, info.NPU_NUM, 1, 4),
            layout=Layout.compact,
        )
        operands.append(get_value(context, **states))

    results = [get_value(context, **result)]
    if reg.opt_res_add:
        states_out = dict(
            address=reg.res1_addr,
            dtype=DType.ui32,
            shape=(1, info.NPU_NUM, 1, 4),
            layout=Layout.compact,
        )
        results.append(get_value(context, **states_out))

    attrs = {
        "c_offset": reg.opd2_n_str,
        "jump_count": reg.opd2_c_str,
    }
    return (results, attrs, operands)


def dma_addr(H, L):
    addr = H * 2**32 + L
    tag = (addr >> 40) & 0x1f
    if tag == 0x0 :     # for workround
        addr =  addr | (0x1 << 40)
    return addr


def extended_dma_addr(reg, prefix):
    """Build an address, including optional document-defined bits [49:40]."""

    low = getattr(reg, f"{prefix}_start_addr_l32")
    high = getattr(
        reg,
        f"{prefix}_start_addr_h13",
        getattr(reg, f"{prefix}_start_addr_h8", 0),
    )
    ext = getattr(
        reg,
        f"{prefix}_start_addr_ext_h10",
        getattr(reg, f"{'source' if prefix == 'src' else prefix}_start_addr_ext_h10", None),
    )
    if ext is None:
        return dma_addr(high, low)
    return (ext << 40) | ((high & 0xFF) << 32) | low


def dma_reg_fmt_base(reg):
    if all(
        hasattr(reg, field)
        for field in (
            "src_start_addr_h13",
            "src_start_addr_l32",
            "dst_start_addr_h13",
            "dst_start_addr_l32",
        )
    ):
        addr = [
            (reg.src_start_addr_h13, reg.src_start_addr_l32),
            (reg.dst_start_addr_h13, reg.dst_start_addr_l32),
        ]
    else:
        raise TypeError(f"unsupported DMA register: {reg.OP_NAME}")

    lane_mask = (
        getattr(reg, "localmem_mask_h32", 0xFFFFFFFF) * 2**32
        + getattr(reg, "localmem_mask_l32", 0xFFFFFFFF)
    )
    opd0 = dict(
        address=dma_addr(*addr[0]),
        dtype=DType(reg.src_data_format),
        shape=tuple(reg[f"src_{d}size"] for d in "nchw"),
        stride=tuple(reg[f"src_{d}stride"] for d in "nchw"),
        layout=Layout.DMAstride(lane_mask),
    )
    res0 = dict(
        address=dma_addr(*addr[1]),
        dtype=DType(reg.src_data_format),
        shape=tuple(reg[f"dst_{d}size"] for d in "nchw"),
        stride=tuple(reg[f"dst_{d}stride"] for d in "nchw"),
        layout=Layout.DMAstride(lane_mask),
    )
    if reg.nchw_copy:
        res0["shape"] = opd0["shape"]

    attr = dict()
    if lane_mask != (2**64 - 1):
        attr["lane_mask"] = hex(lane_mask)

    if reg.fill_constant_en:
        attr = {}
        opd0 = dict(
            address=reg.constant_value, dtype=DType(reg.src_data_format), is_const=True
        )

    return res0, attr, opd0


@opparam_converter_regitstry("DMA_tensor（0x000）")
def DMA_tensor_0x000__converter(context: "AKSContext", reg: DMA_tensor_0x000__reg):
    NONE = 0
    TRANS = 1  # NC Transpose or Matrix Transpose
    # COLLECT = 2  # CW Transpose from lmem to gmem
    BROADCAST = 3
    # DISTRIBUTE = 4  # CW Transpose from gmem to lmem
    res0, attr, opd0 = dma_reg_fmt_base(reg)
    if reg.nchw_copy:
        res0["shape"] = opd0["shape"]

    if reg.cmd_special_function == BROADCAST:  # broadcast
        n, _, h, w = res0["shape"]
        res0["shape"] = (n, reg.src_csize, h, w)
    elif reg.cmd_special_function == TRANS:  # transpose
        n, c, h, w = opd0["shape"]
        res0["shape"] = (c, n, h, w)
    # elif reg.cmd_special_function in (COLLECT, DISTRIBUTE):  # cw transpose
    #     n, c, h, w = opd0["shape"]
    #     res0["shape"] = (n, w, h, c)

    if reg.cmd_special_function != NONE:
        opd0["stride"] = (*opd0["stride"][:-1], 1)
        res0["stride"] = (*res0["stride"][:-1], 1)

    if reg.cmd_special_function == BROADCAST:
        # disable lane mask
        opd0["layout"] = Layout.stride
        res0["layout"] = Layout.stride

    operands = [get_value(context, **opd0)]
    results = [get_value(context, **res0)]

    return (results, attr, operands)


@opparam_converter_regitstry("DMA_matrix")
def DMA_matrix_converter(context: "AKSContext", reg: DMA_matrix_reg):
    res0, attr, opd0 = dma_reg_fmt_base(reg)
    lane_mask = opd0["layout"].args[0]
    l, r = memmap[MType.R]
    s_addr = opd0["address"]
    is_trans = reg.cmd_special_function == 1
    if s_addr >= l and s_addr < r and (reg.fill_constant_en == 0):
        # glocal
        _, _, H, W = res0["shape"]
        res0["shape"] = (1, 1, H, W)
        _, _, h, _ = res0["stride"]
        res0["stride"] = (0, 0, h, 1)
        # local
        if is_trans:
            H, W = W, H
        opd0["shape"] = (H, W)
        opd0["layout"] = Layout.DMAmatrix(lane_mask, reg.src_wsize)
        n, c, _, _ = opd0["stride"]
        opd0["stride"] = (n, c, 0, 1)
    else:
        # glocal
        _, _, H, W = opd0["shape"]
        opd0["shape"] = (1, 1, H, W)
        _, _, h, _ = opd0["stride"]
        opd0["stride"] = (0, 0, h, 1)
        # local
        if is_trans:
            H, W = W, H
        res0["shape"] = (H, W)
        n, c, _, _ = res0["stride"]
        res0["stride"] = (n, c, 0, 1)
        res0["layout"] = Layout.DMAmatrix(lane_mask, reg.dst_wsize)

    operands = [get_value(context, **opd0)]
    results = [get_value(context, **res0)]
    return (results, attr, operands)


@opparam_converter_regitstry("DMA_masked_select")
def DMA_masked_select_converter(context: "AKSContext", reg: DMA_masked_select_reg):
    shape = tuple(reg[f"src_{d}"] for d in ["nsize", "csize", "hsize/src_wsize_high", "wsize"])
    opd0 = dict(
        address=dma_addr(reg.src_start_addr_h13, reg.src_start_addr_l32),
        dtype=DType(reg.src_data_format),
        shape=shape,
        layout=Layout.alignEU,
    )
    _, c, h, w = shape
    res0 = dict(
        address=dma_addr(reg.dst_start_addr_h13, reg.dst_start_addr_l32),
        dtype=DType(reg.src_data_format),
        shape=shape,
        stride=(c * h * w, h * w, w),
        layout=Layout.alignEU,
    )
    opd1 = dict(
        address=dma_addr(reg.mask_start_addr_h13, reg.mask_start_addr_l32),
        dtype=DType(reg.mask_data_format),
        shape=shape,
        layout=Layout.alignEU,
    )

    operands = [get_value(context, **x) for x in (opd0, opd1)]
    results = [get_value(context, **res0)]

    return (results, {}, operands)


@opparam_converter_regitstry("DMA_lossy_compress")
def DMA_lossy_compress_converter(context: "AKSContext", reg):
    return dma_lossy_converter(context, reg)


@opparam_converter_regitstry("DMA_lossy_decompress")
def DMA_lossy_decompress_converter(context: "AKSContext", reg):
    return dma_lossy_converter(context, reg)


def dma_lossy_converter(context, reg):
    dtype = DType(reg.all_reduce_code & 0xF)
    src_shape = tuple(reg[f"src_{axis}size"] for axis in "nchw")
    dst_shape = tuple(reg[f"dst_{axis}size"] for axis in "nchw")
    src_stride = tuple(reg[f"src_{axis}stride"] for axis in "nch") + (1,)
    dst_stride = tuple(reg[f"dst_{axis}stride"] for axis in "nch") + (1,)
    if reg.nchw_copy:
        dst_shape = src_shape

    opd0 = dict(
        address=dma_addr(reg.src_start_addr_h13, reg.src_start_addr_l32),
        dtype=dtype,
        shape=src_shape,
        stride=src_stride,
        layout=Layout.DMAstride(2**64 - 1),
    )
    if reg.fill_constant_en:
        opd0 = dict(address=reg.constant_value, dtype=dtype, is_const=True)

    res0 = dict(
        address=dma_addr(reg.dst_start_addr_h13, reg.dst_start_addr_l32),
        dtype=dtype,
        shape=dst_shape,
        stride=dst_stride,
        layout=Layout.DMAstride(2**64 - 1),
    )
    attrs = {
        "all_reduce_enable": bool((reg.all_reduce_code >> 15) & 1),
        "all_reduce_opcode": (reg.all_reduce_code >> 4) & 0xF,
        "psum_op": (reg.all_reduce_code >> 8) & 0xF,
    }
    return (
        [get_value(context, **res0)],
        attrs,
        [get_value(context, **opd0)],
    )


@opparam_converter_regitstry("DMA_general")
def DMA_general_converter(context: "AKSContext", reg: DMA_general_reg):
    copy_len = getattr(
        reg,
        "cmd_length",
        getattr(reg, "src_cstride_move_length_", 0),
    )
    opd0 = dict(
        address=dma_addr(reg.src_start_addr_h13, reg.src_start_addr_l32),
        dtype=DType(reg.src_data_format),
        shape=(copy_len,),
        stride=(1,),
        layout=Layout.DMAlinear,
    )
    res0 = dict(
        address=dma_addr(reg.dst_start_addr_h13, reg.dst_start_addr_l32),
        dtype=DType(reg.src_data_format),
        shape=(copy_len,),
        stride=(1,),
        layout=Layout.DMAlinear,
    )
    lane_mask = reg.localmem_mask_h32 * 2**32 + reg.localmem_mask_l32
    attr = dict()
    if lane_mask != (2**64 - 1):
        attr["lane_mask"] = hex(lane_mask)

    if reg.fill_constant_en:
        opd0 = dict(
            address=reg.constant_value, dtype=DType(reg.src_data_format), is_const=True
        )
    bc_size = reg.dst_csize
    if reg.cmd_special_function == 1:
        res0["shape"] = (bc_size, copy_len)
        res0["stride"] = (info.LANE_SIZE, 1)

    operands = [get_value(context, **opd0)]
    results = [get_value(context, **res0)]

    return (results, attr, operands)


@opparam_converter_regitstry("DMA_cw_transpose")
def DMA_cw_transpose_converter(context: "AKSContext", reg: DMA_cw_transpose_reg):
    lane_mask = reg.localmem_mask_h32 * 2**32 + reg.localmem_mask_l32
    n, c, h, w = (reg[f"src_{d}size"] for d in "nchw")
    opd0 = dict(
        address=dma_addr(reg.src_start_addr_h13, reg.src_start_addr_l32),
        dtype=DType(reg.src_data_format),
        shape=(n, c, h, w),
        stride=(*(reg[f"src_{d}stride"] for d in "nch"), 1),
        layout=Layout.DMAstride(lane_mask),
    )
    res0 = dict(
        address=dma_addr(reg.dst_start_addr_h13, reg.dst_start_addr_l32),
        dtype=DType(reg.src_data_format),
        shape=(n, w, h, c),
        stride=(*(reg[f"dst_{d}stride"] for d in "nch"), 1),
        layout=Layout.DMAstride(lane_mask),
    )
    attr = dict()
    if lane_mask != (2**64 - 1):
        attr["lane_mask"] = hex(lane_mask)

    if reg.fill_constant_en:
        attr = {}
        opd0 = dict(
            address=reg.constant_value, dtype=DType(reg.src_data_format), is_const=True
        )

    operands = [get_value(context, **opd0)]
    results = [get_value(context, **res0)]

    return (results, attr, operands)


@opparam_converter_regitstry("DMA_nonzero")
def DMA_nonzero_converter(context: "AKSContext", reg: DMA_nonzero_reg):
    n, c, h, w = (reg[f"src_{d}size"] for d in "nchw")
    stride = (c * h * w, h * w, w, 1)
    opd0 = dict(
        address=dma_addr(reg.src_start_addr_h13, reg.src_start_addr_l32),
        dtype=DType(reg.src_data_format),
        shape=(n, c, h, w),
        stride=stride,
        layout=Layout.alignEU,
    )
    res0 = dict(
        address=dma_addr(reg.dst_start_addr_h13, reg.dst_start_addr_l32),
        dtype=DType(reg.src_data_format),
        shape=(n, h, w, c),
        stride=stride,
        layout=Layout.alignEU,
    )

    attr = dict(base=reg.dst_nstride_base_i_)

    operands = [get_value(context, **opd0)]
    results = [get_value(context, **res0)]

    return (results, attr, operands)


def dma_gather_base(context, reg: DMA_gather_reg):
    lane_mask = reg.localmem_mask_h32 * 2**32 + reg.localmem_mask_l32
    c, h, w = (reg[f"src_{d}size"] for d in "chw")
    d_h = reg.dst_hsize
    if reg.nchw_copy:
        d_h = h
    stride = (c * h * w, h * w, w, 1)
    opd0 = dict(
        address=dma_addr(reg.src_start_addr_h13, reg.src_start_addr_l32),
        dtype=DType(reg.src_data_format),
        shape=(1, c, h, w),
        stride=(0, reg.src_cstride, reg.src_hstride, 1),
        layout=Layout.stride,
    )
    res0 = dict(
        address=dma_addr(reg.dst_start_addr_h13, reg.dst_start_addr_l32),
        dtype=DType(reg.src_data_format),
        shape=(1, max(c, reg.index_csize), d_h, w),
        stride=(0, reg.dst_cstride, reg.dst_hstride, 1),
        layout=Layout.DMAstride(lane_mask),
    )
    opd1 = dict(
        address=dma_addr(reg.index_start_addr_h13, reg.index_start_addr_l32),
        dtype=DType.ui32,
        shape=(1, reg.index_csize, d_h, 1),
        stride=(0, reg.index_cstride, 1, 1),
        layout=Layout.stride,
    )
    const = get_value(
        context,
        address=reg.constant_value,
        dtype=DType(reg.src_data_format),
        is_const=True,
    ).data
    attr = dict(const=const)

    operands = [get_value(context, **x) for x in (opd0, opd1)]
    results = [get_value(context, **res0)]

    return (results, attr, operands)


@opparam_converter_regitstry("DMA_gather")
def DMA_gather_converter(context: "AKSContext", reg: DMA_gather_reg):
    return dma_gather_base(context, reg)


@opparam_converter_regitstry("DMA_scatter")
def DMA_scatter_converter(context: "AKSContext", reg: DMA_scatter_reg):
    results, _, operands = dma_gather_base(context, reg)

    return (results, {}, operands)


@opparam_converter_regitstry("DMA_reverse")
def DMA_reverse_converter(context: "AKSContext", reg: DMA_reverse_reg):
    n, c, h, w = (reg[f"src_{d}size"] for d in "nchw")
    stride = (c * h * w, h * w, w, 1)
    opd0 = dict(
        address=dma_addr(reg.src_start_addr_h13, reg.src_start_addr_l32),
        dtype=DType(reg.src_data_format),
        shape=(n, c, h, w),
        stride=stride,
        layout=Layout.alignEU,
    )
    res0 = dict(
        address=dma_addr(reg.dst_start_addr_h13, reg.dst_start_addr_l32),
        dtype=DType(reg.src_data_format),
        shape=(n, h, w, c),
        stride=stride,
        layout=Layout.alignEU,
    )
    attr = dict(base=reg.dst_nstride)

    operands = [get_value(context, **opd0)]
    results = [get_value(context, **res0)]

    return (results, attr, operands)


@opparam_converter_regitstry("DMA_compress")
def DMA_compress_converter(context: "AKSContext", reg: DMA_compress_reg):
    return [] * 3


@opparam_converter_regitstry("DMA_decompress ")
def DMA_decompress_converter(context: "AKSContext", reg: DMA_decompress__reg):
    return [] * 3


@opparam_converter_regitstry("sDMA_sys")
def sDMA_sys_converter(context: "AKSContext", reg: sDMA_sys_reg):
    des_imm = reg.constant_value
    msg_id = des_imm & 0x1FF
    cnt = (des_imm >> 9) & 0x7F
    attr = {}
    if reg.cmd_special_function in (3, 4):
        attr = dict(msg_id=msg_id, cnt=cnt)
    elif reg.cmd_special_function > 4:
        raise KeyError(f"cmd_special_function {reg.cmd_special_function} not supported")
    return ([], attr, [])


def cdma_base(context, reg):
    src_n, src_c, src_h, src_w = (reg[f"src_{d}size"] for d in "nchw")
    dst_shape = tuple(
        getattr(reg, f"dst_{d}size", value)
        for d, value in zip("nchw", (src_n, src_c, src_h, src_w))
    )
    dst_n, dst_c, dst_h, dst_w = dst_shape
    src_sn, src_sc, src_sh = (reg[f"src_{d}stride"] for d in "nch")
    dst_sn, dst_sc, dst_sh = (
        getattr(reg, f"dst_{d}stride", value)
        for d, value in zip("nch", (src_sn, src_sc, src_sh))
    )
    opd0 = dict(
        address=extended_dma_addr(reg, "src"),
        dtype=DType(reg.src_data_format),
        shape=(src_n, src_c, src_h, src_w),
        stride=(src_sn, src_sc, src_sh, 1),
        layout=Layout.compact,
    )
    res0 = dict(
        address=extended_dma_addr(reg, "dst"),
        dtype=DType(reg.src_data_format),
        shape=(dst_n, dst_c, dst_h, dst_w),
        stride=(dst_sn, dst_sc, dst_sh, 1),
        layout=Layout.compact,
    )
    if reg.nchw_copy:
        res0["shape"] = opd0["shape"]

    attr = dict()
    operands = [get_value(context, **opd0)]
    results = [get_value(context, **res0)]

    return (results, attr, operands)

def cdma_operand_base(context, reg):
    n, c, h, w = (reg[f"src_{d}size"] for d in "nchw")
    sn, sc, sh = (reg[f"src_{d}stride"] for d in "nch")
    stride = (sn, sc, sh, 1)
    opd0 = dict(
        address=extended_dma_addr(reg, "src"),
        dtype=DType(reg.src_data_format),
        shape=(n, c, h, w),
        stride=stride,
        layout=Layout.alignLine,
    )
    attr = dict()
    operands = [get_value(context, **opd0)]
    return ([], attr, operands)

def cdma_result_base(context, reg):
    n, c, h, w = (reg[f"dst_{d}size"] for d in "nchw")
    sn, sc, sh = (reg[f"dst_{d}stride"] for d in "nch")
    stride = (sn, sc, sh, 1)
    res0 = dict(
        address=extended_dma_addr(reg, "dst"),
        dtype=DType(0),
        shape=(n, c, h, w),
        stride=stride,
        layout=Layout.compact,
    )
    attr = dict()
    results = [get_value(context, **res0)]
    return (results, attr, [])


@opparam_converter_regitstry("CDMA_send")
def CDMA_send_converter(context: "AKSContext", reg: CDMA_send_reg):
    rao = cdma_operand_base(context, reg)
    rao[1]["psum_op"] = "rd+wr" if reg.psum_op else "wo"
    return rao

@opparam_converter_regitstry("CDMA_read")
def CDMA_read_converter(context: "AKSContext", reg: CDMA_read_reg):
    return cdma_base(context, reg)

@opparam_converter_regitstry("CDMA_write")
def CDMA_write_converter(context: "AKSContext", reg: CDMA_write_reg):
    return cdma_base(context, reg)

@opparam_converter_regitstry("CDMA_general")
def CDMA_general_converter(context: "AKSContext", reg: CDMA_general_reg):
    copy_len = getattr(
        reg,
        "cmd_length",
        getattr(reg, "src_cstride_move_length_", 0),
    )
    opd0 = dict(
        address=extended_dma_addr(reg, "src"),
        dtype=DType(reg.src_data_format),
        shape=(copy_len,),
        stride=(1,),
        layout=Layout.DMAlinear,
    )
    res0 = dict(
        address=extended_dma_addr(reg, "dst"),
        dtype=DType(reg.src_data_format),
        shape=(copy_len,),
        stride=(1,),
        layout=Layout.DMAlinear,
    )
    # attr = dict(base=reg.dst_nstride)
    attr = dict()
    operands = [get_value(context, **opd0)]
    results = [get_value(context, **res0)]
    return (results, attr, operands)

@opparam_converter_regitstry("CDMA_receive")
def CDMA_receive_converter(context: "AKSContext", reg: CDMA_receive_reg):
    reduce_type = {0: "nop", 1:"mul", 2:"max", 3:"min", 4:"add"}
    rao = cdma_result_base(context, reg)
    rao[1]["reduce_op"] = reduce_type[reg.reduce_op]
    return rao

@opparam_converter_regitstry("CDMA_lossy_compress")
def CDMA_lossy_compress_converter(context: "AKSContext", reg: CDMA_lossy_compress_reg):
    rao = cdma_operand_base(context, reg)
    rao[1]["psum_op"] = "rd+wr" if reg.psum_op else "wo"
    return rao

@opparam_converter_regitstry("CDMA_lossy_decompress")
def CDMA_lossy_decompress_converter(context: "AKSContext", reg: CDMA_lossy_decompress_reg):
    rao = cdma_operand_base(context, reg)
    rao[1]["psum_op"] = "rd+wr" if reg.psum_op else "wo"
    return rao

@opparam_converter_regitstry("sCDMA_sys")
def sCDMA_sys_converter(context: "AKSContext", reg: sCDMA_sys_reg):
    constant_value_l32 = reg.constant_value_l32
    attr = {}
    if reg.cmd_special_function in (3, 4, 5, 6):
        msg_id = constant_value_l32 & 0x1FF
        cnt = (constant_value_l32 >> 16) & 0x7F
        attr = dict(msg_id=msg_id, cnt=cnt)
    elif reg.cmd_special_function == 2:
        const_value = reg.constant_value_h32<<32|constant_value_l32
        reg_sel = reg.reg_sel
        if reg_sel == 0:
            reg_sel = "current_id"
        elif reg_sel == 1:
            reg_sel = "src_mac_id"
        elif reg_sel == 2:
            reg_sel = "dst_mac_id"
        attr = dict(const=const_value, reg_sel=reg_sel)
    return ([], attr, [])

@opparam_converter_regitstry("CDMA_tcp_send")
def CDMA_tcp_send_converter(context: "AKSContext", reg: CDMA_tcp_send_reg):
    return [] * 3

@opparam_converter_regitstry("CDMA_tcp_receive")
def CDMA_tcp_rcv_converter(context: "AKSContext", reg: CDMA_tcp_rcv_reg):
    return [] * 3


LONG_TIU_CONVERTER_ALIASES = {
    "CONV": "sCONV",
    "CONV_BW": "sCONV_BW",
    "MM": "sMM",
    "MM2": "sMM2",
    "CMP": "sCMP",
    "SFU": "sSFU",
    "VC": "sVC",
    "LIN": "sLIN",
    "AR": "sAR",
    "PorD": "sPorD",
    "RQ&DQ": "sRQ&sDQ",
    "SG": "sSG",
    "SGL": "sSGL",
    "CW&BC": "sCW&sBC",
}

for long_name, short_name in LONG_TIU_CONVERTER_ALIASES.items():
    opparam_converter[long_name] = opparam_converter[short_name]
