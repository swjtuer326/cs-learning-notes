"""Workbook sheet mappings for register-definition generation.

This is repository tooling configuration and is intentionally kept outside the
runtime ``bigTpuProfile`` package.
"""

AKS_TIU_SHEETS = (
    "CONV", "sCONV", "CONV_BW", "sCONV_BW", "MM", "sMM", "MM2", "sMM2",
    "CMP", "sCMP", "SFU", "sSFU", "VC", "sVC", "LIN", "sLIN", "AR",
    "sAR", "PorD", "sPorD", "RQ&DQ", "sRQ&sDQ", "SG", "sSG", "SGL",
    "sSGL", "CW&BC", "sCW&sBC", "LAR", "SYS", "SYSID", "SYS_TR_ACC",
    "RAND",
)

AKS_TIU_SHEET_MAP = {
    name: name for name in AKS_TIU_SHEETS if name != "SYSID"
}

AKS_DMA_SHEET_MAP = {
    name: name
    for name in (
        "DMA_tensor（0x000）", "DMA_matrix", "DMA_masked_select",
        "DMA_general", "DMA_nonzero", "sDMA_sys", "DMA_gather",
        "DMA_scatter", "DMA_reverse", "DMA_lossy_compress",
        "DMA_lossy_decompress",
    )
}

AKS_CDMA_SHEET_MAP = {
    "DMA_send（0x000）": "CDMA_send",
    "DMA_read（0x000)": "CDMA_read",
    "DMA_write（0x000）": "CDMA_write",
    "DMA_general（0x000)": "CDMA_general",
    "DMA_receive（0x000）": "CDMA_receive",
    "DMA_lossy_compress（0x000）": "CDMA_lossy_compress",
    "DMA_lossy_decompress（0x000）": "CDMA_lossy_decompress",
    "sDMA_sys": "sCDMA_sys",
    "DMA_tcp_send（0x000）": "CDMA_tcp_send",
    "DMA_tcp_rcv（0x000）": "CDMA_tcp_receive",
}

AKSV_TIU_SHEET_MAP = {
    "sCONV": "sCONV",
    "sCONV_BW": "sCONV_BW",
    "sPorD": "sPorD",
    "sMM": "sMM",
    "sMM2": "sMM2",
    "sAR": "sAR",
    "sRQ&sDQ": "sRQ&sDQ",
    "sCW&sBC": "sCW&sBC",
    "sSG": "sSG",
    "sSGL": "sSGL",
    "sRAND": "RAND",
    "sSFU": "sSFU",
    "sLIN": "sLIN",
    "SYS_TR_WR": "SYS_TR_ACC",
    "sCMP": "sCMP",
    "sVC": "sVC",
    "SYS": "SYS",
}

AKSV_DMA_SHEET_MAP = {
    "DMA_tensor（0x000）": "DMA_tensor（0x000）",
    "DMA_matrix": "DMA_matrix",
    "DMA_masked_select": "DMA_masked_select",
    "DMA_general": "DMA_general",
    "DMA_nonzero": "DMA_nonzero",
    "sDMA_sys": "sDMA_sys",
    "DMA_gather": "DMA_gather",
    "DMA_scatter": "DMA_scatter",
    "DMA_reverse": "DMA_reverse",
    "DMA_lossy_compress": "DMA_lossy_compress",
    "DMA_lossy_decompress": "DMA_lossy_decompress",
}

AKSV_CDMA_SHEET_MAP = {
    "DMA_send（0x000）": "CDMA_send",
    "DMA_read（0x000)": "CDMA_read",
    "DMA_write（0x000）": "CDMA_write",
    "sDMA_general": "CDMA_general",
    "DMA_receive（0x000）": "CDMA_receive",
    "DMA_lossy_compress（0x000）": "CDMA_lossy_compress",
    "DMA_lossy_decompress（0x000）": "CDMA_lossy_decompress",
    "sDMA_sys": "sCDMA_sys",
}

BM1684X_TIU_SHEET_MAP = {
    name: name
    for name in (
        "CONV", "sCONV", "MM", "sMM", "MM2", "sMM2", "CMP", "sCMP",
        "SFU", "sSFU", "VC", "sVC", "LIN", "sLIN", "AR", "sAR",
        "SEG", "sSEG", "PorD", "sPorD", "RQ&DQ", "sRQ&sDQ", "SG",
        "sSG", "SGL", "sSGL", "TRANS&BC", "sTRANS&sBC", "LAR", "SYS",
        "SYSID",
    )
}

BM1684X_DMA_SHEET_MAP = {
    name: name
    for name in (
        "DMA_tensor（0x000）", "DMA_matrix", "sDMA_matrix",
        "DMA_masked_select", "sDMA_masked_select ", "DMA_general",
        "sDMA_general", "DMA_cw_transpose", "DMA_nonzero",
        "sDMA_nonzero", "sDMA_sys", "DMA_gather", "DMA_scatter",
    )
}

BM1686_TIU_SHEET_MAP = {
    name: name
    for name in (
        "CONV", "sCONV", "MM", "sMM", "MM2", "sMM2", "CMP", "sCMP",
        "SFU", "sSFU", "LIN", "sLIN", "VC", "sVC", "AR", "sAR",
        "PorD", "sPorD", "RQ&DQ", "sRQ&sDQ", "SG", "sSG", "SGL",
        "sSGL", "CW&BC", "sCW&sBC", "LAR", "SYS", "SYS_TR_ACC",
    )
}

BM1686_DMA_SHEET_MAP = {
    **{
        name: name
        for name in (
            "DMA_tensor（0x000）", "DMA_matrix", "sDMA_matrix",
            "DMA_masked_select", "sDMA_masked_select ", "DMA_general",
            "sDMA_general", "DMA_cw_transpose", "DMA_nonzero",
            "sDMA_nonzero", "sDMA_sys", "DMA_gather", "DMA_scatter",
            "DMA_reverse", "DMA_compress",
        )
    },
    "DMA_decompress ": "DMA_decompress",
}

CHIP_SHEET_MAPS = {
    "AKS": (AKS_TIU_SHEET_MAP, AKS_DMA_SHEET_MAP, AKS_CDMA_SHEET_MAP),
    "AKSV": (AKSV_TIU_SHEET_MAP, AKSV_DMA_SHEET_MAP, AKSV_CDMA_SHEET_MAP),
    "BM1684X": (BM1684X_TIU_SHEET_MAP, BM1684X_DMA_SHEET_MAP, {}),
    "BM1686": (BM1686_TIU_SHEET_MAP, BM1686_DMA_SHEET_MAP, {}),
}

REGISTER_ALIASES = {
    "AKS": {
        "DMA_cw_transpose": "DMA_matrix",
    },
    "AKSV": {
        "DMA_cw_transpose": "DMA_matrix",
    },
}
