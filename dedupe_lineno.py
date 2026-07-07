#!/usr/bin/env python3
"""
扫描 zephyr-rtos/*.md，找出正文中冗余的行号引用并清理。

策略：
1. 删除 markdown link 后的 " 第 N-M 行" / " 第 N 行" 正文标注
2. 删除 markdown link 后的 "（第 N-M 行）" / "（第 N 行）" 括号标注
3. 若 link URL 无锚点，把行号转为 #LN-LM 加到 URL
4. 删除 markdown link 文本中的 "L121-L127" 显示行号
5. 删除 markdown link 文本中的 ":N-M" 显示行号
6. 删除 markdown link 后的 " L16-L18 " 显示行号
7. 处理 link 前的行号标注 "第 N-M 行（[text](url)）"
8. 简化代码块内 /* 第 N-M 行：xxx */ 注释
9. 简化代码块内 /* file_path L121-L127 */ 注释
10. 简化代码块内 /* 来源：xxx.rst 第 N-M 行 */ 注释
11. 把反引号 file:// URL + 行号转为 markdown link

保留：
- "第 N 章"、"第 N 节"、"第 N 步" 等非行号引用
- markdown link 锚点中的 #L123-L456
"""

import re
import sys
from pathlib import Path

NOTES_DIR = Path("/home/pbw/rtos/cs-learning-notes/zephyr-rtos")


def make_anchor(line_range: str) -> str:
    """把 '6596-6637' / '6596' / '121-L127' / 'L121-L127' 转成 '#L6596-L6637' / '#L6596'"""
    # 去掉所有 L 前缀，统一处理
    cleaned = line_range.replace("L", "")
    if "-" in cleaned:
        start, end = cleaned.split("-", 1)
        return f"#L{start.strip()}-L{end.strip()}"
    return f"#L{cleaned.strip()}"


def process_text(text: str, name: str) -> tuple[str, list[str]]:
    logs = []
    orig = text

    # 行号引用的核心模式：
    # - 单个：第 N 行 / 第 N-M 行
    # - 多范围：第 N-M、A-B、C-D 行
    # - 或形式：第 N 或 M 行
    LINE_REF = r'第\s*\d+(?:-\d+)?(?:\s*[、或]\s*\d+(?:-\d+)?)*\s*行'

    # ===== 模式 1: [text](url#LN-LM) 第 N-M 行 / 第 N 行 =====
    def rep1(m):
        logs.append(f"  [1a] 删除正文行号")
        return m.group(1)

    pat1a = re.compile(
        r'(\[[^\]\n]+\]\(file://[^)\n]*?#L\d+(?:-L\d+)?\))\s*第\s*(\d+(?:-\d+)?)\s*行'
    )
    text = pat1a.sub(rep1, text)

    # ===== 模式 1b: [text](url) 第 N-M 行 / 第 N 行 =====
    def rep1b(m):
        text_part = m.group(1)
        url = m.group(2)
        line_range = m.group(3)
        anchor = make_anchor(line_range)
        if '#' in url:
            return m.group(0)
        new = f'{text_part}({url}{anchor})'
        logs.append(f"  [1b] URL 加锚点并删除正文行号")
        return new

    pat1b = re.compile(
        r'(\[[^\]\n]+\])\((file://[^)\n]*?)\)\s*第\s*(\d+(?:-\d+)?)\s*行'
    )
    text = pat1b.sub(rep1b, text)

    # ===== 模式 2a: [text](url#LN-LM)（第 N-M 行） / （第 N 行） =====
    def rep2a(m):
        logs.append(f"  [2a] 删除正文行号括号")
        return m.group(1)

    pat2a = re.compile(
        r'(\[[^\]\n]+\]\(file://[^)\n]*?#L\d+(?:-L\d+)?\))\s*（\s*第\s*\d+(?:-\d+)?\s*行\s*）'
    )
    text = pat2a.sub(rep2a, text)

    # ===== 模式 2b: [text](url)（第 N-M 行） / （第 N 行） =====
    def rep2b(m):
        text_part = m.group(1)
        url = m.group(2)
        line_range = m.group(3)
        anchor = make_anchor(line_range)
        if '#' in url:
            return m.group(0)
        new = f'{text_part}({url}{anchor})'
        logs.append(f"  [2b] URL 加锚点并删除括号")
        return new

    pat2b = re.compile(
        r'(\[[^\]\n]+\])\((file://[^)\n]*?)\)\s*（\s*第\s*(\d+(?:-\d+)?)\s*行\s*）'
    )
    text = pat2b.sub(rep2b, text)

    # ===== 模式 3: [text](url#LN-LM) L16-L18 后跟说明 =====
    # 这种形式：[file](url#LN-LM) L16-L18 维护...
    def rep3(m):
        logs.append(f"  [3] 删除正文 L16-L18 标注")
        return m.group(1)

    pat3 = re.compile(
        r'(\[[^\]\n]+\]\(file://[^)\n]*?#L\d+(?:-L\d+)?\))\s+L\d+(?:-L\d+)?\s'
    )
    text = pat3.sub(rep3, text)

    # ===== 模式 3b: [text](url) L16-L18 后跟说明 =====
    def rep3b(m):
        text_part = m.group(1)
        url = m.group(2)
        line_range = m.group(3)
        anchor = make_anchor(line_range)
        if '#' in url:
            return m.group(0)
        new = f'{text_part}({url}{anchor}) '
        logs.append(f"  [3b] URL 加锚点并删除 L 标注")
        return new

    pat3b = re.compile(
        r'(\[[^\]\n]+\])\((file://[^)\n]*?)\)\s+L(\d+(?:-L\d+)?)\s'
    )
    text = pat3b.sub(rep3b, text)

    # ===== 模式 4: [text L121-L127](url) → [text](url#L121-L127) =====
    # link 文本里包含 "L121-L127" 显示行号
    # group(1): 前缀文本, group(2): 行号数字, group(3): 后缀文本(如"（节选）"),
    # group(4): URL, group(5): 现有锚点
    def rep4(m):
        text_prefix = m.group(1).rstrip()
        line_range = m.group(2)
        text_suffix = (m.group(3) or "").strip()
        url = m.group(4)
        existing_anchor = m.group(5) or ""
        anchor = make_anchor(line_range)
        # 组合 link 文本：前缀 + 后缀（保留"（节选）"等说明）
        if text_suffix:
            full_text = f'{text_prefix} {text_suffix}'
        else:
            full_text = text_prefix
        # 如果 URL 已有锚点且是范围锚点，保留；否则加上
        if existing_anchor and '-L' in existing_anchor:
            new = f'[{full_text}]({url}{existing_anchor})'
        else:
            new = f'[{full_text}]({url}{anchor})'
        logs.append(f"  [4] link 文本去行号")
        return new

    pat4 = re.compile(
        r'\[([^\]\n]*?)\s+L(\d+(?:-L\d+)?)([^\]\n]*?)\]\((file://[^)\n]*?)(#L\d+(?:-L\d+)?)?\)'
    )
    # 迭代应用，处理一个 link 文本中含多个 "L\d+-L\d+" 的情况
    # 例：[file.c L155-L180 L612-L651](url#L155) → 第一次去掉 L155-L180，第二次去掉 L612-L651
    while True:
        new_text = pat4.sub(rep4, text)
        if new_text == text:
            break
        text = new_text

    # ===== 模式 4b: [L153-L163](url) → [filename](url#...) =====
    # link 文本本身就是行号范围，没有其他文本前缀
    def rep4b(m):
        line_range = m.group(1)
        url = m.group(2)
        existing_anchor = m.group(3) or ""
        anchor = make_anchor(line_range)
        # 从 URL 提取文件名作为 link 文本
        fname = url.rsplit('/', 1)[-1] or "file"
        if existing_anchor:
            new = f'[{fname}]({url}{existing_anchor})'
        else:
            new = f'[{fname}]({url}{anchor})'
        logs.append(f"  [4b] link 文本(纯行号)转文件名")
        return new

    pat4b = re.compile(
        r'\[L(\d+(?:-L\d+)?)\]\((file://[^)\n]*?)(#L\d+(?:-L\d+)?)?\)'
    )
    text = pat4b.sub(rep4b, text)

    # ===== 模式 5: [text:N-M](url) / [text:N](url) → [text](url#LN-LM) =====
    # link 文本里包含 ":N-M" / ":N" 显示行号，把行号转为 URL 锚点
    # 同时保留 link 文本中的其他内容（如"（节选）"、反引号等）
    # 注意：:N-M 前后通常无空格（如 `path:851-852`），所以拼接时不加空格
    def rep5(m):
        text_prefix = (m.group(1) or "")
        line_range = m.group(2)  # "851-852" 或 "612"
        text_suffix = (m.group(3) or "")
        url = m.group(4)
        existing_anchor = m.group(5) or ""
        anchor = make_anchor(line_range)
        # 直接拼接前后文本，不加空格（因为 :N-M 前后原本无空格）
        full_text = f'{text_prefix}{text_suffix}'
        if existing_anchor and '-L' in existing_anchor:
            new = f'[{full_text}]({url}{existing_anchor})'
        else:
            new = f'[{full_text}]({url}{anchor})'
        logs.append(f"  [5] link 文本去 :N-M 行号")
        return new

    pat5 = re.compile(
        r'\[([^\]\n]*?):(\d+(?:-\d+)?)([^\]\n]*?)\]\((file://[^)\n]*?)(#L\d+(?:-L\d+)?)?\)'
    )
    text = pat5.sub(rep5, text)

    # ===== 模式 6: 第 N-M 行（[text](url)） → [text](url#LN-LM) =====
    # link 前面的行号
    def rep6(m):
        text_part = m.group(1)
        url = m.group(2)
        line_range = m.group(3)
        anchor = make_anchor(line_range)
        if '#' in url:
            new = f'{text_part}({url})'
        else:
            new = f'{text_part}({url}{anchor})'
        logs.append(f"  [6] link 前行号转锚点")
        return new

    pat6 = re.compile(
        r'第\s*(\d+(?:-\d+)?)\s*行\s*[（(]\s*(\[[^\]\n]+\])\((file://[^)\n]*?)\)\s*[）)]'
    )
    # 这种比较复杂，需要重新组合
    def rep6_v2(m):
        line_range = m.group(1)
        text_part = m.group(2)
        url = m.group(3)
        anchor = make_anchor(line_range)
        if '#' in url:
            new = f'{text_part}({url})'
        else:
            new = f'{text_part}({url}{anchor})'
        logs.append(f"  [6] link 前行号转锚点")
        return new

    pat6_v2 = re.compile(
        r'第\s*(\d+(?:-\d+)?)\s*行\s*（\s*(\[[^\]\n]+\])\((file://[^)\n]*?)\)\s*）'
    )
    text = pat6_v2.sub(rep6_v2, text)

    # ===== 模式 7: ` 第 N-M 行`（反引号 URL + 行号）转为 markdown link =====
    # `file:///...path` 第 N-M 行
    def rep7(m):
        url = m.group(1)
        line_range = m.group(2)
        anchor = make_anchor(line_range)
        # 从 URL 中提取文件名作为 link 文本
        # URL 形如 file:///home/pbw/.../zephyr/include/zephyr/kernel.h
        fname = url.rsplit('/', 1)[-1] or "file"
        new = f'[{fname}]({url}{anchor})'
        logs.append(f"  [7] 反引号 URL 转为 markdown link")
        return new

    pat7 = re.compile(
        r'`((?:file|https?)://[^\s`]+?)`\s*第\s*(\d+(?:-\d+)?)\s*行'
    )
    text = pat7.sub(rep7, text)

    # ===== 模式 8: 代码注释 /* 第 N-M 行：xxx */ → /* xxx */ =====
    def rep8(m):
        logs.append(f"  [8] 简化代码注释（去行号）")
        return f'/* '

    pat8 = re.compile(
        r'/\*\s*第\s*\d+(?:-\d+)?\s*行\s*[：:]\s*'
    )
    text = pat8.sub(rep8, text)

    # ===== 模式 9: 代码注释 /* file_path L121-L127 */ → /* file_path */ =====
    # 也处理 /* file_path L121-L127（节选） */ → /* file_path（节选） */
    def rep9(m):
        prefix = m.group(1).rstrip()
        suffix = (m.group(2) or "").strip()
        if suffix:
            result = f'/* {prefix} {suffix} */'
        else:
            result = f'/* {prefix} */'
        logs.append(f"  [9] 代码注释去 L 行号")
        return result

    # 匹配 /* file_path L121-L127（节选） */ 这种形式
    # 要求前面是文件路径（含 .h/.c/.rst 等后缀），避免误伤其他代码
    pat9_v2 = re.compile(
        r'/\*\s*((?:[a-zA-Z_][\w./-]*\.(?:[ch]|rst|ld|cmake|py|txt|kconfig))[^\n]*?)\s+L\d+(?:-L\d+)?\s*([^\n]*?)\s*\*/'
    )
    text = pat9_v2.sub(rep9, text)

    # ===== 模式 10: 代码注释 /* 来源：xxx.rst 第 N-M 行 */ → /* 来源：xxx.rst */ =====
    def rep10(m):
        prefix = m.group(1)
        prefix = prefix.rstrip()
        logs.append(f"  [10] 来源注释去行号")
        return f'/* {prefix} */'

    pat10 = re.compile(
        r'/\*\s*(来源[：:][^\n]*?)\s+第\s*\d+(?:-\d+)?\s*行\s*\*/'
    )
    text = pat10.sub(rep10, text)

    # ===== 模式 8b: 代码注释 /* file_path 第 N-M 行 */ → /* file_path */ =====
    # 处理 /* include/zephyr/init.h 第 150 行 */ 这类
    def rep8b(m):
        prefix = m.group(1).rstrip()
        logs.append(f"  [8b] 代码注释去行号(文件路径)")
        return f'/* {prefix} */'

    pat8b = re.compile(
        r'/\*\s*((?:[a-zA-Z_][\w./-]*\.(?:[ch]|rst|ld|cmake|py|txt|kconfig))[^\n]*?)\s+第\s*\d+(?:-\d+)?\s*行\s*\*/'
    )
    text = pat8b.sub(rep8b, text)

    # ===== 模式 10b: 代码注释 /* 来自 xxx 第 N-M 行 */ → /* 来自 xxx */ =====
    def rep10b(m):
        prefix = m.group(1).rstrip()
        logs.append(f"  [10b] 代码注释去行号(来自)")
        return f'/* {prefix} */'

    pat10b = re.compile(
        r'/\*\s*(来自[^\n]*?)\s+第\s*\d+(?:-\d+)?\s*行\s*\*/'
    )
    text = pat10b.sub(rep10b, text)

    # ===== 模式 11: 括号内的行号引用 =====
    # 11a: （第 N 行） → 整个删除
    def rep11a(m):
        logs.append(f"  [11a] 删除括号内行号(单独)")
        return ''

    pat11a = re.compile(rf'（\s*{LINE_REF}\s*）')
    text = pat11a.sub(rep11a, text)

    # 11b: （第 N 行，X） → （X）
    def rep11b(m):
        rest = m.group(1).strip()
        logs.append(f"  [11b] 删除括号内行号(前置)")
        return f'（{rest}）'

    pat11b = re.compile(rf'（\s*{LINE_REF}\s*[，,]\s*([^）\n]+)）')
    text = pat11b.sub(rep11b, text)

    # 11c: （X，第 N 行） → （X）
    def rep11c(m):
        prefix = m.group(1).strip()
        logs.append(f"  [11c] 删除括号内行号(后置)")
        return f'（{prefix}）'

    pat11c = re.compile(rf'（\s*([^）\n，,]+?)\s*[，,]\s*{LINE_REF}\s*）')
    text = pat11c.sub(rep11c, text)

    # 11d: （X 第 N 行） → （X）（空格分隔，无逗号）
    def rep11d(m):
        prefix = m.group(1).strip()
        logs.append(f"  [11d] 删除括号内行号(空格分隔)")
        return f'（{prefix}）'

    pat11d = re.compile(rf'（\s*([^）\n]+?)\s+{LINE_REF}\s*）')
    text = pat11d.sub(rep11d, text)

    # 11e: （约 N 行） → 整个删除（带"约"前缀的近似行号引用）
    def rep11e(m):
        logs.append(f"  [11e] 删除括号内'约N行'")
        return ''

    pat11e = re.compile(r'（\s*约\s*\d+(?:-\d+)?\s*行(?:起)?\s*）')
    text = pat11e.sub(rep11e, text)

    # ===== 模式 12: "在第 N 行" → ""（正文中） =====
    def rep12(m):
        logs.append(f"  [12] 删除'在第N行'")
        return ''

    pat12 = re.compile(rf'在{LINE_REF}')
    text = pat12.sub(rep12, text)

    # ===== 模式 13: "第 N 行的 " → ""（所有格） =====
    def rep13(m):
        logs.append(f"  [13] 删除'第N行的'")
        return ''

    pat13 = re.compile(rf'{LINE_REF}的\s*')
    text = pat13.sub(rep13, text)

    # ===== 模式 14: "第 N 行" + 标点 → 保留标点，删行号 =====
    def rep14(m):
        logs.append(f"  [14] 删除'第N行'+保留标点")
        return m.group(1)

    # 匹配 "第 N 行" 后跟标点（：。，；）—），保留标点
    pat14 = re.compile(rf'{LINE_REF}\s*([：。，；）——])')
    text = pat14.sub(rep14, text)

    # ===== 模式 15: 兜底删除剩余 "第 N 行" =====
    # 先处理 "第 N 行附近/开始/结束/处" 等带后缀的情况
    def rep15a(m):
        logs.append(f"  [15a] 删除'第N行+后缀'")
        return ''

    pat15a = re.compile(rf'{LINE_REF}(?:附近|开始|结束|处)?')
    text = pat15a.sub(rep15a, text)

    def rep15(m):
        logs.append(f"  [15] 兜底删除'第N行'")
        return ''

    pat15 = re.compile(rf'{LINE_REF}')
    text = pat15.sub(rep15, text)

    # ===== 模式 16: 清理删除行号后遗留的多余破折号 =====
    # "；— " → "；" （分号后的破折号是行号引出的，行号删了破折号也该删）
    def rep16(m):
        logs.append(f"  [16] 清理多余破折号")
        return m.group(1)

    pat16 = re.compile(r'([；，])\s*—\s*')
    text = pat16.sub(rep16, text)

    if text != orig:
        logs.insert(0, f"  共修改 {len(logs)} 处")

    return text, logs


def main():
    dry_run = '--apply' not in sys.argv

    if dry_run:
        print("=== DRY RUN（不写入文件）===")
        print("如需实际写入，加 --apply 参数\n")
    else:
        print("=== APPLY（写入文件）===\n")

    total_changes = 0
    files_changed = 0

    for md_path in sorted(NOTES_DIR.glob("[0-9][0-9]-*.md")):
        orig = md_path.read_text(encoding="utf-8")
        new, logs = process_text(orig, md_path.name)

        if logs:
            print(f"📄 {md_path.name}")
            for log in logs:
                print(log)
            print()
            total_changes += len([l for l in logs if l.startswith("  [")])
            files_changed += 1

            if not dry_run:
                md_path.write_text(new, encoding="utf-8")

    print(f"\n总计：{files_changed} 个文件，{total_changes} 处修改")
    if dry_run:
        print("\n这是 dry-run，未写入文件。如需应用，运行：python3 dedupe_lineno.py --apply")


if __name__ == "__main__":
    main()
