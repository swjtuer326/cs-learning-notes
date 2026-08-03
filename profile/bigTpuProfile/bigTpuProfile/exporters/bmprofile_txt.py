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
from glob import glob


class TxtExporter:
    def __init__(self, parser):
        self.parser = parser

    def _write_engine_info(self, nfile, idx, events, engine, new_file=True):
        p = self.parser
        if not new_file and not os.path.exists(nfile.format(idx)):
            new_file = True
        fmode = 'w'
        if not new_file:
            fmode = 'a'
        if engine in [p.archlib.EngineType.GDMA, p.archlib.EngineType.SDMA]:
            engine_id = 1
            if engine == p.archlib.EngineType.SDMA:
                engine_id = 3
            arch = p.archlib.DMA_ARCH
            tag = "__TDMA_REG_INFO__\n"
        elif engine == p.archlib.EngineType.BD:
            engine_id = 0
            arch = p.archlib.TIU_ARCH
            tag = "__TIU_REG_INFO__\n"
        elif engine == p.archlib.EngineType.CDMA:
            engine_id = 4
            arch = p.archlib.DMA_ARCH
            tag = "__CDMA_REG_INFO__\n"
        else:
            raise ValueError(f"Not support parse {p.archlib.EngineType(engine).name} now.")
        if len(events):
            with open(nfile.format(idx), fmode, buffering=1024*1024) as f:
                if new_file:
                    f.write("__CHIP_ARCH_ARGS__\n")
                    f.write("".join(f"\t{key}: {value}\n" for key,
                            value in arch.items()))
                buf = []
                buf_size = 0
                for event in events:
                    info, extra, meta = event
                    if engine_id==4:
                        meta.pop("Port")
                    info.update(meta)
                    buf.append(tag)
                    buf.append("".join(f"\t{key}: {value}\n" for key, value in info.items()))
                    if extra is not None:
                        buf.append('{}:\n'.format(info["Function Type"]))
                        buf.append("".join(f"\t{key}: {value}\n" for key, value in extra.items()))
                    buf_size += 1
                    if buf_size >= 1000:
                        f.write("".join(buf))
                        buf.clear()
                        buf_size = 0
                if buf:
                    f.write("".join(buf))

    def to_txt(self, out_dir, tiu_freq=1000):
        p = self.parser
        p.tiu_freq = tiu_freq
        p.archlib.TIU_ARCH["TIU Frequency(MHz)"] = tiu_freq
        assert p.bd_events != [] and p.gdma_events != [], ""
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        dma_file = os.path.join(out_dir, "tdmaRegInfo_{}.txt")
        tiu_file = os.path.join(out_dir, "tiuRegInfo_{}.txt")
        cdma_file = os.path.join(out_dir, "cdmaRegInfo_{}.txt")
        for f in glob(os.path.join(out_dir, "*RegInfo_*.txt")):
            os.remove(f)

        print("Write engine info...")
        for idx in tqdm(range(p.num_cores)):
            if idx < len(p.gdma_events):
                self._write_engine_info(dma_file, idx, p.gdma_events[idx], p.archlib.EngineType.GDMA)
            if idx < len(p.sdma_events):
                self._write_engine_info(dma_file, idx, p.sdma_events[idx], p.archlib.EngineType.SDMA, False)
            if idx < len(p.bd_events):
                self._write_engine_info(tiu_file, idx, p.bd_events[idx], p.archlib.EngineType.BD)

        for idx, events in tqdm(enumerate(p.cdma_events)):
            self._write_engine_info(cdma_file, idx, events, p.archlib.EngineType.CDMA)