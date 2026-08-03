#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2023/8/7 11:49
# @Author  : chongqing.zeng@bigtpu.com
# @Project: PerfAI
# Style definitions as xlsxwriter format property dicts


def merge_format(workbook, *dicts):
    """Merge multiple format property dicts and create an xlsxwriter format."""
    merged = {}
    for d in dicts:
        if d:
            merged.update(d)
    return workbook.add_format(merged)


class DetailsStyle:
    title_pattern = {'bg_color': '#ffffff'}
    title_header_pattern = {'bg_color': '#548235'}
    title_content_pattern = {'bg_color': '#F2F2F2'}
    title_font = {'font_name': '等线', 'font_size': 10, 'bold': True, 'font_color': '#000000'}
    title_header_font = {'font_name': 'Calibri', 'font_size': 10, 'bold': True, 'font_color': '#ffffff'}

    content_pattern = {'bg_color': '#305496'}
    key_content_pattern = {'bg_color': '#FCE4D6'}
    red_light = {'bg_color': '#FF0000'}
    yellow_light = {'bg_color': '#FFFF00'}

    center_align = {'align': 'center', 'valign': 'top'}
    right_align = {'align': 'right', 'valign': 'top'}
    left_align = {'align': 'left', 'valign': 'top'}
    border = {'border': 1}

    tiu_pattern = {'bg_color': '#008000'}
    gdma_pattern = {'bg_color': '#FFA500'}
    sdma_pattern = {'bg_color': '#D0CECE'}
    cdma_pattern = {'bg_color': '#C0504D'}


class LayerStyle:
    title_pattern = {'bg_color': '#ffffff'}
    title_header_pattern = {'bg_color': '#B8CCE4'}
    title_content_pattern = {'bg_color': '#F2F2F2'}
    title_font = {'font_name': '等线', 'font_size': 10, 'bold': True, 'font_color': '#000000'}
    title_header_font = {'font_name': 'Calibri', 'font_size': 10, 'bold': True, 'font_color': '#000000'}

    content_header1_pattern = {'bg_color': '#95B3D7'}
    content_header2_pattern = {'bg_color': '#31869B'}
    uarch_pattern = {'bg_color': '#538FD5'}
    alg_pattern = {'bg_color': '#B8CCE4'}
    sim_pattern = {'bg_color': '#95B3D7'}
    content_header_font = {'font_name': 'Calibri', 'font_size': 10, 'bold': True, 'font_color': '#ffffff'}

    red_light = {'bg_color': '#FF0000'}
    yellow_light = {'bg_color': '#FFFF00'}

    center_align = {'align': 'center', 'valign': 'top'}
    right_align = {'align': 'right', 'valign': 'top'}
    left_align = {'align': 'left', 'valign': 'top'}
    border = {'border': 1}
    title_border = {'border': 2}
    tab_pattern = {'bg_color': '#0070C0'}


class SummaryStyle:
    title_pattern = {'bg_color': '#ffffff'}
    title_header_pattern = {'bg_color': '#B8CCE4'}
    title_content_pattern = {'bg_color': '#F4DCDB'}
    title_font = {'font_name': '等线', 'font_size': 10, 'bold': True, 'font_color': '#000000'}
    title_header_font = {'font_name': 'Calibri', 'font_size': 10, 'bold': True, 'font_color': '#000000'}

    content1_pattern = {'bg_color': '#FDF5E6'}
    content2_pattern = {'bg_color': '#F5DEB3'}
    content_header1_pattern = {'bg_color': '#C6E0B4'}
    content_header2_pattern = {'bg_color': '#548235'}
    content_header_font = {'font_name': 'Calibri', 'font_size': 10, 'bold': True, 'font_color': '#ffffff'}

    red_light = {'bg_color': '#FF0000'}
    yellow_light = {'bg_color': '#FFFF00'}

    center_align = {'align': 'center', 'valign': 'top'}
    right_align = {'align': 'right', 'valign': 'top'}
    left_align = {'align': 'left', 'valign': 'top'}
    border = {'border': 1}
    title_border = {'border': 2}
    tab_pattern = {'bg_color': '#31869B'}
