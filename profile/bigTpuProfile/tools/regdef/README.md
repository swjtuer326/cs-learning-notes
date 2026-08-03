# Register definition generator

This directory contains repository-maintenance tools and is intentionally
outside the `bigTpuProfile` Python package, so it is not included in wheels.

Chip-specific workbook sheet names and output-name mappings live in
`chip_configs.py`. The generator itself contains only workbook parsing,
validation, normalization, and Python rendering logic.

Example:

```bash
python3 tools/regdef/xlsx_to_py.py \
  --chip AKSV \
  --tiu-xlsx refer/aksv/SG2260E_TPU_TIU_Reg1.0.xlsx \
  --dma-xlsx refer/aksv/GDMA_SG2260E_DES_REG.xlsx \
  --cdma-xlsx refer/aksv/CDMA_2260E_DES_REG_v6.4.xlsx \
  --output bigTpuProfile/debugger/target/regdef_aksv.py
```

Generated `regdef_<chip>.py` files remain independent. To add a chip, add one
entry to `CHIP_SHEET_MAPS` and keep workbook-specific aliases in that chip's
configuration.
