#!/usr/bin/env python3
"""
Zephyr RTOS 学习笔记 → 静态 HTML 站点构建器

读取 ../zephyr-rtos/*.md，嵌入到单页 HTML 中，使用 marked.js + mermaid.js +
KaTeX + highlight.js 前端渲染。

用法：
    python3 build_html.py
输出：
    ./index.html
"""

import os
import re
import html
import json
from pathlib import Path

# 配置
NOTES_DIR = Path(__file__).parent.parent / "zephyr-rtos"
PROJECT_ROOT = Path(__file__).parent.parent  # 用于图片路径解析
OUTPUT_FILE = Path(__file__).parent / "index.html"
ZEPHYR_SRC_DIR = Path(__file__).parent.parent / "zephyr-project" / "zephyr"

# 文档顺序（按序号）
DOC_GLOB = "[0-9][0-9]-*.md"


def load_documents():
    """加载所有笔记 md 文件，返回 [(name, title, content, toc), ...]"""
    docs = []
    readme = NOTES_DIR / "README.md"
    if readme.exists():
        content = readme.read_text(encoding="utf-8")
        docs.append(("README", "README · 导航", content, extract_toc(content)))

    for md_path in sorted(NOTES_DIR.glob(DOC_GLOB)):
        name = md_path.stem  # e.g. "00-入门概览"
        content = md_path.read_text(encoding="utf-8")
        title = extract_title(content, name)
        toc = extract_toc(content)
        docs.append((name, title, content, toc))

    return docs


def extract_title(content, fallback):
    """从 md 内容提取 H1 标题"""
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def slugify(text):
    """生成锚点 id：保留中文/字母/数字/空格/连字符"""
    # 保留中文(\u4e00-\u9fa5)、字母、数字、空格、连字符
    s = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9\s\-]", "", text)
    s = re.sub(r"\s+", "-", s.strip())
    return s.lower()


def extract_toc(content):
    """提取 H2 标题作为目录，返回 [(id, text), ...]"""
    toc = []
    for line in content.splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            text = m.group(1).strip()
            anchor = slugify(text)
            toc.append((anchor, text))
    return toc


def escape_for_script(content):
    """转义 md 内容中可能破坏 <script> 标签的字符"""
    # 转义 </script>
    content = content.replace("</script>", "<\\/script>")
    # 转义 <!-- （防止 HTML 注释解析问题）
    return content


# ==================== 调用关系图提取 ====================

# C 控制流关键字（出现在 call 位置时一定是控制结构而非函数调用）
C_CONTROL_KEYWORDS = {
    "if", "while", "for", "switch", "return", "sizeof", "else", "do",
    "case", "break", "continue", "goto", "default", "defined",
}

# C 类型/存储类/限定符关键字（不可能作为函数名）
C_TYPE_KEYWORDS = {
    "void", "int", "char", "float", "double", "long", "short", "unsigned",
    "signed", "bool", "size_t", "ssize_t", "off_t",
    "int8_t", "int16_t", "int32_t", "int64_t",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "uintptr_t", "intptr_t",
    "struct", "enum", "union", "const", "volatile", "static", "inline",
    "extern", "register", "auto", "typedef",
}

# 属性宏（出现在函数定义前缀里，不会是函数名）
C_ATTR_MACROS = {
    "__boot_func", "FUNC_NORETURN", "__printf_like", "__used", "__noasan",
    "Z_DECL_ALIGN", "always_inline", "noinline", "__weak", "__naked",
}

# 常见伪函数（宏或运行时辅助），不应当作调用图节点
C_COMMON_MACROS = {
    "NULL", "true", "false",
    "container_of", "printk", "ARG_UNUSED", "BUILD_ASSERT",
    "CODE_UNREACHABLE", "ASSERT", "__ASSERT",
}

C_ALL_KEYWORDS = (
    C_CONTROL_KEYWORDS | C_TYPE_KEYWORDS | C_ATTR_MACROS | C_COMMON_MACROS
)

# callgraph_overrides.json 中作为元数据、不应当作函数的键
OVERRIDE_META_KEYS = {"说明", "格式", "示例", "使用方式", "_meta", "_comment"}


def strip_c_comments_and_strings(code):
    """剥离 C 代码中的注释与字符串字面量，保留行结构（换行符不变）。

    用于调用关系分析：避免把注释/字符串中的 identifier( 误识别为函数调用。
    返回的字符串与原 code 等长、行号对齐，便于后续按位置切片。
    """
    out = []
    i = 0
    n = len(code)
    in_line_cmt = False
    in_block_cmt = False
    in_str = False
    in_char = False
    while i < n:
        c = code[i]
        nxt = code[i + 1] if i + 1 < n else ""
        if in_line_cmt:
            if c == "\n":
                in_line_cmt = False
                out.append(c)
            else:
                out.append(" ")
        elif in_block_cmt:
            if c == "*" and nxt == "/":
                in_block_cmt = False
                out.append("  ")
                i += 2
                continue
            elif c == "\n":
                out.append(c)
            else:
                out.append(" ")
        elif in_str:
            if c == "\\" and nxt:
                out.append("  ")
                i += 2
                continue
            elif c == '"':
                in_str = False
                out.append('"')
            elif c == "\n":
                in_str = False
                out.append(c)
            else:
                out.append(" ")
        elif in_char:
            if c == "\\" and nxt:
                out.append("  ")
                i += 2
                continue
            elif c == "'":
                in_char = False
                out.append("'")
            elif c == "\n":
                in_char = False
                out.append(c)
            else:
                out.append(" ")
        else:
            if c == "/" and nxt == "/":
                in_line_cmt = True
                out.append("  ")
                i += 2
                continue
            elif c == "/" and nxt == "*":
                in_block_cmt = True
                out.append("  ")
                i += 2
                continue
            elif c == '"':
                in_str = True
                out.append('"')
            elif c == "'":
                in_char = True
                out.append("'")
            else:
                out.append(c)
        i += 1
    return "".join(out)


def find_matching(s, open_pos, open_ch, close_ch):
    """在 s 中从 open_pos（指向 open_ch）开始，找到配对的 close_ch 位置。

    处理嵌套。找不到返回 None。
    """
    depth = 0
    i = open_pos
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


# 函数定义候选正则：行首 + 至少一个"类型词 + 空白" + 可选 * + 函数名 + (
# 关键点：要求函数名前必须有"类型词+空白"，这天然排除
#   - 单独的 macro() 调用（无前缀类型）
#   - 控制语句 if/while/for（无前缀类型）
#   - 缩进的函数调用（行首是空白，正则 ^ 不匹配）
_FUNC_DEF_CANDIDATE = re.compile(
    r"^(?P<prefix>(?:[a-zA-Z_]\w*\s+)+\**\s*)"
    r"(?P<name>[a-zA-Z_]\w*)\s*\(",
    re.MULTILINE,
)


def find_function_definitions(code):
    """在 C 代码块中找出所有函数定义。

    判定标准：[修饰符/类型词]+ [*]* name ( params ) [const|__attribute__...] { body }
    即参数列表后必须紧跟 {（允许中间有 __attribute__ 等），而不能是 ; 或 =。

    返回: [{"name", "signature", "body", "full_text", "start", "end"}, ...]
    其中 start/end 是在原始 code 中的字符偏移。
    """
    cleaned = strip_c_comments_and_strings(code)
    funcs = []
    pos = 0
    n = len(cleaned)

    while pos < n:
        m = _FUNC_DEF_CANDIDATE.search(cleaned, pos)
        if not m:
            break

        name = m.group("name")
        # 跳过关键字（if/while/for/switch/return 等即使有前缀也不算函数定义）
        if name in C_ALL_KEYWORDS:
            pos = m.end()
            continue

        # 找到参数列表的右括号
        open_paren = m.end() - 1  # ( 的位置
        close_paren = find_matching(cleaned, open_paren, "(", ")")
        if close_paren is None:
            pos = m.end()
            continue

        # 在 ) 之后找第一个出现的 { ; 或 =
        # 函数定义要求 { 在 ; 和 = 之前出现
        after = cleaned[close_paren + 1:]
        next_brace = after.find("{")
        next_semi = after.find(";")
        next_eq = after.find("=")
        if next_brace == -1:
            # 没有 { —— 不是定义（可能是声明或调用）
            pos = m.end()
            continue
        # 如果 ; 或 = 出现在 { 之前，也不是函数定义
        # （= 出现说明是变量初始化，如 Z_DECL_ALIGN(struct x) ... = { ... };）
        if next_semi != -1 and next_semi < next_brace:
            pos = m.end()
            continue
        if next_eq != -1 and next_eq < next_brace:
            pos = m.end()
            continue

        body_open = close_paren + 1 + next_brace  # { 在 cleaned 中的位置
        body_close = find_matching(cleaned, body_open, "{", "}")
        if body_close is None:
            pos = m.end()
            continue

        signature = cleaned[m.start():close_paren + 1].strip()
        body = cleaned[body_open + 1:body_close]
        # full_text 取原始代码（保留注释），便于展示
        full_text = code[m.start():body_close + 1]

        funcs.append({
            "name": name,
            "signature": signature,
            "body": body,
            "full_text": full_text.strip(),
            "start": m.start(),
            "end": body_close + 1,
        })

        # 跳过整个函数体，避免在函数体内误匹配嵌套"定义"
        pos = body_close + 1

    return funcs


def extract_calls_from_body(body):
    """从函数体（已剥离注释/字符串的代码）中提取被调用函数名列表。

    返回按出现顺序去重的列表。
    """
    calls = []
    seen = set()
    for m in re.finditer(r"\b([a-zA-Z_]\w*)\s*\(", body):
        name = m.group(1)
        if name in C_ALL_KEYWORDS:
            continue
        if name not in seen:
            seen.add(name)
            calls.append(name)
    return calls


def extract_code_blocks(markdown_text):
    """从 markdown 中提取所有代码块，返回 [(lang, code, start_offset), ...]。

    start_offset 是代码块在 markdown_text 中的字符偏移，用于源码路径回溯。
    """
    blocks = []
    for m in re.finditer(r"```(\w*)\n(.*?)```", markdown_text, re.DOTALL):
        lang = m.group(1)
        code = m.group(2)
        blocks.append((lang, code, m.start()))
    return blocks


def find_source_path(markdown_text, code_block_start):
    """在代码块之前的内容中查找最近的 file:/// 源码链接。

    Zephyr 笔记的惯例是在代码块前一行用 [path](file:///...#Lxx-Lyy) 标注来源。
    返回 {"path": str, "line_start": int|None, "line_end": int|None} 或 None。
    """
    # 在代码块前 2000 个字符里找最后一个 file:/// 链接
    # （Zephyr 笔记常在代码块前用列表详述步骤，链接可能距代码块较远）
    window = markdown_text[max(0, code_block_start - 2000):code_block_start]
    # 匹配 [显示文本](file:///path#L123-L456) 或无行号的链接
    matches = list(re.finditer(
        r"\[[^\]]+\]\((file:///[^)]+?)(?:#L(\d+)(?:-L?(\d+))?)?\)",
        window,
    ))
    if not matches:
        return None
    m = matches[-1]
    path = m.group(1)
    line_start = int(m.group(2)) if m.group(2) else None
    line_end = int(m.group(3)) if m.group(3) else None
    return {"path": path, "line_start": line_start, "line_end": line_end}


def _is_arm_source(source):
    """判断 source 字典是否指向 ARM 架构相关源码。

    用于多定义函数时优先选择 ARM 版本展示（Zephyr 笔记以 Cortex-M 为主示例架构）。
    """
    if not source or not source.get("path"):
        return False
    p = source["path"].lower()
    return "arch/arm/" in p or "cortex_m" in p or "cortex-m" in p


def extract_call_graph(docs):
    """从所有文档的 C 代码块中提取函数调用关系。

    策略：
    1. 对每个 C 代码块，用 find_function_definitions 找出所有函数定义
       （能正确处理指针返回类型、多函数代码块、跳过宏调用/声明）
    2. 对每个函数定义，只从其函数体内提取调用（不跨函数边界）
    3. 捕获源码路径（file:/// 链接）

    返回:
        dict: {func_name: {
            "calls": [callee...],
            "called_by": [caller...],
            "defined_in": [doc...],     # 在哪些文档中有定义
            "referenced_in": [doc...],  # 在哪些文档中被调用但未定义
            "signature": str|None,
            "code": str|None,
            "source": {path, line_start, line_end}|None,
        }}
    """
    graph = {}

    def ensure_node(name):
        if name not in graph:
            graph[name] = {
                "calls": [],
                "called_by": [],
                "defined_in": [],
                "referenced_in": [],
                "signature": None,
                "code": None,
                "source": None,
            }
        return graph[name]

    for doc_name, _title, content, _toc in docs:
        if doc_name == "README":
            continue
        code_blocks = extract_code_blocks(content)

        for lang, code, block_offset in code_blocks:
            if lang != "c":
                continue

            defs = find_function_definitions(code)
            source = find_source_path(content, block_offset)

            for d in defs:
                fn = d["name"]
                node = ensure_node(fn)

                # 选择"最佳"定义来展示 signature/code/source：
                # - 优先 ARM 架构（source 路径含 arch/arm 或 cortex_m）
                # - 其次保留首次出现的定义
                # 判定当前定义是否 ARM 相关
                is_arm = _is_arm_source(source)
                # 判定已记录的是否 ARM 相关
                cur_is_arm = _is_arm_source(node["source"])

                if node["signature"] is None:
                    node["signature"] = d["signature"]
                elif is_arm and not cur_is_arm:
                    # 用 ARM 版本覆盖
                    node["signature"] = d["signature"]

                if node["code"] is None:
                    node["code"] = d["full_text"]
                elif is_arm and not cur_is_arm:
                    node["code"] = d["full_text"]

                if node["source"] is None:
                    node["source"] = source
                elif is_arm and not cur_is_arm:
                    node["source"] = source

                if doc_name not in node["defined_in"]:
                    node["defined_in"].append(doc_name)

                # 只从函数体内提取调用（不跨函数）
                calls = extract_calls_from_body(d["body"])
                for c in calls:
                    if c == fn:
                        continue  # 排除直接递归
                    if c not in node["calls"]:
                        node["calls"].append(c)
                    callee_node = ensure_node(c)
                    if doc_name not in callee_node["referenced_in"]:
                        callee_node["referenced_in"].append(doc_name)

    # 构建反向引用：谁调了我
    for func_name, node in graph.items():
        for callee in node["calls"]:
            if callee in graph:
                if func_name not in graph[callee]["called_by"]:
                    graph[callee]["called_by"].append(func_name)

    # 合并手工覆盖数据
    override_path = Path(__file__).parent / "callgraph_overrides.json"
    if override_path.exists():
        try:
            overrides = json.loads(override_path.read_text(encoding="utf-8"))
            real_count = 0
            for func_name, patch in overrides.items():
                # 跳过元数据键（说明/格式/示例/使用方式/_meta 等）
                if func_name in OVERRIDE_META_KEYS or func_name.startswith("_"):
                    continue
                if not isinstance(patch, dict):
                    continue
                real_count += 1
                node = ensure_node(func_name)
                for key in ("calls", "called_by", "defined_in", "referenced_in"):
                    if key in patch and isinstance(patch[key], list):
                        for v in patch[key]:
                            if v not in node[key]:
                                node[key].append(v)
                if "signature" in patch and node["signature"] is None:
                    node["signature"] = patch["signature"]
                if "code" in patch and node["code"] is None:
                    node["code"] = patch["code"]
                if "source" in patch and node["source"] is None:
                    node["source"] = patch["source"]
            print(f"  合并覆盖数据: {real_count} 个函数")
        except Exception as e:
            print(f"  警告: callgraph_overrides.json 解析失败: {e}")

    # 从 zephyr-project 源码补全缺失的函数定义
    if ZEPHYR_SRC_DIR.exists():
        filled = _fill_from_zephyr_source(graph)
        if filled:
            print(f"  从 zephyr-project 补全: {filled} 个函数定义")

    return graph


def _fill_from_zephyr_source(graph):
    """从 zephyr-project 源码目录补全笔记中缺失的函数定义。

    对于 graph 中没有 code 的函数，在 ZEPHYR_SRC_DIR 下搜索其定义，
    提取 signature、code、source 路径，并提取调用关系。
    优先搜索 arch/arm 目录（笔记以 Cortex-M 为主示例架构）。
    返回补全的函数数量。
    """
    # 收集需要补全的函数名
    missing = [name for name, node in graph.items()
               if node["code"] is None and node["referenced_in"]]

    if not missing:
        return 0

    # 构建文件索引：ARM .c > ARM .h > 其他 .c > 其他 .h
    # （.c 优先：完整函数定义；.h 补全：inline 函数定义，如 arch_curr_cpu）
    arm_c, arm_h, other_c, other_h = [], [], [], []
    for src_file in ZEPHYR_SRC_DIR.rglob("*"):
        if src_file.suffix not in (".c", ".h"):
            continue
        rel = src_file.relative_to(ZEPHYR_SRC_DIR).as_posix()
        is_arm = "arch/arm/" in rel or "cortex_m" in rel
        if src_file.suffix == ".c":
            (arm_c if is_arm else other_c).append(src_file)
        else:
            (arm_h if is_arm else other_h).append(src_file)
    # ARM .c 优先（完整定义），然后 ARM .h（inline 定义），然后其他
    ordered_files = arm_c + arm_h + other_c + other_h

    # 为每个缺失函数构建搜索正则
    # 匹配函数定义：行首 + 类型前缀 + 函数名 + (
    func_patterns = {}
    for name in missing:
        pattern = re.compile(
            r"^(?:[a-zA-Z_]\w*\s+)+\**\s*" + re.escape(name) + r"\s*\(",
            re.MULTILINE,
        )
        func_patterns[name] = pattern

    filled_count = 0
    remaining = set(missing)

    for src_file in ordered_files:
        if not remaining:
            break
        try:
            code = src_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for name in list(remaining):
            pattern = func_patterns[name]
            if not pattern.search(code):
                continue

            # 找到匹配，提取函数定义
            defs = find_function_definitions(code)
            matched_def = None
            for d in defs:
                if d["name"] == name:
                    matched_def = d
                    break

            if matched_def is None:
                continue

            node = graph[name]
            rel_path = src_file.relative_to(ZEPHYR_SRC_DIR).as_posix()
            file_uri = f"file://{src_file.resolve()}"

            # 计算行号
            line_start = code[:matched_def["start"]].count("\n") + 1
            line_end = line_start + matched_def["full_text"].count("\n")

            node["signature"] = matched_def["signature"]
            node["code"] = matched_def["full_text"]
            node["source"] = {
                "path": file_uri,
                "line_start": line_start,
                "line_end": line_end,
            }

            # 从函数体提取调用关系
            calls = extract_calls_from_body(matched_def["body"])
            for c in calls:
                if c == name:
                    continue
                if c not in node["calls"]:
                    node["calls"].append(c)
                # 确保 callee 节点存在
                if c not in graph:
                    graph[c] = {
                        "calls": [], "called_by": [],
                        "defined_in": [], "referenced_in": [],
                        "signature": None, "code": None, "source": None,
                    }
                if name not in graph[c]["called_by"]:
                    graph[c]["called_by"].append(name)

            remaining.discard(name)
            filled_count += 1

    return filled_count


def collect_source_snippets(docs):
    """收集文档中所有 file:/// 链接对应的源码片段。

    用于实现"点击源码链接在侧边栏展开源码"功能。
    只处理 .c 和 .h 文件链接。

    返回 {url_key: {content, line_start, line_end, short_path}}
    url_key 是链接的完整 href（file:///path#Lxx-Lyy 或 file:///path）
    """
    snippets = {}
    link_re = re.compile(r"\[[^\]]+\]\((file:///[^)]+)\)")

    for doc_name, _title, content, _toc in docs:
        for m in link_re.finditer(content):
            url = m.group(1)
            if url in snippets:
                continue

            # 解析路径和行号
            parts = url.split("#", 1)
            file_path = parts[0].replace("file://", "")

            # 只处理 .c 和 .h 文件
            if not file_path.endswith((".c", ".h")):
                continue

            if not os.path.exists(file_path):
                continue

            line_start = None
            line_end = None
            if len(parts) > 1:
                line_m = re.match(r"L(\d+)(?:-L?(\d+))?", parts[1])
                if line_m:
                    line_start = int(line_m.group(1))
                    line_end = int(line_m.group(2)) if line_m.group(2) else line_start

            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                continue

            if line_start and line_end:
                # 截取行范围（带上下文）
                ctx = 5
                start = max(1, line_start - ctx)
                end = min(len(lines), line_end + ctx)
                content_str = "".join(lines[start - 1:end])
                display_start = start
                display_end = end
            else:
                # 整个文件（太大则截断）
                if len(lines) > 300:
                    content_str = "".join(lines[:300])
                    content_str += "\n... (文件过长，仅显示前 300 行) ...\n"
                    display_start = 1
                    display_end = 300
                else:
                    content_str = "".join(lines)
                    display_start = 1
                    display_end = len(lines)

            # 短路径（相对于 zephyr-project/zephyr/）
            short_path = file_path
            if "/zephyr-project/zephyr/" in short_path:
                short_path = "zephyr/" + short_path.split("/zephyr-project/zephyr/", 1)[1]

            snippets[url] = {
                "content": content_str,
                "line_start": display_start,
                "line_end": display_end,
                "short_path": short_path,
            }

    return snippets


def build_sidebar(docs):
    """构建侧边栏 HTML"""
    html_parts = ['<nav class="sidebar" id="sidebar">']
    html_parts.append('<div class="sidebar-search">'
                      '<input type="text" id="search-input" '
                      'placeholder="搜索文档..." '
                      'oninput="filterSidebar()">'
                      '</div>')
    html_parts.append('<ul class="doc-list" id="doc-list">')

    for name, title, _, toc in docs:
        doc_id = name
        html_parts.append(
            f'<li class="doc-item" data-doc="{doc_id}" '
            f'data-title="{html.escape(title)}">'
            f'<a href="#/{doc_id}" class="doc-link" '
            f'onclick="loadDoc(\'{doc_id}\'); return false;">'
            f'{html.escape(title)}</a>'
        )
        if toc:
            html_parts.append('<ul class="toc-list">')
            for anchor, text in toc:
                html_parts.append(
                    f'<li><a href="#/{doc_id}#{anchor}" '
                    f'onclick="loadDoc(\'{doc_id}\', \'{anchor}\'); return false;">'
                    f'{html.escape(text)}</a></li>'
                )
            html_parts.append('</ul>')
        html_parts.append('</li>')

    html_parts.append('</ul></nav>')
    return "\n".join(html_parts)


def build_html(docs):
    """构建完整 HTML 文档"""
    # 嵌入所有 md 内容到 <script type="text/markdown"> 标签
    scripts = []
    for name, title, content, _ in docs:
        escaped = escape_for_script(content)
        scripts.append(
            f'<script type="text/markdown" data-name="{name}" '
            f'data-title="{html.escape(title)}">\n{escaped}\n</script>'
        )
    scripts_html = "\n".join(scripts)

    sidebar_html = build_sidebar(docs)

    # 提取调用关系图
    callgraph = extract_call_graph(docs)
    callgraph_json = json.dumps(callgraph, ensure_ascii=False, indent=2)

    # 收集源码片段（用于点击 file:/// 链接在侧边栏展开）
    source_snippets = collect_source_snippets(docs)
    source_snippets_json = json.dumps(source_snippets, ensure_ascii=False)

    return HTML_TEMPLATE.format(
        scripts=scripts_html,
        sidebar=sidebar_html,
        callgraph_json=callgraph_json,
        source_snippets_json=source_snippets_json,
        callgraph_js=CALLLGRAPH_JS,
        build_time=__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
        doc_count=len(docs),
    )


CALLLGRAPH_JS = r"""
// ============================================================
// 调用图浏览器 (Call Graph Explorer)
// 设计参考：VS Code Call Hierarchy + Source Insight
// ============================================================

var CG = (function () {
  // ---- 状态 ----
  var callgraph = {};
  var sourceSnippets = {};   // file:/// 链接对应的源码片段
  var drawer = null;
  var history = [];          // 导航历史：[{func, tab}, ...]
  var historyIdx = -1;       // 当前历史位置
  var currentFn = null;      // 当前展示的函数名
  var activeTab = 'calls';   // 'calls' | 'called_by'
  var MAX_TREE_DEPTH = 8;    // 递归调用树最大深度
  var SEARCH_LIMIT = 30;     // 搜索结果上限
  var focusStack = [];       // 聚焦历史，用于面包屑导航

  // C 关键字（用于代码块函数识别，与 Python 端保持一致）
  var KEYWORDS = new Set([
    'if','while','for','switch','return','sizeof','else','do','case',
    'break','continue','goto','default','defined',
    'void','int','char','float','double','long','short','unsigned',
    'signed','bool','size_t','ssize_t','off_t','int8_t','int16_t',
    'int32_t','int64_t','uint8_t','uint16_t','uint32_t','uint64_t',
    'uintptr_t','intptr_t','struct','enum','union','const','volatile',
    'static','inline','extern','register','auto','typedef',
    '__boot_func','FUNC_NORETURN','__printf_like','__used','__noasan',
    'Z_DECL_ALIGN','always_inline','noinline','__weak','__naked',
    'NULL','true','false','container_of','printk','ARG_UNUSED',
    'BUILD_ASSERT','CODE_UNREACHABLE','ASSERT','__ASSERT'
  ]);

  // ---- 初始化 ----
  function init() {
    try {
      var el = document.getElementById('cg-data');
      if (el) callgraph = JSON.parse(el.textContent);
    } catch (e) { console.warn('callgraph data load failed:', e); }
    try {
      var srcEl = document.getElementById('cg-source-data');
      if (srcEl) sourceSnippets = JSON.parse(srcEl.textContent);
    } catch (e) { console.warn('source snippets data load failed:', e); }
    buildDrawer();
    attachToCodeBlocks();
    attachToInlineRefs();
    attachToSourceLinks();
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && isOpen()) close();
      if (isOpen() && (e.altKey || e.metaKey)) {
        if (e.key === 'ArrowLeft') { e.preventDefault(); back(); }
        if (e.key === 'ArrowRight') { e.preventDefault(); forward(); }
      }
    });
  }

  // ---- 构建抽屉 DOM ----
  function buildDrawer() {
    // 遮罩层
    var backdrop = document.createElement('div');
    backdrop.className = 'cg-backdrop';
    backdrop.id = 'cg-backdrop';
    document.body.appendChild(backdrop);

    drawer = document.createElement('div');
    drawer.className = 'cg-drawer';
    drawer.id = 'cg-drawer';
    drawer.innerHTML =
      '<div class="cg-drawer-header">' +
        '<div class="cg-drawer-nav">' +
          '<button class="cg-nav-btn" id="cg-back" title="后退 (Alt+←)" disabled>←</button>' +
          '<button class="cg-nav-btn" id="cg-forward" title="前进 (Alt+→)" disabled>→</button>' +
          '<button class="cg-nav-btn" id="cg-root" title="返回根视图" disabled>\u2302</button>' +
        '</div>' +
        '<div class="cg-breadcrumb" id="cg-breadcrumb"></div>' +
        '<div class="cg-drawer-title" id="cg-title">调用图浏览器</div>' +
        '<button class="cg-drawer-close" id="cg-close" title="关闭 (Esc)">\u00d7</button>' +
      '</div>' +
      '<div class="cg-search-box">' +
        '<input type="text" id="cg-search-input" placeholder="\ud83d\udd0d 搜索函数..." autocomplete="off">' +
        '<div class="cg-search-results" id="cg-search-results"></div>' +
      '</div>' +
      '<div class="cg-drawer-body" id="cg-body">' +
        '<div class="cg-split-pane">' +
          '<div class="cg-pane-left" id="cg-pane-left"></div>' +
          '<div class="cg-pane-right" id="cg-pane-right">' +
            '<div class="cg-detail-placeholder">选择一个函数查看详情</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(drawer);

    // 点击遮罩关闭抽屉
    backdrop.addEventListener('click', close);

    document.getElementById('cg-close').onclick = close;
    document.getElementById('cg-back').onclick = back;
    document.getElementById('cg-forward').onclick = forward;
    document.getElementById('cg-root').onclick = backToRoot;

    var searchInput = document.getElementById('cg-search-input');
    searchInput.oninput = function () { doSearch(this.value); };
    searchInput.onfocus = function () {
      if (this.value) doSearch(this.value);
    };
    searchInput.onkeydown = function (e) {
      if (e.key === 'Enter') {
        var first = document.querySelector('#cg-search-results .cg-search-item');
        if (first) first.click();
      }
    };
    // 点击搜索结果外部关闭下拉
    document.addEventListener('click', function (e) {
      if (!e.target.closest('.cg-search-box')) {
        document.getElementById('cg-search-results').style.display = 'none';
      }
    });
  }

  function isOpen() { return drawer && drawer.classList.contains('open'); }

  function openDrawer() {
    if (!drawer) return;
    drawer.classList.add('open');
    document.body.classList.add('cg-drawer-open');
  }

  function close() {
    if (!drawer) return;
    drawer.classList.remove('open');
    document.body.classList.remove('cg-drawer-open');
    document.getElementById('cg-search-results').style.display = 'none';
  }

  // ---- 导航 ----
  function navigate(funcName, tab) {
    if (!callgraph[funcName]) return;
    // 截断 history（如果在中间位置又导航了新函数）
    if (historyIdx < history.length - 1) {
      history = history.slice(0, historyIdx + 1);
    }
    var node = callgraph[funcName];
    // 智能选择初始标签：如果未指定 tab，优先选有内容的方向
    if (!tab) {
      var hasCalls = node.calls && node.calls.length > 0;
      var hasCallers = node.called_by && node.called_by.length > 0;
      if (!hasCalls && hasCallers) {
        tab = 'called_by';
      } else {
        tab = 'calls';
      }
    }
    focusStack = [];  // 重置聚焦
    history.push({ func: funcName, tab: tab });
    historyIdx = history.length - 1;
    currentFn = funcName;
    activeTab = tab;
    openDrawer();
    render();
    updateNavButtons();
  }

  function back() {
    if (historyIdx <= 0) return;
    historyIdx--;
    var h = history[historyIdx];
    currentFn = h.func;
    activeTab = h.tab;
    render();
    updateNavButtons();
  }

  function forward() {
    if (historyIdx >= history.length - 1) return;
    historyIdx++;
    var h = history[historyIdx];
    currentFn = h.func;
    activeTab = h.tab;
    render();
    updateNavButtons();
  }

  function updateNavButtons() {
    document.getElementById('cg-back').disabled = historyIdx <= 0;
    document.getElementById('cg-forward').disabled = historyIdx >= history.length - 1;
  }

  // ---- 搜索 ----
  function doSearch(query) {
    var results = document.getElementById('cg-search-results');
    query = query.trim().toLowerCase();
    if (!query) { results.style.display = 'none'; return; }
    var matches = [];
    var count = 0;
    for (var fn in callgraph) {
      if (fn.toLowerCase().indexOf(query) !== -1) {
        matches.push(fn);
        count++;
        if (count >= SEARCH_LIMIT) break;
      }
    }
    if (matches.length === 0) {
      results.innerHTML = '<div class="cg-search-empty">\u65e0\u5339\u914d\u51fd\u6570</div>';
    } else {
      results.innerHTML = matches.map(function (fn) {
        var node = callgraph[fn];
        var doc = (node.defined_in && node.defined_in[0]) ||
                  (node.referenced_in && node.referenced_in[0]) || '';
        var isDefined = node.defined_in && node.defined_in.length > 0;
        return '<div class="cg-search-item" data-fn="' + fn + '">' +
          '<span class="cg-search-name' + (isDefined ? '' : ' cg-search-ext') + '">' + fn + '()</span>' +
          '<span class="cg-search-doc">' + doc + '</span>' +
        '</div>';
      }).join('');
      results.querySelectorAll('.cg-search-item').forEach(function (item) {
        item.onclick = function () {
          navigate(this.dataset.fn);
          document.getElementById('cg-search-input').value = '';
          results.style.display = 'none';
        };
      });
    }
    results.style.display = 'block';
  }

  // ---- 渲染 ----
  function render() {
    var node = callgraph[currentFn];
    if (!node) {
      document.getElementById('cg-pane-left').innerHTML = '<div class="cg-empty">\u672a\u627e\u5230\u51fd\u6570 ' + currentFn + '</div>';
      return;
    }
    renderTree();
    renderDetail(currentFn);
    renderBreadcrumb();
    updateNavButtons();
  }

  // ---- 渲染左栏：调用树 ----
  function renderTree() {
    var paneLeft = document.getElementById('cg-pane-left');
    var node = callgraph[currentFn];
    if (!node) return;

    var html = '';

    // 标签栏
    var calls = node.calls || [];
    var callers = node.called_by || [];
    var activeList = activeTab === 'calls' ? calls : callers;
    var treeIsEmpty = activeList.length === 0;
    var bothEmpty = calls.length === 0 && callers.length === 0;
    html += '<div class="cg-tabs">';
    html += '<button class="cg-tab' + (activeTab === 'calls' ? ' active' : '') + '" data-tab="calls">' +
      '\u8c03\u7528 (' + calls.length + ')</button>';
    html += '<button class="cg-tab' + (activeTab === 'called_by' ? ' active' : '') + '" data-tab="called_by">' +
      '\u88ab\u8c03\u7528 (' + callers.length + ')</button>';
    html += '</div>';

    // 树视图 / 空状态
    html += '<div class="cg-tree-container">';
    if (bothEmpty && !node.code) {
      html += '<div class="cg-empty-state">';
      html += '<div class="cg-empty-state-icon">\u2139\ufe0f</div>';
      html += '<div class="cg-empty-state-title">\u6b64\u51fd\u6570\u5728\u7b14\u8bb0\u4e2d\u4ec5\u88ab\u5f15\u7528</div>';
      html += '<div class="cg-empty-state-desc">\u672a\u5c55\u793a\u5176\u5b9a\u4e49\u4ee3\u7801\u6216\u8c03\u7528\u5173\u7cfb\u3002</div>';
      html += '</div>';
    } else if (treeIsEmpty) {
      var otherTab = activeTab === 'calls' ? 'called_by' : 'calls';
      var otherList = activeTab === 'calls' ? callers : calls;
      var otherLabel = activeTab === 'calls' ? '\u8c03\u7528\u8005' : '\u5b50\u8c03\u7528';
      html += '<div class="cg-empty-state">';
      html += '<div class="cg-empty-state-title">\u65e0 ' + (activeTab === 'calls' ? '\u5b50\u8c03\u7528' : '\u8c03\u7528\u8005') + '</div>';
      if (otherList.length > 0) {
        html += '<div class="cg-empty-state-hint">\u6b64\u51fd\u6570\u6709 ' + otherList.length +
          ' \u4e2a' + otherLabel + '\uff0c' +
          '<a class="cg-switch-tab" data-tab="' + otherTab + '">\u5207\u6362\u67e5\u770b</a></div>';
      }
      html += '</div>';
    } else {
      if (activeTab === 'calls') {
        html += renderTreeHtml(currentFn, 'calls', new Set(), 0);
      } else {
        html += renderTreeHtml(currentFn, 'called_by', new Set(), 0);
      }
    }
    html += '</div>';

    paneLeft.innerHTML = html;

    // 绑定事件
    bindTreeEvents(paneLeft);
    bindTabEvents(paneLeft);
  }

  // ---- 渲染右栏：函数详情 ----
  function renderDetail(funcName) {
    var paneRight = document.getElementById('cg-pane-right');
    var node = callgraph[funcName];
    if (!node) {
      paneRight.innerHTML = '<div class="cg-detail-placeholder">\u9009\u62e9\u4e00\u4e2a\u51fd\u6570\u67e5\u770b\u8be6\u60c5</div>';
      return;
    }

    var html = '<div class="cg-detail">';

    // 标题
    html += '<div class="cg-detail-header">';
    html += '<h3>' + funcName + '()</h3>';
    // 文档链接
    var docs = node.defined_in || [];
    var refs = node.referenced_in || [];
    if (docs.length > 0) {
      html += '<div class="cg-detail-docs">';
      html += '<span class="cg-meta-label">\u5b9a\u4e49\u4e8e</span> ';
      html += docs.map(function (d) { return '<span class="cg-doc-tag" data-doc="' + d + '">' + d + '</span>'; }).join('');
      html += '</div>';
    }
    if (refs.length > 0 && refs.join() !== docs.join()) {
      var refOnly = refs.filter(function (r) { return docs.indexOf(r) === -1; });
      if (refOnly.length > 0) {
        html += '<div class="cg-detail-docs">';
        html += '<span class="cg-meta-label">\u5f15\u7528\u4e8e</span> ';
        html += refOnly.slice(0, 5).map(function (d) { return '<span class="cg-doc-tag cg-doc-ext">' + d + '</span>'; }).join('');
        if (refOnly.length > 5) {
          html += '<span class="cg-more">+' + (refOnly.length - 5) + '</span>';
        }
        html += '</div>';
      }
    }
    html += '</div>';

    // 签名
    if (node.signature) {
      html += '<div class="cg-detail-signature"><code>' + escapeHtml(node.signature) + '</code></div>';
    }

    // 源码链接
    if (node.source && node.source.path) {
      var srcText = node.source.path.replace(/^file:\/\//, '');
      var shortPath = srcText.split('/zephyr-project/').pop() || srcText;
      var lineInfo = '';
      if (node.source.line_start) {
        lineInfo = '#L' + node.source.line_start +
          (node.source.line_end ? '-L' + node.source.line_end : '');
      }
      html += '<div class="cg-detail-source-link">';
      html += '<a class="cg-src-link" href="' + node.source.path + lineInfo + '" target="_blank" title="' + srcText + lineInfo + '">' +
        shortPath + lineInfo + ' \u2197</a>';
      html += '</div>';
    }

    // 源码
    if (node.code) {
      html += '<div class="cg-detail-code-section">';
      html += '<div class="cg-detail-section-title">\u6e90\u7801</div>';
      html += '<pre class="cg-detail-pre"><code class="language-c">' + escapeHtml(node.code) + '</code></pre>';
      html += '</div>';
    }

    // 调用关系列表
    var calls = node.calls || [];
    var callers = node.called_by || [];
    if (calls.length > 0 || callers.length > 0) {
      html += '<div class="cg-detail-relations">';
      if (calls.length > 0) {
        html += '<div class="cg-detail-section-title">\u8c03\u7528 (' + calls.length + ')</div>';
        html += '<div class="cg-detail-fn-list">';
        calls.forEach(function(fn) {
          html += '<span class="cg-detail-fn" data-fn="' + fn + '">' + fn + '()</span>';
        });
        html += '</div>';
      }
      if (callers.length > 0) {
        html += '<div class="cg-detail-section-title">\u88ab\u8c03\u7528 (' + callers.length + ')</div>';
        html += '<div class="cg-detail-fn-list">';
        callers.forEach(function(fn) {
          html += '<span class="cg-detail-fn" data-fn="' + fn + '">' + fn + '()</span>';
        });
        html += '</div>';
      }
      html += '</div>';
    }

    html += '</div>';
    paneRight.innerHTML = html;

    // 高亮代码
    var codeEl = paneRight.querySelector('.cg-detail-pre code');
    if (codeEl && window.hljs) {
      try {
        var result = hljs.highlight(codeEl.textContent, { language: 'c' });
        codeEl.innerHTML = result.value;
      } catch (e) {}
    }

    // 绑定文档标签点击事件
    bindDocTagEvents(paneRight);

    // 绑定关系列表点击事件
    paneRight.querySelectorAll('.cg-detail-fn').forEach(function(fnEl) {
      fnEl.onclick = function() {
        navigate(this.dataset.fn, activeTab);
      };
    });
  }

  // ---- 递归渲染调用树 HTML ----
  function renderTreeHtml(funcName, direction, visited, depth) {
    var node = callgraph[funcName];
    var items = node ? (direction === 'calls' ? node.calls : node.called_by) : [];
    if (!items || items.length === 0) {
      return '<div class="cg-tree-empty">(\u65e0 ' + (direction === 'calls' ? '\u5b50\u8c03\u7528' : '\u8c03\u7528\u8005') + ')</div>';
    }
    visited = new Set(visited);
    visited.add(funcName);
    var html = '<ul class="cg-tree' + (depth === 0 ? ' cg-tree-root' : '') + '">';
    for (var i = 0; i < items.length; i++) {
      var name = items[i];
      var childNode = callgraph[name];
      var childItems = childNode ? (direction === 'calls' ? childNode.calls : childNode.called_by) : [];
      var hasChildren = childItems && childItems.length > 0;
      var isCycle = visited.has(name);
      var hasCode = childNode && childNode.code;
      var isDefined = childNode && childNode.defined_in && childNode.defined_in.length > 0;

      html += '<li class="cg-node' + (hasChildren && !isCycle ? ' cg-node-expandable' : '') + '">';
      // 展开/折叠三角
      if (hasChildren && !isCycle) {
        html += '<span class="cg-toggle" data-fn="' + name + '" data-dir="' + direction + '" data-depth="' + depth + '">\u25b6</span>';
      } else {
        html += '<span class="cg-toggle-placeholder"></span>';
      }
      // 函数名
      html += '<span class="cg-fn-link' +
        (isDefined ? '' : ' cg-fn-external') +
        (hasCode ? '' : ' cg-fn-nocode') +
        '" data-fn="' + name + '" title="' + name + '()">' + name + '()</span>';
      // 徽章
      if (isCycle) {
        html += '<span class="cg-badge cg-badge-cycle">\u21bb \u5faa\u73af</span>';
      } else if (hasChildren) {
        html += '<span class="cg-badge">' + childItems.length + '</span>';
      } else if (!childNode) {
        html += '<span class="cg-badge cg-badge-undef">\u5916\u90e8</span>';
      } else if (hasCode) {
        // 叶子节点但有代码，显示 { } 图标提示可展开
        html += '<span class="cg-badge cg-badge-code" title="\u5355\u51fb\u67e5\u770b\u6e90\u7801">{ }</span>';
      }
      html += '</li>';
    }
    html += '</ul>';
    return html;
  }

  // ---- 树事件绑定 ----
  function bindTreeEvents(container) {
    // 函数名单击 → 右栏显示详情（不跳转）
    container.querySelectorAll('.cg-fn-link').forEach(function (el) {
      el.onclick = function (e) {
        e.stopPropagation();
        var fn = this.dataset.fn;
        // 更新选中状态
        container.querySelectorAll('.cg-fn-link').forEach(function(l) {
          l.classList.remove('cg-fn-selected');
        });
        this.classList.add('cg-fn-selected');
        // 右栏显示详情
        renderDetail(fn);
      };
      // 函数名双击 → 聚焦模式（以该函数为根重建左栏树）
      el.ondblclick = function (e) {
        e.stopPropagation();
        focusOn(this.dataset.fn);
      };
    });
    // 展开/折叠三角
    container.querySelectorAll('.cg-toggle').forEach(function (el) {
      el.onclick = function (e) {
        e.stopPropagation();
        toggleTreeNode(this);
      };
    });
  }

  function toggleTreeNode(toggleEl) {
    var li = toggleEl.parentElement;
    var fn = toggleEl.dataset.fn;
    var dir = toggleEl.dataset.dir;
    var depth = parseInt(toggleEl.dataset.depth);
    var existingSubTree = li.querySelector(':scope > ul.cg-tree');
    if (existingSubTree) {
      // 已渲染过，切换显示
      existingSubTree.classList.toggle('collapsed');
      toggleEl.textContent = existingSubTree.classList.contains('collapsed') ? '\u25b6' : '\u25bc';
      toggleEl.classList.toggle('expanded');
      return;
    }
    // 深度限制
    if (depth >= MAX_TREE_DEPTH) {
      toggleEl.textContent = '\u26d4';
      toggleEl.title = '\u5df2\u8fbe\u6700\u5927\u6df1\u5ea6';
      return;
    }
    // 构建子树 visited 集合：从当前 li 向上收集所有祖先函数名
    var visited = new Set();
    var cur = li.parentElement;
    while (cur && cur !== document.getElementById('cg-body')) {
      if (cur.classList && cur.classList.contains('cg-node')) {
        var link = cur.querySelector(':scope > .cg-fn-link');
        if (link) visited.add(link.dataset.fn);
      }
      cur = cur.parentElement;
    }
    // 加入当前函数（树的根）
    visited.add(currentFn);
    var subHtml = renderTreeHtml(fn, dir, visited, depth + 1);
    if (subHtml) {
      li.insertAdjacentHTML('beforeend', subHtml);
      bindTreeEvents(li);
      toggleEl.textContent = '\u25bc';
      toggleEl.classList.add('expanded');
    }
  }

  // ---- 内联展开/折叠函数信息 ----
  function toggleInlineExpand(fnLinkEl) {
    var li = fnLinkEl.parentElement;
    var fn = fnLinkEl.dataset.fn;
    var node = callgraph[fn];
    if (!node) return;

    // 查找已有的内联展开区域
    var existingInline = li.querySelector(':scope > .cg-inline-expand');

    if (existingInline) {
      // 已展开 → 折叠
      existingInline.remove();
      fnLinkEl.classList.remove('cg-fn-expanded');
      // 同时折叠子树（如果有）
      var toggle = li.querySelector(':scope > .cg-toggle');
      if (toggle && toggle.classList.contains('expanded')) {
        var subTree = li.querySelector(':scope > ul.cg-tree');
        if (subTree) {
          subTree.classList.add('collapsed');
          toggle.textContent = '\u25b6';
          toggle.classList.remove('expanded');
        }
      }
      return;
    }

    // 未展开 → 创建内联展开区域
    var inlineHtml = '<div class="cg-inline-expand">';

    // 1. 函数签名
    if (node.signature) {
      inlineHtml += '<div class="cg-inline-signature">' +
        escapeHtml(node.signature) + '</div>';
    }

    // 2. 源码（可折叠）
    if (node.code) {
      inlineHtml += '<details class="cg-inline-code-section">';
      inlineHtml += '<summary>\u6e90\u7801</summary>';
      inlineHtml += '<pre class="cg-inline-code"><code class="language-c">' +
        escapeHtml(node.code) + '</code></pre>';
      inlineHtml += '</details>';
    }

    // 3. 源码链接
    if (node.source && node.source.path) {
      var srcText = node.source.path.replace(/^file:\/\//, '');
      var shortPath = srcText.split('/zephyr-project/').pop() || srcText;
      var lineInfo = '';
      if (node.source.line_start) {
        lineInfo = '#L' + node.source.line_start +
          (node.source.line_end ? '-L' + node.source.line_end : '');
      }
      inlineHtml += '<div class="cg-inline-source-link">' +
        '<a href="' + node.source.path + lineInfo + '" target="_blank">' +
        shortPath + lineInfo + ' \u2197</a></div>';
    }

    inlineHtml += '</div>';

    // 插入到 li 末尾（在子树之前）
    li.insertAdjacentHTML('beforeend', inlineHtml);
    fnLinkEl.classList.add('cg-fn-expanded');

    // 高亮代码
    var codeEl = li.querySelector('.cg-inline-code code');
    if (codeEl && window.hljs) {
      try {
        var result = hljs.highlight(codeEl.textContent, { language: 'c' });
        codeEl.innerHTML = result.value;
      } catch (e) {}
    }

    // 同时展开子树（如果有子调用且未展开）
    var toggle = li.querySelector(':scope > .cg-toggle');
    if (toggle && !li.querySelector(':scope > ul.cg-tree')) {
      toggleTreeNode(toggle);
    }
  }

  // ---- 聚焦模式 ----
  function focusOn(funcName) {
    if (!callgraph[funcName]) return;
    focusStack.push(funcName);
    currentFn = funcName;
    render();
  }

  function backToRoot() {
    focusStack = [];
    if (history.length > 0) {
      currentFn = history[0].func;
      activeTab = history[0].tab;
    }
    render();
  }

  // ---- 面包屑导航 ----
  function renderBreadcrumb() {
    var el = document.getElementById('cg-breadcrumb');
    if (!el) return;
    var html = '';
    if (focusStack.length > 0) {
      html += '<span class="cg-breadcrumb-root" id="cg-bc-root" title="返回根视图">⌂</span>';
      html += '<span class="cg-breadcrumb-sep">/</span>';
      focusStack.forEach(function(fn, i) {
        if (i > 0) html += '<span class="cg-breadcrumb-sep">›</span>';
        if (i === focusStack.length - 1) {
          html += '<span class="cg-breadcrumb-current">' + fn + '()</span>';
        } else {
          html += '<span class="cg-breadcrumb-link" data-fn="' + fn + '">' + fn + '()</span>';
        }
      });
    }
    el.innerHTML = html;
    var rootBtn = el.querySelector('#cg-bc-root');
    if (rootBtn) rootBtn.onclick = backToRoot;
    el.querySelectorAll('.cg-breadcrumb-link').forEach(function(link) {
      link.onclick = function() {
        var fn = this.dataset.fn;
        var idx = focusStack.indexOf(fn);
        if (idx >= 0) focusStack = focusStack.slice(0, idx + 1);
        currentFn = fn;
        render();
      };
    });
  }

  // ---- 标签切换 ----
  function bindTabEvents(container) {
    container.querySelectorAll('.cg-tab').forEach(function (tab) {
      tab.onclick = function () {
        activeTab = this.dataset.tab;
        // 更新历史中当前位置的 tab
        if (historyIdx >= 0 && historyIdx < history.length) {
          history[historyIdx].tab = activeTab;
        }
        render();
      };
    });
    // 空状态中的"切换查看"链接
    container.querySelectorAll('.cg-switch-tab').forEach(function (link) {
      link.onclick = function (e) {
        e.preventDefault();
        activeTab = this.dataset.tab;
        if (historyIdx >= 0 && historyIdx < history.length) {
          history[historyIdx].tab = activeTab;
        }
        render();
      };
    });
  }

  // ---- 文档标签点击 → 跳转到该文档 ----
  function bindDocTagEvents(container) {
    container.querySelectorAll('.cg-doc-tag').forEach(function (tag) {
      tag.onclick = function () {
        var doc = this.dataset.doc;
        if (typeof loadDoc === 'function') {
          close();
          loadDoc(doc);
        }
      };
    });
  }

  // ---- 内联：代码块下方添加调用图按钮 ----
  function attachToCodeBlocks() {
    document.querySelectorAll('.content pre > code').forEach(function (codeEl) {
      var pre = codeEl.parentElement;
      if (pre.dataset.cgDone) return;
      pre.dataset.cgDone = '1';
      var code = codeEl.textContent;
      var fns = detectFuncDefs(code);
      if (fns.length === 0) return;
      // 只为 callgraph 中存在的函数添加按钮
      var known = fns.filter(function (fn) { return callgraph[fn]; });
      if (known.length === 0) return;
      var btn = document.createElement('div');
      btn.className = 'cg-btn';
      var label = known.length === 1
        ? '\u{1F50E} \u8c03\u7528\u56fe: ' + known[0] + '()'
        : '\u{1F50E} \u8c03\u7528\u56fe (' + known.length + ' \u4e2a\u51fd\u6570)';
      btn.textContent = label;
      btn.title = known.join(', ');
      btn.onclick = function (e) {
        e.stopPropagation();
        navigate(known[0]);
      };
      pre.parentElement.insertBefore(btn, pre.nextSibling);
    });
  }

  // ---- 内联：函数名引用可点击 ----
  function attachToInlineRefs() {
    document.querySelectorAll('.content code').forEach(function (codeEl) {
      if (codeEl.dataset.cgLinked) return;
      // 跳过代码块内的 code（已由 attachToCodeBlocks 处理）
      if (codeEl.parentElement && codeEl.parentElement.tagName === 'PRE') return;
      var text = codeEl.textContent.trim();
      // 匹配 func_name() 或 func_name  形式
      var m = text.match(/^([a-zA-Z_]\w*)\s*\(\s*\)?$/);
      if (!m) {
        m = text.match(/^([a-zA-Z_]\w*)\s*\(/);
      }
      if (!m) return;
      var name = m[1];
      if (KEYWORDS.has(name)) return;
      if (!callgraph[name]) return;
      codeEl.dataset.cgLinked = '1';
      codeEl.classList.add('cg-inline-link');
      codeEl.title = name + '() \u2014 \u70b9\u51fb\u67e5\u770b\u8c03\u7528\u56fe';
      codeEl.onclick = function (e) {
        e.stopPropagation();
        navigate(name);
      };
    });
  }

  // ---- 源码文件链接：点击在侧边栏展开 ----
  function navigateToSource(urlKey) {
    var snippet = sourceSnippets[urlKey];
    if (!snippet) return false;

    openDrawer();

    // 更新标题
    var titleEl = document.getElementById('cg-title');
    if (titleEl) titleEl.textContent = snippet.short_path;

    // 左栏显示文件信息
    var paneLeft = document.getElementById('cg-pane-left');
    if (paneLeft) {
      paneLeft.innerHTML =
        '<div class="cg-file-info">' +
          '<div class="cg-file-info-icon">\uD83D\uDCC4</div>' +
          '<div class="cg-file-info-path">' + escapeHtml(snippet.short_path) + '</div>' +
          '<div class="cg-file-info-lines">\u884c ' + snippet.line_start + '-' + snippet.line_end + '</div>' +
          '<div class="cg-file-info-hint">\u6E90\u7801\u6587\u4EF6\u89C6\u56FE</div>' +
        '</div>';
    }

    // 右栏渲染源码
    renderFileView(snippet);

    // 清空导航历史
    history = [];
    historyIdx = -1;
    currentFn = null;
    updateNavButtons();

    return true;
  }

  function renderFileView(snippet) {
    var paneRight = document.getElementById('cg-pane-right');
    if (!paneRight) return;

    var html = '<div class="cg-detail cg-file-view">';
    html += '<div class="cg-detail-header">';
    html += '<h3>' + escapeHtml(snippet.short_path) + '</h3>';
    html += '<div class="cg-detail-docs">';
    html += '<span class="cg-meta-label">\u884c ' + snippet.line_start + '-' + snippet.line_end + '</span>';
    html += '</div>';
    html += '</div>';
    html += '<div class="cg-detail-code-section">';
    html += '<div class="cg-detail-section-title">\u6E90\u7801</div>';
    html += '<pre class="cg-detail-pre cg-file-pre"><code class="language-c">' + escapeHtml(snippet.content) + '</code></pre>';
    html += '</div>';
    html += '</div>';
    paneRight.innerHTML = html;

    // 高亮代码
    var codeEl = paneRight.querySelector('.cg-detail-pre code');
    if (codeEl && window.hljs) {
      try {
        var result = hljs.highlight(codeEl.textContent, { language: 'c' });
        codeEl.innerHTML = result.value;
      } catch (e) {}
    }
  }

  function attachToSourceLinks() {
    document.querySelectorAll('.content a[href^="file://"]').forEach(function (a) {
      if (a.dataset.cgSrcLinked) return;
      var href = a.getAttribute('href');
      if (!sourceSnippets[href]) return;
      a.dataset.cgSrcLinked = '1';
      a.classList.add('cg-src-link-inline');
      a.removeAttribute('target');
      a.title = '\u70B9\u51FB\u5728\u4FA7\u8FB9\u680F\u67E5\u770B\u6E90\u7801';
      a.addEventListener('click', function (e) {
        e.preventDefault();
        navigateToSource(href);
      });
    });
  }

  // ---- C 代码块函数定义检测（与 Python 端逻辑对齐） ----
  function stripCComments(code) {
    var out = [];
    var i = 0, n = code.length;
    var inLine = false, inBlock = false, inStr = false, inChar = false;
    while (i < n) {
      var c = code[i], nxt = i + 1 < n ? code[i + 1] : '';
      if (inLine) {
        if (c === '\n') { inLine = false; out.push(c); } else out.push(' ');
      } else if (inBlock) {
        if (c === '*' && nxt === '/') { inBlock = false; out.push('  '); i += 2; continue; }
        else if (c === '\n') out.push(c); else out.push(' ');
      } else if (inStr) {
        if (c === '\\' && nxt) { out.push('  '); i += 2; continue; }
        else if (c === '"') { inStr = false; out.push('"'); }
        else if (c === '\n') { inStr = false; out.push(c); }
        else out.push(' ');
      } else if (inChar) {
        if (c === '\\' && nxt) { out.push('  '); i += 2; continue; }
        else if (c === "'") { inChar = false; out.push("'"); }
        else if (c === '\n') { inChar = false; out.push(c); }
        else out.push(' ');
      } else {
        if (c === '/' && nxt === '/') { inLine = true; out.push('  '); i += 2; continue; }
        else if (c === '/' && nxt === '*') { inBlock = true; out.push('  '); i += 2; continue; }
        else if (c === '"') { inStr = true; out.push('"'); }
        else if (c === "'") { inChar = true; out.push("'"); }
        else out.push(c);
      }
      i++;
    }
    return out.join('');
  }

  function findMatching(s, openPos, openCh, closeCh) {
    var depth = 0;
    for (var i = openPos; i < s.length; i++) {
      if (s[i] === openCh) depth++;
      else if (s[i] === closeCh) { depth--; if (depth === 0) return i; }
    }
    return -1;
  }

  function detectFuncDefs(code) {
    var cleaned = stripCComments(code);
    var results = [];
    var re = /^(?:(?:[a-zA-Z_]\w*\s+)+\**\s*)([a-zA-Z_]\w*)\s*\(/gm;
    var m;
    while ((m = re.exec(cleaned)) !== null) {
      var name = m[1];
      if (KEYWORDS.has(name)) { re.lastIndex = m.index + 1; continue; }
      var openParen = m.index + m[0].length - 1;
      var closeParen = findMatching(cleaned, openParen, '(', ')');
      if (closeParen === -1) { re.lastIndex = m.index + 1; continue; }
      var after = cleaned.substring(closeParen + 1);
      var nextBrace = after.indexOf('{');
      var nextSemi = after.indexOf(';');
      var nextEq = after.indexOf('=');
      if (nextBrace === -1) { re.lastIndex = m.index + 1; continue; }
      if (nextSemi !== -1 && nextSemi < nextBrace) { re.lastIndex = m.index + 1; continue; }
      if (nextEq !== -1 && nextEq < nextBrace) { re.lastIndex = m.index + 1; continue; }
      var bodyOpen = closeParen + 1 + nextBrace;
      var bodyClose = findMatching(cleaned, bodyOpen, '{', '}');
      if (bodyClose === -1) { re.lastIndex = m.index + 1; continue; }
      results.push(name);
      re.lastIndex = bodyClose + 1;
    }
    return results;
  }

  // ---- 工具 ----
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  // ---- 公开 API ----
  return {
    init: init,
    open: navigate,
    openDrawer: function () {
      if (currentFn) openDrawer();
      else {
        // 没有当前函数时，打开抽屉并聚焦搜索框
        openDrawer();
        setTimeout(function () {
          document.getElementById('cg-search-input').focus();
        }, 100);
      }
    },
    close: close,
    refresh: function () {
      attachToCodeBlocks();
      attachToInlineRefs();
      attachToSourceLinks();
    }
  };
})();

// 供 loadDoc 调用
function initCallGraph() { CG.refresh(); }
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>Zephyr RTOS 学习笔记</title>

<!-- KaTeX CSS -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">

<!-- highlight.js CSS (atom-one-light 兼容暗黑模式切换) -->
<link id="hljs-light" rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-light.min.css">
<link id="hljs-dark" rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css" disabled>

<style>
:root {{
  --bg: #fafbfc;
  --fg: #1e293b;
  --fg-muted: #64748b;
  --border: #e2e8f0;
  --accent: #2563eb;
  --accent-hover: #1d4ed8;
  --code-bg: #f1f5f9;
  --code-fg: #334155;
  --sidebar-bg: #ffffff;
  --sidebar-hover: #f1f5f9;
  --blockquote-bg: #f8fafc;
  --blockquote-border: #94a3b8;
  --table-header-bg: #f1f5f9;
  --table-stripe: #f8fafc;
  --search-bg: #f1f5f9;
  --core-point-bg: #fef3c7;
  --core-point-border: #d97706;
  --bridge-bg: #cffafe;
  --bridge-border: #0891b2;
  --howto-bg: #dbeafe;
  --howto-border: #2563eb;
}}

[data-theme="dark"] {{
  --bg: #0f172a;
  --fg: #f1f5f9;
  --fg-muted: #cbd5e1;
  --border: #475569;
  --accent: #60a5fa;
  --accent-hover: #93c5fd;
  --code-bg: #1e293b;
  --code-fg: #f1f5f9;
  --sidebar-bg: #1e293b;
  --sidebar-hover: #334155;
  --blockquote-bg: #1e293b;
  --blockquote-border: #475569;
  --table-header-bg: #1e293b;
  --table-stripe: #1e293b;
  --search-bg: #1e293b;
  --core-point-bg: #451a03;
  --core-point-border: #f59e0b;
  --bridge-bg: #0c4a6e;
  --bridge-border: #06b6d4;
  --howto-bg: #1e3a8a;
  --howto-border: #3b82f6;
}}

* {{
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}}

html {{
  background: var(--bg);
}}

html, body {{
  min-height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
               "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  font-size: 15px;
  line-height: 1.7;
  background: var(--bg);
  color: var(--fg);
  transition: background 0.2s, color 0.2s;
}}

/* 顶部工具栏 */
.toolbar {{
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: 48px;
  background: var(--sidebar-bg);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  padding: 0 16px;
  z-index: 100;
  gap: 12px;
}}

.toolbar-title {{
  font-weight: 600;
  font-size: 15px;
  color: var(--fg);
}}

.toolbar-spacer {{
  flex: 1;
}}

.toolbar-btn {{
  background: none;
  border: 1px solid var(--border);
  color: var(--fg);
  padding: 6px 12px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 4px;
}}

.toolbar-btn:hover {{
  background: var(--sidebar-hover);
}}

/* 侧边栏 */
.sidebar {{
  position: fixed;
  top: 48px;
  left: 0;
  bottom: 0;
  width: 300px;
  background: var(--sidebar-bg);
  border-right: 1px solid var(--border);
  overflow-y: auto;
  padding: 12px 0;
  z-index: 50;
  transition: transform 0.2s;
}}

.sidebar-search {{
  padding: 0 12px 12px;
}}

.sidebar-search input {{
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--search-bg);
  color: var(--fg);
  font-size: 13px;
  outline: none;
}}

.sidebar-search input:focus {{
  border-color: var(--accent);
}}

.doc-list {{
  list-style: none;
}}

.doc-item {{
  margin: 0;
}}

.doc-link {{
  display: block;
  padding: 8px 16px;
  color: var(--fg);
  text-decoration: none;
  font-weight: 500;
  font-size: 14px;
  border-left: 3px solid transparent;
  transition: all 0.1s;
}}

.doc-link:hover {{
  background: var(--sidebar-hover);
  border-left-color: var(--accent);
}}

.doc-item.active .doc-link {{
  background: var(--sidebar-hover);
  border-left-color: var(--accent);
  color: var(--accent);
}}

.toc-list {{
  list-style: none;
  margin: 0 0 8px 0;
  padding: 0;
}}

.toc-list a {{
  display: block;
  padding: 4px 16px 4px 28px;
  color: var(--fg-muted);
  text-decoration: none;
  font-size: 13px;
  line-height: 1.4;
}}

.toc-list a:hover {{
  color: var(--accent);
  background: var(--sidebar-hover);
}}

/* 内容区 */
.content {{
  margin-top: 48px;
  margin-left: 300px;
  padding: 32px 48px 80px;
  max-width: 1100px;
  min-height: calc(100vh - 48px);
  color: var(--fg);
  background: var(--bg);
}}

.content h1 {{
  font-size: 28px;
  margin: 0 0 8px;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--border);
  color: var(--fg);
}}

.content h2 {{
  font-size: 22px;
  margin: 32px 0 12px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border);
  color: var(--fg);
  scroll-margin-top: 64px;
}}

.content h3 {{
  font-size: 18px;
  margin: 24px 0 8px;
  color: var(--fg);
  scroll-margin-top: 64px;
}}

.content h4 {{
  font-size: 16px;
  margin: 20px 0 6px;
  color: var(--fg);
  scroll-margin-top: 64px;
}}

.content p {{
  margin: 8px 0;
}}

.content ul, .content ol {{
  margin: 8px 0;
  padding-left: 24px;
}}

.content li {{
  margin: 4px 0;
}}

.content a {{
  color: var(--accent);
  text-decoration: none;
}}

.content a:hover {{
  color: var(--accent-hover);
  text-decoration: underline;
}}

.content code {{
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 0.9em;
  background: var(--code-bg);
  color: var(--code-fg);
  padding: 2px 6px;
  border-radius: 4px;
}}

.content pre {{
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0;
  margin: 12px 0;
  overflow: hidden;
  position: relative;
}}

.content pre > code {{
  display: block;
  padding: 12px 16px;
  background: transparent;
  color: var(--code-fg);
  font-size: 13px;
  line-height: 1.5;
  overflow-x: auto;
}}

/* 代码折叠 */
.content pre.collapsible > .code-toggle {{
  display: block;
  background: var(--sidebar-hover);
  border-top: 1px solid var(--border);
  padding: 4px 12px;
  text-align: center;
  cursor: pointer;
  font-size: 12px;
  color: var(--fg-muted);
  user-select: none;
}}

.content pre.collapsible > .code-toggle:hover {{
  background: var(--sidebar-hover);
  color: var(--accent);
}}

.content pre.collapsible.collapsed > code {{
  max-height: 200px;
  overflow: hidden;
  position: relative;
}}

.content pre.collapsible.collapsed > code::after {{
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 60px;
  background: linear-gradient(to bottom, transparent, var(--code-bg));
  pointer-events: none;
}}

/* 引用块 */
.content blockquote {{
  background: var(--blockquote-bg);
  border-left: 4px solid var(--blockquote-border);
  padding: 12px 16px;
  margin: 12px 0;
  border-radius: 0 6px 6px 0;
  color: var(--fg);
}}

.content blockquote p {{
  margin: 4px 0;
}}

/* 特殊引用块样式（核心要点、桥接段、如何读） */
.content blockquote > p:first-child > strong:first-child {{
  display: inline-block;
  margin-bottom: 4px;
}}

/* 表格 */
.content table {{
  border-collapse: collapse;
  width: 100%;
  margin: 16px 0;
  font-size: 14px;
}}

.content th, .content td {{
  border: 1px solid var(--border);
  padding: 8px 12px;
  text-align: left;
}}

.content th {{
  background: var(--table-header-bg);
  font-weight: 600;
}}

.content td {{
  background: var(--bg);
}}

.content tr:nth-child(even) td {{
  background: var(--table-stripe);
}}

/* 图片与 figure */
.content img {{
  max-width: 100%;
  height: auto;
  display: block;
  margin: 16px auto;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px;
  background: var(--sidebar-bg);
}}

.content figure {{
  margin: 16px 0;
}}

.content figcaption {{
  text-align: center;
  font-size: 0.9em;
  color: var(--fg-muted);
  margin-top: 8px;
}}

.content figcaption em {{
  color: inherit;
}}

.content em {{
  color: var(--fg-muted);
  font-size: 0.92em;
}}

/* Mermaid 图 */
.content .mermaid {{
  background: var(--sidebar-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 16px;
  margin: 16px 0;
  text-align: center;
}}

.content .mermaid svg {{
  max-width: 100%;
  height: auto;
}}

/* 数学公式 */
.content .katex-display {{
  margin: 16px 0;
  overflow-x: auto;
  overflow-y: hidden;
  padding: 8px 0;
}}

/* 回到顶部 */
.back-to-top {{
  position: fixed;
  bottom: 32px;
  right: 32px;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--accent);
  color: white;
  border: none;
  cursor: pointer;
  font-size: 20px;
  display: none;
  align-items: center;
  justify-content: center;
  z-index: 90;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}}

.back-to-top.visible {{
  display: flex;
}}

/* 加载状态 */
.loading {{
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 64px;
  color: var(--fg-muted);
  font-size: 14px;
}}

.loading::before {{
  content: '';
  display: inline-block;
  width: 16px;
  height: 16px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-right: 8px;
}}

@keyframes spin {{
  to {{ transform: rotate(360deg); }}
}}

/* 移动端响应式 */
@media (max-width: 900px) {{
  .sidebar {{
    transform: translateX(-100%);
    box-shadow: 2px 0 8px rgba(0,0,0,0.1);
  }}
  .sidebar.open {{
    transform: translateX(0);
  }}
  .content {{
    margin-left: 0;
    padding: 24px 16px 80px;
  }}
  .menu-toggle {{
    display: flex !important;
  }}
}}

.menu-toggle {{
  display: none;
  background: none;
  border: none;
  color: var(--fg);
  font-size: 20px;
  cursor: pointer;
  padding: 4px 8px;
}}

/* 引用块特殊样式（核心要点/桥接/如何读） */
.content blockquote.core-point {{
  background: var(--core-point-bg);
  border-left-color: var(--core-point-border);
}}

.content blockquote.bridge {{
  background: var(--bridge-bg);
  border-left-color: var(--bridge-border);
}}

.content blockquote.howto {{
  background: var(--howto-bg);
  border-left-color: var(--howto-border);
}}

/* 脚注样式 */
.content .footnote {{
  font-size: 0.85em;
  color: var(--fg-muted);
  border-top: 1px solid var(--border);
  padding-top: 8px;
  margin-top: 24px;
}}

/* hr */
.content hr {{
  border: none;
  border-top: 1px solid var(--border);
  margin: 24px 0;
}}

/* ===== 暗黑模式覆盖 ===== */
[data-theme="dark"] .content img {{
  background: transparent;
  padding: 0;
  border-color: var(--border);
}}

[data-theme="dark"] .content figcaption {{
  color: var(--fg-muted);
}}

[data-theme="dark"] .content em {{
  color: var(--fg-muted);
}}

[data-theme="dark"] .content strong {{
  color: var(--fg);
}}

[data-theme="dark"] .content blockquote {{
  color: var(--fg);
}}

[data-theme="dark"] .content blockquote p {{
  color: var(--fg);
}}

[data-theme="dark"] .content blockquote strong {{
  color: var(--fg);
}}

[data-theme="dark"] .content td {{
  background: var(--bg);
}}

[data-theme="dark"] .content tr:nth-child(even) td {{
  background: var(--table-stripe);
}}

[data-theme="dark"] .content .mermaid {{
  background: #0f172a;
}}

[data-theme="dark"] .content .mermaid svg {{
  filter: brightness(0.95);
}}

/* build info */
.build-info {{
  position: fixed;
  bottom: 8px;
  right: 16px;
  font-size: 11px;
  color: var(--fg-muted);
  opacity: 0.5;
  z-index: 1;
}}

/* ===== 调用关系图：内联按钮 + 抽屉 ===== */

/* 代码块下方的「调用图」按钮 */
.cg-btn {{
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin: 4px 0 8px;
  padding: 4px 12px;
  font-size: 12px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--sidebar-hover);
  color: var(--fg-muted);
  cursor: pointer;
  user-select: none;
  transition: all 0.15s;
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
}}
.cg-btn:hover {{
  color: var(--accent);
  border-color: var(--accent);
  background: var(--howto-bg);
}}

/* 正文中的函数名可点击链接 */
.cg-inline-link {{
  cursor: pointer !important;
  border-bottom: 1px dashed var(--accent);
}}
.cg-inline-link:hover {{
  background: var(--howto-bg) !important;
  color: var(--accent) !important;
}}

/* 正文中的源码文件链接 */
.cg-src-link-inline {{
  cursor: pointer !important;
  border-bottom: 1px dashed var(--accent) !important;
}}
.cg-src-link-inline:hover {{
  background: var(--howto-bg) !important;
  color: var(--accent) !important;
}}

/* 文件视图：左栏信息卡片 */
.cg-file-info {{
  padding: 24px 16px;
  text-align: center;
}}
.cg-file-info-icon {{
  font-size: 32px;
  margin-bottom: 8px;
}}
.cg-file-info-path {{
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 13px;
  color: var(--fg);
  word-break: break-all;
  margin-bottom: 4px;
}}
.cg-file-info-lines {{
  font-size: 12px;
  color: var(--fg-muted);
  margin-bottom: 12px;
}}
.cg-file-info-hint {{
  font-size: 11px;
  color: var(--fg-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}}

/* ---- 遮罩层 ---- */
.cg-backdrop {{
  position: fixed;
  top: 48px;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0,0,0,0.25);
  z-index: 140;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.25s ease;
}}
body.cg-drawer-open .cg-backdrop {{
  opacity: 1;
  pointer-events: auto;
}}

/* ---- 右侧抽屉（overlay 模式） ---- */
.cg-drawer {{
  position: fixed;
  top: 48px;
  right: 0;
  bottom: 0;
  width: 1280px;
  max-width: 85vw;
  background: var(--sidebar-bg);
  border-left: 1px solid var(--border);
  box-shadow: -4px 0 32px rgba(0,0,0,0.12);
  z-index: 150;
  display: flex;
  flex-direction: column;
  transform: translateX(100%);
  transition: transform 0.25s ease;
}}
.cg-drawer.open {{
  transform: translateX(0);
}}

.cg-drawer-header {{
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--sidebar-bg);
  flex-shrink: 0;
}}
.cg-drawer-nav {{
  display: flex;
  gap: 2px;
}}
.cg-nav-btn {{
  background: none;
  border: 1px solid var(--border);
  color: var(--fg);
  width: 28px;
  height: 28px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.1s;
}}
.cg-nav-btn:hover:not(:disabled) {{
  background: var(--sidebar-hover);
  border-color: var(--accent);
  color: var(--accent);
}}
.cg-nav-btn:disabled {{
  opacity: 0.3;
  cursor: default;
}}
.cg-drawer-title {{
  flex: 1;
  font-size: 14px;
  font-weight: 600;
  color: var(--fg);
}}
.cg-drawer-close {{
  background: none;
  border: none;
  color: var(--fg-muted);
  font-size: 22px;
  cursor: pointer;
  width: 32px;
  height: 32px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}}
.cg-drawer-close:hover {{
  background: var(--sidebar-hover);
  color: var(--fg);
}}

/* ---- 搜索框 ---- */
.cg-search-box {{
  position: relative;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}}
.cg-search-box input {{
  width: 100%;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--search-bg);
  color: var(--fg);
  font-size: 13px;
  outline: none;
}}
.cg-search-box input:focus {{
  border-color: var(--accent);
}}
.cg-search-results {{
  display: none;
  position: absolute;
  top: 100%;
  left: 12px;
  right: 12px;
  max-height: 320px;
  overflow-y: auto;
  background: var(--sidebar-bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.12);
  z-index: 10;
}}
.cg-search-item {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  cursor: pointer;
  border-bottom: 1px solid var(--border);
}}
.cg-search-item:last-child {{
  border-bottom: none;
}}
.cg-search-item:hover {{
  background: var(--sidebar-hover);
}}
.cg-search-name {{
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 13px;
  color: var(--accent);
}}
.cg-search-name.cg-search-ext {{
  color: var(--fg-muted);
}}
.cg-search-doc {{
  font-size: 11px;
  color: var(--fg-muted);
}}
.cg-search-empty {{
  padding: 12px;
  text-align: center;
  color: var(--fg-muted);
  font-size: 13px;
}}

/* ---- 抽屉主体 ---- */
.cg-drawer-body {{
  flex: 1;
  overflow: hidden;
  padding: 0;
  display: flex;
  flex-direction: column;
}}

.cg-empty {{
  padding: 32px;
  text-align: center;
  color: var(--fg-muted);
  font-size: 14px;
}}

/* ---- 函数信息区 ---- */
.cg-info {{
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}}
.cg-fn-name-lg {{
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 18px;
  font-weight: 600;
  color: var(--fg);
  margin-bottom: 4px;
}}
.cg-signature {{
  margin: 4px 0 8px;
  padding: 8px 10px;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  overflow-x: auto;
}}
.cg-signature code {{
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 12px;
  color: var(--code-fg);
  background: none;
  padding: 0;
  white-space: pre;
}}
.cg-meta-row {{
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  margin: 4px 0;
  font-size: 12px;
}}
.cg-meta-label {{
  color: var(--fg-muted);
  font-weight: 600;
  margin-right: 4px;
}}
.cg-doc-tag {{
  display: inline-block;
  padding: 1px 8px;
  background: var(--howto-bg);
  border: 1px solid var(--howto-border);
  border-radius: 10px;
  font-size: 11px;
  color: var(--fg);
  cursor: pointer;
  transition: all 0.1s;
}}
.cg-doc-tag:hover {{
  background: var(--accent);
  color: white;
  border-color: var(--accent);
}}
.cg-doc-tag.cg-doc-ext {{
  background: var(--blockquote-bg);
  border-color: var(--blockquote-border);
}}
.cg-more {{
  font-size: 11px;
  color: var(--fg-muted);
  margin-left: 4px;
}}
.cg-src-link {{
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 11px;
  color: var(--accent);
  text-decoration: none;
}}
.cg-src-link:hover {{
  text-decoration: underline;
}}

/* ---- 标签栏 ---- */
.cg-tabs {{
  display: flex;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}}
.cg-tab {{
  flex: 1;
  padding: 8px 12px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--fg-muted);
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  transition: all 0.1s;
}}
.cg-tab:hover {{
  color: var(--fg);
  background: var(--sidebar-hover);
}}
.cg-tab.active {{
  color: var(--accent);
  border-bottom-color: var(--accent);
}}

/* ---- 调用树 ---- */
.cg-tree-container {{
  padding: 8px 0;
  flex: 1;
}}
.cg-tree {{
  list-style: none;
  margin: 0;
  padding: 0 0 0 20px;
  border-left: 1px solid var(--border);
}}
.cg-tree.cg-tree-root {{
  padding-left: 16px;
  margin-left: 16px;
}}
.cg-tree.collapsed {{
  display: none;
}}
.cg-tree-empty {{
  padding: 8px 16px;
  color: var(--fg-muted);
  font-size: 12px;
  font-style: italic;
}}

/* ---- 空状态（叶子节点 / 仅引用函数） ---- */
.cg-empty-state {{
  padding: 20px 16px;
  text-align: center;
}}
.cg-empty-state-icon {{
  font-size: 28px;
  margin-bottom: 8px;
}}
.cg-empty-state-title {{
  font-size: 14px;
  font-weight: 600;
  color: var(--fg);
  margin-bottom: 6px;
}}
.cg-empty-state-desc {{
  font-size: 12px;
  color: var(--fg-muted);
  line-height: 1.6;
  margin-bottom: 10px;
}}
.cg-empty-state-hint {{
  font-size: 12px;
  color: var(--fg-muted);
  margin: 6px 0;
}}
.cg-empty-state-hint a {{
  color: var(--accent);
  cursor: pointer;
  text-decoration: underline;
}}
.cg-empty-state-refs {{
  margin-top: 12px;
  text-align: left;
}}
.cg-empty-state-refs-label {{
  font-size: 11px;
  color: var(--fg-muted);
  margin-bottom: 6px;
}}
.cg-empty-state-refs-list {{
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}}
.cg-node {{
  position: relative;
  margin: 1px 0;
  line-height: 1.8;
}}
.cg-node::before {{
  content: '';
  position: absolute;
  left: -20px;
  top: 14px;
  width: 16px;
  height: 0;
  border-top: 1px solid var(--border);
}}
.cg-toggle {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  font-size: 10px;
  cursor: pointer;
  color: var(--fg-muted);
  flex-shrink: 0;
  margin-right: 2px;
  transition: color 0.1s;
}}
.cg-toggle:hover {{
  color: var(--accent);
}}
.cg-toggle.expanded {{
  /* 视觉反馈由文本字符变化体现 */
}}
.cg-toggle-placeholder {{
  display: inline-block;
  width: 16px;
  flex-shrink: 0;
}}
.cg-fn-link {{
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 13px;
  cursor: pointer;
  color: var(--accent);
  padding: 1px 4px;
  border-radius: 3px;
  transition: background 0.1s;
}}
.cg-fn-link:hover {{
  background: var(--sidebar-hover);
  text-decoration: underline;
}}
.cg-fn-link.cg-fn-external {{
  color: var(--fg-muted);
}}
.cg-fn-link.cg-fn-nocode {{
  border-bottom: 1px dashed var(--fg-muted);
}}
.cg-badge {{
  display: inline-block;
  font-size: 10px;
  padding: 0 6px;
  border-radius: 8px;
  background: var(--code-bg);
  color: var(--fg-muted);
  margin-left: 4px;
  line-height: 1.5;
}}
.cg-badge-cycle {{
  background: var(--core-point-bg);
  color: var(--core-point-border);
}}
.cg-badge-undef {{
  background: var(--blockquote-bg);
  color: var(--fg-muted);
  font-style: italic;
}}
.cg-badge-code {{
  background: var(--code-bg);
  color: var(--accent);
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 11px;
  font-weight: 600;
}}

/* ---- 函数名展开状态 ---- */
.cg-fn-expanded {{
  background: var(--howto-bg);
  font-weight: 600;
}}

/* ---- 内联展开区域 ---- */
.cg-inline-expand {{
  margin: 8px 0 8px 24px;
  padding: 12px;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
}}
.cg-inline-signature {{
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 11px;
  color: var(--code-fg);
  margin-bottom: 8px;
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.5;
}}
.cg-inline-code-section {{
  margin: 8px 0;
}}
.cg-inline-code-section summary {{
  cursor: pointer;
  font-size: 11px;
  color: var(--fg-muted);
  user-select: none;
  padding: 2px 0;
}}
.cg-inline-code-section summary:hover {{
  color: var(--accent);
}}
.cg-inline-code {{
  margin: 6px 0 0 0;
  padding: 8px 10px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  overflow-x: auto;
  max-height: 300px;
  overflow-y: auto;
  font-size: 11px;
  line-height: 1.5;
}}
.cg-inline-code code {{
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  color: var(--code-fg);
  background: none;
  white-space: pre;
}}
.cg-inline-source-link {{
  margin-top: 8px;
  font-size: 11px;
}}
.cg-inline-source-link a {{
  color: var(--accent);
  text-decoration: none;
}}
.cg-inline-source-link a:hover {{
  text-decoration: underline;
}}

/* ---- 代码区 ---- */
.cg-code-section {{
  border-top: 1px solid var(--border);
  margin: 0;
}}
.cg-code-section summary {{
  padding: 8px 16px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  color: var(--fg);
  user-select: none;
  border-bottom: 1px solid transparent;
  transition: all 0.1s;
}}
.cg-code-section summary:hover {{
  background: var(--sidebar-hover);
}}
.cg-code-section[open] summary {{
  border-bottom-color: var(--border);
}}
.cg-code {{
  margin: 0;
  border: none;
  border-radius: 0;
  background: var(--code-bg);
  overflow-x: auto;
  max-height: 50vh;
  overflow-y: auto;
}}
.cg-code > code {{
  display: block;
  padding: 10px 14px;
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: var(--code-fg);
  white-space: pre;
  background: none;
}}

/* ---- 分栏布局 ---- */
.cg-split-pane {{
  display: flex;
  flex: 1;
  overflow: hidden;
  min-height: 0;
}}
.cg-pane-left {{
  width: 340px;
  min-width: 280px;
  overflow-y: auto;
  border-right: 1px solid var(--border);
  padding: 0;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}}
.cg-pane-right {{
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 0;
}}

/* ---- 左栏选中状态 ---- */
.cg-fn-selected {{
  background: var(--howto-bg);
  font-weight: 600;
}}

/* ---- 右栏详情面板 ---- */
.cg-detail {{
  padding: 16px;
}}
.cg-detail-header {{
  margin-bottom: 12px;
}}
.cg-detail-header h3 {{
  margin: 0 0 8px 0;
  font-size: 18px;
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  color: var(--fg);
}}
.cg-detail-docs {{
  margin: 6px 0;
  font-size: 12px;
}}
.cg-detail-signature {{
  padding: 8px 12px;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  margin: 12px 0;
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 12px;
  overflow-x: auto;
}}
.cg-detail-signature code {{
  color: var(--code-fg);
  background: none;
  white-space: pre;
}}
.cg-detail-source-link {{
  margin: 8px 0;
  font-size: 12px;
}}
.cg-detail-code-section {{
  margin: 16px 0;
}}
.cg-detail-section-title {{
  font-size: 12px;
  font-weight: 600;
  color: var(--fg-muted);
  margin: 12px 0 6px 0;
  text-transform: uppercase;
}}
.cg-detail-pre {{
  margin: 0;
  padding: 12px;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
  font-size: 12px;
  line-height: 1.5;
}}
.cg-detail-pre code {{
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  color: var(--code-fg);
  background: none;
  white-space: pre;
}}
/* 文件视图代码块：允许更高 */
.cg-file-pre {{
  max-height: 70vh;
}}
.cg-detail-relations {{
  margin-top: 16px;
}}
.cg-detail-fn-list {{
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}}
.cg-detail-fn {{
  padding: 2px 8px;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 3px;
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.1s;
}}
.cg-detail-fn:hover {{
  background: var(--howto-bg);
  border-color: var(--accent);
}}

/* ---- 面包屑 ---- */
.cg-breadcrumb {{
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  font-size: 12px;
  font-family: "JetBrains Mono", monospace;
  color: var(--fg-muted);
  flex-wrap: wrap;
  min-height: 20px;
}}
.cg-breadcrumb-sep {{
  color: var(--border);
  margin: 0 2px;
}}
.cg-breadcrumb-link {{
  cursor: pointer;
  color: var(--accent);
  padding: 2px 4px;
  border-radius: 3px;
  transition: background 0.1s;
}}
.cg-breadcrumb-link:hover {{
  background: var(--sidebar-hover);
  text-decoration: underline;
}}
.cg-breadcrumb-current {{
  font-weight: 600;
  color: var(--fg);
  padding: 2px 4px;
}}
.cg-breadcrumb-root {{
  cursor: pointer;
  font-size: 14px;
  padding: 2px 6px;
  border-radius: 3px;
  transition: background 0.1s;
}}
.cg-breadcrumb-root:hover {{
  background: var(--sidebar-hover);
}}

/* ---- 详情占位符 ---- */
.cg-detail-placeholder {{
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--fg-muted);
  font-size: 14px;
  padding: 32px;
}}

/* ---- 左栏树容器调整 ---- */
.cg-pane-left .cg-tree-container {{
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}}
.cg-pane-left .cg-tabs {{
  flex-shrink: 0;
}}

/* ---- 暗黑模式 ---- */
[data-theme="dark"] .cg-drawer {{
  box-shadow: -4px 0 24px rgba(0,0,0,0.4);
}}
[data-theme="dark"] .cg-search-results {{
  box-shadow: 0 4px 12px rgba(0,0,0,0.4);
}}

/* ---- 移动端 ---- */
@media (max-width: 1200px) {{
  .cg-drawer {{
    width: 100vw;
    max-width: 100vw;
  }}
  .cg-split-pane {{
    flex-direction: column;
  }}
  .cg-pane-left {{
    width: 100%;
    min-width: 0;
    max-width: none;
    max-height: 40vh;
    border-right: none;
    border-bottom: 1px solid var(--border);
  }}
  .cg-pane-right {{
    width: 100%;
    min-width: 0;
  }}
}}

</style>
</head>
<body data-theme="light">

<div class="toolbar">
  <button class="menu-toggle" onclick="toggleSidebar()">☰</button>
  <span class="toolbar-title">📘 Zephyr RTOS 学习笔记</span>
  <span class="toolbar-spacer"></span>
  <button class="toolbar-btn" onclick="CG.openDrawer()" title="打开调用图浏览器（搜索函数、查看调用/被调用关系）">🔗 调用图</button>
  <button class="toolbar-btn" onclick="toggleTheme()" id="theme-btn">🌙 暗黑</button>
  <button class="toolbar-btn" onclick="expandAllCode()">展开全部代码</button>
  <button class="toolbar-btn" onclick="collapseAllCode()">折叠全部代码</button>
</div>

{sidebar}

<main class="content" id="content">
  <div class="loading">正在加载文档...</div>
</main>

<button class="back-to-top" id="back-to-top" onclick="scrollTop()">↑</button>

<div class="build-info">构建时间：{build_time} · 共 {doc_count} 篇文档</div>

{scripts}

<!-- 调用关系图数据 -->
<script id="cg-data" type="application/json">
{callgraph_json}
</script>

<!-- 源码片段数据（用于点击 file:/// 链接在侧边栏展开） -->
<script id="cg-source-data" type="application/json">
{source_snippets_json}
</script>

<!-- marked.js -->
<script src="https://cdn.jsdelivr.net/npm/marked@11.1.1/marked.min.js"></script>
<!-- highlight.js -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<!-- KaTeX -->
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"></script>
<!-- Mermaid -->
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10.4.0/dist/mermaid.esm.min.mjs";
window.mermaid = mermaid;
window.mermaidReady = true;
window.dispatchEvent(new Event("mermaid-loaded"));
</script>

<script>
// ========== 文档数据 ==========
const docs = {{}};
const docTitles = {{}};

// 加载所有嵌入的 md 内容
document.querySelectorAll('script[type="text/markdown"]').forEach(s => {{
  docs[s.dataset.name] = s.textContent;
  docTitles[s.dataset.name] = s.dataset.title;
}});

// ========== Marked 配置 ==========
marked.setOptions({{
  breaks: false,
  gfm: true,
}});

// 自定义 renderer：处理图片路径、链接、引用块
const renderer = new marked.Renderer();

const origImage = renderer.image.bind(renderer);
renderer.image = function(href, title, text) {{
  // 处理相对路径：../zephyr-project/... → 从站点根目录出发
  let src = href;
  if (src.startsWith('../zephyr-project/')) {{
    // HTML 站点在 zephyr-rtos-html/，所以 ../zephyr-project 就是上级目录
    src = href;  // 保持相对路径，浏览器会从 html 文件所在目录解析
  }}
  const titleAttr = title ? ` title="${{title}}"` : '';
  return `<figure><img src="${{src}}" alt="${{text}}"${{titleAttr}}><figcaption>${{text || title || ''}}</figcaption></figure>`;
}};

const origLink = renderer.link.bind(renderer);
renderer.link = function(href, title, text) {{
  // 处理 .md 链接 → 文档切换
  let hrefStr = String(href);
  const mdMatch = hrefStr.match(/^\.\/([0-9A-Za-z\u4e00-\u9fa5_-]+)\.md(#.*)?$/);
  if (mdMatch) {{
    const targetDoc = mdMatch[1];
    const anchor = mdMatch[2] || '';
    return `<a href="#/${{targetDoc}}${{anchor}}" onclick="loadDoc('${{targetDoc}}', '${{anchor.replace('#', '')}}'); return false;" title="${{title || ''}}">${{text}}</a>`;
  }}
  // file:// 链接保持原样
  // 其他链接：新窗口打开
  if (hrefStr.startsWith('http') || hrefStr.startsWith('file://')) {{
    return `<a href="${{hrefStr}}" target="_blank" rel="noopener" title="${{title || ''}}">${{text}}</a>`;
  }}
  return origLink(href, title, text);
}};

const origBlockquote = renderer.blockquote.bind(renderer);
renderer.blockquote = function(quote) {{
  // 检测特殊引用块：核心要点 / 桥接 / 如何读
  let cls = '';
  if (quote.includes('核心要点')) cls = 'core-point';
  else if (quote.includes('如何读这张表') || quote.includes('如何读这张图')) cls = 'howto';
  else if (quote.includes('上一篇') || quote.includes('上一章') || quote.includes('本章')) cls = 'bridge';
  return `<blockquote class="${{cls}}">${{quote}}</blockquote>`;
}};

// ========== Mermaid 块处理（通过 renderer.code 拦截）==========
// 拦截 marked.js 的代码块渲染：当语言为 mermaid 时，直接返回
// <div class="mermaid">...</div>，绕过 marked 的代码高亮逻辑。
// 转义 < > & 防止浏览器把 mermaid 语法当 HTML 解析；浏览器读取
// textContent 时会自动反转义，mermaid 拿到的是原始文本。
// 注意：必须在 marked.use() 之前设置，否则 marked 内部
// 已拷贝了 renderer 快照，后续修改不会生效。
const origCode = renderer.code ? renderer.code.bind(renderer) : null;
renderer.code = function(code, infostring, escaped) {{
  const lang = (infostring || '').trim().split(/\\s+/)[0];
  if (lang === 'mermaid') {{
    const safe = String(code)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    return `<div class="mermaid">${{safe}}</div>`;
  }}
  if (origCode) return origCode(code, infostring, escaped);
  return false;
}};

marked.use({{ renderer }});

// ========== 后处理：代码高亮、代码折叠 ==========

function postProcess(content) {{
  // 代码高亮 + 折叠
  content.querySelectorAll('pre > code').forEach(codeEl => {{
    if (codeEl.classList.contains('hljs')) return;
    const lang = [...codeEl.classList]
      .find(c => c.startsWith('language-'))?.replace('language-', '');
    const raw = codeEl.textContent;

    // 仅对指定了已知语言的代码块做高亮；
    // 无语言标记的代码块（目录树、纯文本输出、ASCII 图等）
    // 跳过 hljs，避免 highlightAuto 误判导致渲染异常
    if (lang && hljs.getLanguage(lang)) {{
      try {{
        const highlighted = hljs.highlight(raw, {{ language: lang }}).value;
        codeEl.innerHTML = highlighted;
      }} catch (e) {{
        console.warn('highlight error:', e);
      }}
    }}
    codeEl.classList.add('hljs');
    codeEl.classList.add('language-' + (lang || 'plain'));

    // 长代码折叠（>25 行）
    const lineCount = raw.split('\\n').length;
    const pre = codeEl.parentElement;
    if (lineCount > 25) {{
      pre.classList.add('collapsible', 'collapsed');
      const toggle = document.createElement('div');
      toggle.className = 'code-toggle';
      toggle.textContent = `▼ 展开 (${{lineCount}} 行)`;
      toggle.onclick = function() {{ toggleCode(this); }};
      pre.appendChild(toggle);
    }}
  }});
}}

// ========== 文档加载与渲染 ==========

function loadDoc(name, anchor) {{
  if (!docs[name]) {{
    document.getElementById('content').innerHTML = `<h1>未找到文档</h1><p>文档 "${{name}}" 不存在。</p>`;
    return;
  }}

  // 更新侧边栏激活状态
  document.querySelectorAll('.doc-item').forEach(item => {{
    item.classList.toggle('active', item.dataset.doc === name);
  }});

  // 渲染 md（mermaid 块由 renderer.code 拦截，直接生成 <div class="mermaid">）
  const html = marked.parse(docs[name]);
  const content = document.getElementById('content');
  content.innerHTML = html;

  // 后处理：高亮 / 折叠
  postProcess(content);

  // 为标题添加 id（用于锚点跳转，需在滚动前完成）
  addHeadingIds(content);

  // 渲染数学公式
  renderMath(content);

  // 渲染 Mermaid
  renderMermaid(content);

  // 初始化调用关系图交互
  initCallGraph();

  // 更新 URL hash（设置标志避免 hashchange 触发重复渲染）
  suppressHashChange = true;
  if (anchor) {{
    window.location.hash = `/${{name}}#${{anchor}}`;
  }} else {{
    window.location.hash = `/${{name}}`;
  }}
  // 在下一个事件循环恢复标志，确保 hashchange 事件已被跳过
  setTimeout(() => {{ suppressHashChange = false; }}, 0);

  // 滚动到顶部或锚点
  if (anchor) {{
    setTimeout(() => {{
      const el = document.getElementById(anchor);
      if (el) el.scrollIntoView();
      else window.scrollTo(0, 0);
    }}, 150);
  }} else {{
    window.scrollTo(0, 0);
  }}

  // 关闭移动端侧边栏
  document.getElementById('sidebar').classList.remove('open');
}}

function addHeadingIds(content) {{
  // 与 Python slugify 保持一致：保留中文、字母、数字、空格、连字符
  content.querySelectorAll('h1, h2, h3, h4').forEach(h => {{
    const text = h.textContent.trim();
    let id = text.replace(/[^\\u4e00-\\u9fa5A-Za-z0-9\\s\\-]/g, '');
    id = id.replace(/\\s+/g, '-').toLowerCase();
    h.id = id;
  }});
}}

function renderMath(content) {{
  if (window.renderMathInElement) {{
    renderMathInElement(content, {{
      delimiters: [
        {{left: '$$', right: '$$', display: true}},
        {{left: '$', right: '$', display: false}},
        {{left: '\\\\(', right: '\\\\)', display: false}},
        {{left: '\\\\[', right: '\\\\]', display: true}},
      ],
      throwOnError: false,
    }});
  }}
}}

function renderMermaid(content) {{
  const mermaidDivs = content.querySelectorAll('.mermaid');
  if (mermaidDivs.length === 0) return;

  // 确保 mermaid 已初始化（主题随当前 body 主题）
  if (window.mermaid) {{
    const theme = document.body.dataset.theme === 'dark' ? 'dark' : 'default';
    window.mermaid.initialize({{
      startOnLoad: false,
      theme: theme,
      securityLevel: 'loose',
    }});
  }}

  const theme = document.body.dataset.theme === 'dark' ? 'dark' : 'default';
  // 暗黑模式下直接把整个 init 配置替换为 dark 专用版本，避免浅色 themeVariables 残留
  const darkInit = '%%{{init: {{"theme": "dark", "themeVariables": {{"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#64748b", "lineColor": "#94a3b8", "secondaryColor": "#334155", "secondaryBorderColor": "#64748b", "tertiaryColor": "#1e293b", "fontFamily": "\\"trebuchet ms\\", verdana, arial, sans-serif"}}}}}}%%';
  const preprocessMermaid = (code) => {{
    if (theme !== 'dark') return code;
    return code.replace(/%%\{{init:[^%]*\}}%%/, darkInit);
  }};

  const doRender = () => {{
    // 保存原始 mermaid 代码到 data 属性，便于主题切换时重新渲染
    mermaidDivs.forEach(d => {{
      if (!d.dataset.mermaidSrc) {{
        d.dataset.mermaidSrc = d.textContent;
      }}
    }});

    // 使用 mermaid.render() 逐个渲染，避免 mermaid.run() 的 getBoundingClientRect 问题
    // mermaid.render(id, text) 返回 Promise，resolve 时得到 {{svg, bindFunctions}}
    mermaidDivs.forEach((d, i) => {{
      // 跳过已渲染的（含 svg）
      if (d.querySelector('svg')) return;
      const code = preprocessMermaid(d.dataset.mermaidSrc || d.textContent);
      const renderId = `mermaid-svg-${{i}}-${{Date.now()}}`;

      window.mermaid.render(renderId, code).then(result => {{
        d.innerHTML = result.svg;
        if (result.bindFunctions) {{
          result.bindFunctions(d);
        }}
      }}).catch(err => {{
        console.error(`Mermaid render error (diagram ${{i}}):`, err);
        d.innerHTML = '<pre style="text-align:left;color:#dc2626;font-size:12px;white-space:pre-wrap;">' +
          'Mermaid 渲染失败:\\n' + code.replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</pre>';
      }});
    }});
  }};

  if (window.mermaidReady) {{
    doRender();
  }} else {{
    window.addEventListener('mermaid-loaded', doRender, {{ once: true }});
  }}
}}

// ========== 工具函数 ==========

function toggleTheme() {{
  const body = document.body;
  const current = body.dataset.theme;
  const newTheme = current === 'light' ? 'dark' : 'light';
  body.dataset.theme = newTheme;
  document.getElementById('theme-btn').textContent = newTheme === 'light' ? '🌙 暗黑' : '☀️ 明亮';
  // 切换 highlight.js 样式表
  document.getElementById('hljs-light').disabled = (newTheme === 'dark');
  document.getElementById('hljs-dark').disabled = (newTheme === 'light');
  // 重新渲染 Mermaid（暗黑模式下把源码中的 base 主题替换为 dark）
  if (window.mermaid) {{
    window.mermaid.initialize({{
      startOnLoad: false,
      theme: newTheme === 'dark' ? 'dark' : 'default',
      securityLevel: 'loose',
    }});
    const content = document.getElementById('content');
    const mermaidDivs = content.querySelectorAll('.mermaid');
    const darkInit = '%%{{init: {{"theme": "dark", "themeVariables": {{"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#64748b", "lineColor": "#94a3b8", "secondaryColor": "#334155", "secondaryBorderColor": "#64748b", "tertiaryColor": "#1e293b", "fontFamily": "\\"trebuchet ms\\", verdana, arial, sans-serif"}}}}}}%%';
    const preprocessMermaid = (code) => {{
      if (newTheme !== 'dark') return code;
      return code.replace(/%%\{{init:[^%]*\}}%%/, darkInit);
    }};
    mermaidDivs.forEach((d, i) => {{
      const src = d.dataset.mermaidSrc;
      if (!src) return;
      const code = preprocessMermaid(src);
      const renderId = `mermaid-svg-${{i}}-${{Date.now()}}`;
      window.mermaid.render(renderId, code).then(result => {{
        d.innerHTML = result.svg;
        if (result.bindFunctions) result.bindFunctions(d);
      }}).catch(err => {{
        console.error(`Mermaid re-render error (diagram ${{i}}):`, err);
      }});
    }});
  }}
  localStorage.setItem('zephyr-theme', newTheme);
}}

function toggleCode(el) {{
  const pre = el.parentElement;
  pre.classList.toggle('collapsed');
  el.textContent = pre.classList.contains('collapsed')
    ? '▼ 展开 (' + pre.querySelector('code').textContent.split('\\n').length + ' 行)'
    : '▲ 折叠';
}}

function expandAllCode() {{
  document.querySelectorAll('pre.collapsible').forEach(pre => {{
    pre.classList.remove('collapsed');
    const toggle = pre.querySelector('.code-toggle');
    if (toggle) toggle.textContent = '▲ 折叠';
  }});
}}

function collapseAllCode() {{
  document.querySelectorAll('pre.collapsible').forEach(pre => {{
    pre.classList.add('collapsed');
    const toggle = pre.querySelector('.code-toggle');
    if (toggle) toggle.textContent = '▼ 展开 (' + pre.querySelector('code').textContent.split('\\n').length + ' 行)';
  }});
}}

function toggleSidebar() {{
  document.getElementById('sidebar').classList.toggle('open');
}}

function filterSidebar() {{
  const q = document.getElementById('search-input').value.toLowerCase();
  document.querySelectorAll('.doc-item').forEach(item => {{
    const title = item.dataset.title.toLowerCase();
    const match = title.includes(q);
    item.style.display = match ? '' : 'none';
  }});
}}

function scrollTop() {{
  window.scrollTo({{ top: 0, behavior: 'smooth' }});
}}

// 滚动时显示回到顶部按钮
window.addEventListener('scroll', () => {{
  const btn = document.getElementById('back-to-top');
  btn.classList.toggle('visible', window.scrollY > 400);
}});

{callgraph_js}

// ========== 初始化 ==========

// 恢复主题
const savedTheme = localStorage.getItem('zephyr-theme');
if (savedTheme) {{
  document.body.dataset.theme = savedTheme;
  document.getElementById('theme-btn').textContent = savedTheme === 'light' ? '🌙 暗黑' : '☀️ 明亮';
  document.getElementById('hljs-light').disabled = (savedTheme === 'dark');
  document.getElementById('hljs-dark').disabled = (savedTheme === 'light');
}}

// 初始化 mermaid 主题
if (window.mermaid) {{
  const theme = document.body.dataset.theme;
  window.mermaid.initialize({{
    startOnLoad: false,
    theme: theme === 'dark' ? 'dark' : 'default',
    securityLevel: 'loose',
  }});
}}

// 初始化调用图浏览器（一次性：加载数据 + 构建抽屉 + 绑定快捷键）
// 注意：CG.refresh() 由 loadDoc() 内的 initCallGraph() 触发，
// 每次切换文档时重新挂载代码块按钮与行内引用链接
CG.init();

// 解析 URL hash 加载文档
// 通过 suppressHashChange 标志避免 loadDoc 设置 hash 时触发重复渲染
let suppressHashChange = false;

function loadFromHash() {{
  if (suppressHashChange) return;
  const hash = window.location.hash.slice(1);  // 去掉 #
  if (hash.startsWith('/')) {{
    const parts = hash.slice(1).split('#');
    // 浏览器会将 hash 中的中文进行 URL 编码（如 %E5%85%A5...），
    // 需 decodeURIComponent 还原为原始文件名才能匹配 docs 的 key
    const docName = decodeURIComponent(parts[0]);
    const anchor = parts[1] ? decodeURIComponent(parts[1]) : '';
    if (docs[docName]) {{
      loadDoc(docName, anchor);
      return;
    }}
  }}
  // 默认加载 README
  loadDoc('README');
}}

window.addEventListener('hashchange', loadFromHash);
window.addEventListener('load', loadFromHash);
</script>

</body>
</html>
"""


def main():
    print(f"加载文档 from {NOTES_DIR}...")
    docs = load_documents()
    print(f"  共 {len(docs)} 篇文档")
    for name, title, _, toc in docs:
        print(f"    - {name}: {title} ({len(toc)} H2)")

    print(f"\n构建 HTML...")
    html_content = build_html(docs)

    print(f"\n写入 {OUTPUT_FILE}...")
    OUTPUT_FILE.write_text(html_content, encoding="utf-8")

    size_kb = OUTPUT_FILE.stat().st_size / 1024
    print(f"\n✅ 完成！文件大小：{size_kb:.1f} KB")
    print(f"   打开方式：file://{OUTPUT_FILE}")
    print(f"   或启动本地服务器：cd {OUTPUT_FILE.parent} && python3 -m http.server 8000")


if __name__ == "__main__":
    main()
