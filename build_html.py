#!/usr/bin/env python3
"""
通用 Markdown 笔记 → 单文件 HTML 构建器

将一个目录下的 Markdown 文档(README.md + 编号章节)转换为单个独立的 HTML 文件。
内嵌 marked.js / mermaid.js / KaTeX / highlight.js,图片以 base64 内嵌,
可在无网络环境下使用,方便在其他平台分发。

特性:
  - 前端渲染:marked.js(GFM) + mermaid.js + KaTeX 数学公式 + highlight.js 代码高亮
  - 侧边栏:文档列表 + 每篇 H2 目录 + 搜索过滤
  - 路由:hash 路由(#/doc-id, #/doc-id#anchor)+ localStorage 记忆上次阅读
  - 图片:本地图片内嵌为 data URI,真正单文件可移植
  - 跨文档链接:[text](./01-foo.md) → #/01-foo;[text](./src/...) → file:/// 绝对路径

用法:
    python3 build_html.py <src-dir> [--output <file>] [--title <title>]
                           [--vendor-dir <dir>] [--cdn]

示例:
    python3 build_html.py trusted-firmware
    python3 build_html.py nccl --output nccl/index.html --title "NCCL 学习笔记"
    python3 build_html.py zephyr-rtos --output zephyr-rtos-html/index.html

依赖:仅 Python 标准库。首次运行需网络下载前端库(缓存到 .html-build/vendor/)。
"""

import argparse
import base64
import html
import json
import mimetypes
import re
import sys
import urllib.request
from pathlib import Path

# ==================== 配置 ====================

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_VENDOR_DIR = REPO_ROOT / ".html-build" / "vendor"
DEFAULT_OUTPUT_NAME = "standalone.html"

# 前端库:文件名 → CDN URL。marked 版本与现有 tenstorrent HTML 保持一致。
VENDOR_LIBS = {
    "marked.min.js":      "https://cdnjs.cloudflare.com/ajax/libs/marked/9.1.6/marked.min.js",
    "mermaid.min.js":     "https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.6.1/mermaid.min.js",
    "katex.min.css":      "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css",
    "katex.min.js":       "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.js",
    "auto-render.min.js": "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/contrib/auto-render.min.js",
    "highlight.min.js":   "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js",
    "github.min.css":     "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css",
    # 额外语言(highlight.js common bundle 不含 armasm/x86asm)
    "armasm.min.js":      "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/armasm.min.js",
    "x86asm.min.js":      "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/x86asm.min.js",
}

# CDN 兜底(当 --cdn 或下载失败时使用)
CDN_PREFIX = {
    "marked.min.js":      "https://cdnjs.cloudflare.com/ajax/libs/marked/9.1.6/marked.min.js",
    "mermaid.min.js":     "https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.6.1/mermaid.min.js",
    "katex.min.css":      "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css",
    "katex.min.js":       "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.js",
    "auto-render.min.js": "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/contrib/auto-render.min.js",
    "highlight.min.js":   "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js",
    "github.min.css":     "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css",
    "armasm.min.js":      "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/armasm.min.js",
    "x86asm.min.js":      "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/x86asm.min.js",
}

DOC_GLOB = "[0-9][0-9]-*.md"

# ==================== 前端库下载/缓存 ====================


def download_vendor(vendor_dir: Path, use_cdn: bool) -> dict:
    """下载(并缓存)前端库。返回 {filename: ("inline", text) | ("cdn", url)}。

    use_cdn=True 时直接返回 CDN 链接,不下载。
    否则尝试下载并内嵌;下载失败则回退到 CDN 链接并告警。
    """
    vendor_dir.mkdir(parents=True, exist_ok=True)
    result = {}
    for fname, url in VENDOR_LIBS.items():
        if use_cdn:
            result[fname] = ("cdn", url)
            continue
        cache = vendor_dir / fname
        if cache.exists():
            try:
                result[fname] = ("inline", cache.read_text(encoding="utf-8"))
                continue
            except UnicodeDecodeError:
                cache.unlink(missing_ok=True)
        # 下载
        try:
            print(f"  下载 {fname} ...", file=sys.stderr)
            req = urllib.request.Request(url, headers={"User-Agent": "build_html.py/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            cache.write_bytes(data)
            result[fname] = ("inline", data.decode("utf-8", errors="replace"))
        except Exception as e:
            print(f"  ⚠️  下载失败 {fname}: {e};回退到 CDN 链接", file=sys.stderr)
            result[fname] = ("cdn", url)
    return result


# ==================== 文本处理 ====================


def slugify(text: str) -> str:
    """生成锚点 id:保留中文/字母/数字/空格/连字符,空格转连字符,小写。

    与前端 JS slugify() 保持一致,确保 Python 生成的 TOC 锚点与 JS 渲染的 H2 id 对得上。
    """
    s = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9\s\-]", "", text)
    s = re.sub(r"\s+", "-", s.strip())
    return s.lower()


def heading_to_slug(text: str) -> str:
    """从 markdown 原始标题文本(含行内格式)生成 slug。

    去除行内代码/加粗/斜体/链接等标记,与前端 textContent 处理后一致。
    """
    clean = text
    clean = re.sub(r"`([^`]*)`", r"\1", clean)          # `code` → code
    clean = re.sub(r"\*\*([^*]*)\*\*", r"\1", clean)    # **bold** → bold
    clean = re.sub(r"\*([^*]*)\*", r"\1", clean)        # *italic* → italic
    clean = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", clean)  # [text](url) → text
    clean = re.sub(r"[~_]", "", clean)
    return slugify(clean)


def extract_title(content: str, fallback: str) -> str:
    """从 md 提取 H1 标题文本(去除行内标记)。"""
    for line in content.splitlines():
        if line.startswith("# "):
            raw = line[2:].strip()
            return re.sub(r"[`*_~]", "", raw)
    return fallback


def extract_toc(content: str):
    """提取 H2 标题作为目录。返回 [(slug, text), ...]。slug 已去重。"""
    toc = []
    seen = {}
    for line in content.splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            text = m.group(1).strip()
            slug = heading_to_slug(text)
            if slug in seen:
                seen[slug] += 1
                slug = f"{slug}-{seen[slug]}"
            else:
                seen[slug] = 0
            toc.append((slug, text))
    return toc


def escape_for_script(content: str) -> str:
    """转义 md 内容中可能破坏 <script> 标签的字符。"""
    content = content.replace("</script>", "<\\/script>")
    content = content.replace("<!--", "<\\!--")
    return content


# ==================== 图片内嵌与链接重写 ====================

IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# 非图片链接(负向断言排除 ! 前缀的图片)
LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def embed_images(content: str, src_dir: Path) -> str:
    """将本地图片引用替换为 base64 data URI 的 <img> 标签。

    外部 http(s)/data: URL 保持不变。缺失文件保留原 markdown。
    转成 <img> HTML 后,后续 LINK_RE 不会再误伤图片(无 [text](url) 形态)。
    """
    def repl(m):
        alt = m.group(1)
        url = m.group(2).strip()
        if url.startswith(("http://", "https://", "data:", "mailto:")):
            return m.group(0)
        # 解析路径(相对 src_dir),去掉可能的 URL fragment
        path_part = url.split("#", 1)[0].split("?", 1)[0]
        if path_part.startswith("/"):
            img_path = Path(path_part)
        else:
            img_path = (src_dir / path_part).resolve()
        if not img_path.is_file():
            # 文件不存在,保留原 markdown(浏览器显示 broken image)
            return m.group(0)
        mime, _ = mimetypes.guess_type(str(img_path))
        if mime is None or not mime.startswith("image/"):
            mime = "image/png"
        b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
        alt_esc = html.escape(alt, quote=True)
        return f'<img class="md-img" src="data:{mime};base64,{b64}" alt="{alt_esc}" loading="lazy" />'

    return IMAGE_RE.sub(repl, content)


def rewrite_links(content: str, src_dir: Path, doc_stems: set) -> str:
    """重写 markdown 链接:

    - 指向本文档集内其他 md(如 ./01-foo.md、README.md#sec)→ hash 路由 #/stem[#anchor]
    - 指向 src/ 源码或 ../ 其他目录 → file:/// 绝对路径(本机可点击,他机仅显示路径)
    - http/https/mailto/# → 原样保留
    """
    def repl(m):
        text = m.group(1)
        url = m.group(2).strip()
        if url.startswith(("http://", "https://", "mailto:", "#", "data:")):
            return m.group(0)
        path_part, _, anchor = url.partition("#")
        pp = path_part.strip()
        if pp.startswith("./"):
            pp = pp[2:]
        if not pp:
            return m.group(0)
        # 是否为本文档集内的 md(带或不带 .md 后缀)
        stem = pp[:-3] if pp.endswith(".md") else pp
        if stem in doc_stems:
            route = f"#/{stem}"
            if anchor:
                route += f"#{anchor}"
            return f"[{text}]({route})"
        # 其他本地路径(src/ 源码、../ 跨目录)→ file:/// 绝对路径
        try:
            target = (src_dir / path_part).resolve()
            file_url = f"file://{target}"
        except Exception:
            file_url = path_part
        if anchor:
            file_url += f"#{anchor}"
        return f"[{text}]({file_url})"

    return LINK_RE.sub(repl, content)


# ==================== 源码引用:内联 + 出处徽章 + 锚点 ====================
#
# 两种源码引用写法,build_html 都识别:
#
#   新语法(构建时从源码拉取,防漂移;agent 友好):
#     ```c src="./src/tf-a-src/bl1/bl1_main.c" lines="51-175" anchor="bl1_main"
#     ```
#     (fence 体为空,构建时填入源码对应行)
#
#   旧写法(代码已手贴,块首注释标出处):
#     ```c
#     /* 摘自 [tf-a-src/bl1/bl1_main.c](./src/tf-a-src/bl1/bl1_main.c) 第 51-175 行 */
#     void bl1_main(void) { ... }
#     ```
#
# 两种都渲染成:出处徽章(可点击 file:// 跳源码) + 带行号的代码块 + 锚点 id
# (供文中 `[fn()](#anchor)` 跳转,call graph 雏形)。

FENCE_OPEN_RE = re.compile(r"^```(\w+)(.*)$")
# 摘自注释:`摘自 [name](path) 第 X-Y 行` / `摘自 name L X-Y` / `(节选)` `(简化)` 等尾巴
CITATION_RE = re.compile(
    r"摘自\s+"
    r"(?:\[([^\]]*)\]\(([^)]+)\)|(\S+))"   # [name](path) 或 bare path
    r"(?:[^\d]*?)"                          # "第" / "L" / 空格
    r"(\d+)\s*[-–]\s*(\d+)"                 # 51-175
)


def _parse_fence_attrs(attr_str: str) -> dict:
    """从 fence info string 的尾巴解析 key=\"value\" 属性。"""
    attrs = {}
    for m in re.finditer(r'(\w+)\s*=\s*"([^"]*)"', attr_str):
        attrs[m.group(1)] = m.group(2)
    return attrs


def _resolve_src_path(raw_path: str, src_dir: Path) -> Path | None:
    """把引用里的路径解析到真实文件。尝试 src_dir 为基,再试 src_dir/src,再试仓库根。"""
    raw = raw_path.strip()
    if raw.startswith("./"):
        raw = raw[2:]
    cands = []
    if raw.startswith("/"):
        cands.append(Path(raw))
    else:
        cands.append((src_dir / raw).resolve())
        cands.append((src_dir / "src" / raw).resolve())
        cands.append((REPO_ROOT / raw).resolve())
    for c in cands:
        try:
            if c.is_file():
                return c
        except OSError:
            pass
    return None


def _src_anchor_id(raw_path: str, start: int) -> str:
    """为源码引用块生成锚点 id(供文中链接跳转)。"""
    raw = raw_path.strip()
    if raw.startswith("./"):
        raw = raw[2:]
    stem = re.sub(r"[^一-龥A-Za-z0-9]+", "-", raw).strip("-")
    return f"src-{stem}-{start}"


def _tidy_code_line(line: str) -> str:
    """清理一行代码的尾随空白。注意续行符场景:源文件常把行尾 '\\'
    用空格垫到固定列做对齐,空格在 '\\' 之前,rstrip 碰到 '\\' 会停手——
    需要把 '\\\\' 前的空格串收成一个,否则 <pre> 被撑到最宽。"""
    line = line.rstrip()
    if line.endswith("\\"):
        line = re.sub(r"[ \t]+\\$", " \\\\", line)
    return line


def _rstrip_code_fences(md: str) -> str:
    """去掉围栏代码块内各行的尾随空白与续行符前的对齐空格。
    只动 fence 内的行,散文不动。"""
    out, in_fence = [], False
    for line in md.split("\n"):
        if not in_fence and re.match(r"^\s{0,3}(`{3,}|~{3,})", line):
            in_fence = True
        elif in_fence and re.match(r"^\s{0,3}(`{3,}|~{3,})\s*$", line):
            in_fence = False
        out.append(_tidy_code_line(line) if in_fence else line)
    return "\n".join(out)


def _render_src_block(display: str, fpath: Path, start: int, end: int,
                      body: str, lang: str, anchor_id: str) -> str:
    """把一段源码引用渲染成带出处徽章 + 行号 + 锚点的 HTML 块。

    代码体内的换行写成 &#10; 实体,让整块保持单物理行:CommonMark type-6
    HTML block 在第一个空行处截断,若源码体里本身有空行(python docstring
    很常见),后半段会被 marked 按 markdown 重新解析并嵌进未闭合的 <code>。
    浏览器解析时实体解码回真实换行,显示与复制均不受影响。
    """
    href = f"file://{fpath}"
    badge = (f'<div class="src-block" id="{html.escape(anchor_id)}">'
             f'<div class="src-badge">'
             f'<a href="{html.escape(href)}">{html.escape(display)} '
             f'第 {start}-{end} 行</a>'
             f'</div>')
    code_text = "\n".join(_tidy_code_line(line) for line in body.split("\n"))
    code = html.escape(code_text).replace("\n", "&#10;")
    pre = (f'<pre class="src-pre" data-start="{start}">'
           f'<code class="language-{html.escape(lang)}">{code}</code></pre></div>')
    return badge + "\n" + pre


def process_source_blocks(content: str, src_dir: Path, problems: list, doc_name: str,
                          collected: list | None = None) -> str:
    """识别并转换源码引用代码块(新语法 + 旧注释)。

    新语法空体 fence 从源码拉取填入;两种写法都渲染成带徽章/行号/锚点的 HTML。
    问题(文件缺失、行区间越界)记入 problems。

    collected 非 None 时,同时收集每个代码块的
    {kind: src|cite|plain, doc, lang, fpath, start, end, anchor_id, body},
    供调用图构建复用(src= 围栏的 body 已从源码填好,行号精确)。
    """
    lines = content.split("\n")
    out: list[str] = []
    anchor_seen: dict = {}
    i = 0
    n = len(lines)
    while i < n:
        m = FENCE_OPEN_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        lang = m.group(1)
        attrs_str = m.group(2)
        attrs = _parse_fence_attrs(attrs_str)
        # 收集 fence 体直到闭合 ```
        body_start = i + 1
        j = body_start
        while j < n and not lines[j].startswith("```"):
            j += 1
        body = "\n".join(lines[body_start:j])
        close = j  # 闭合 ``` 行号(或 n)

        src_attr = attrs.get("src")
        citation = None  # (display, fpath, start, end, anchor_id, body)
        if src_attr:
            lines_attr = attrs.get("lines", "")
            rng = re.match(r"\s*(\d+)\s*[-–]\s*(\d+)\s*", lines_attr)
            fpath = _resolve_src_path(src_attr, src_dir)
            if fpath is None:
                problems.append({
                    "severity": "error", "doc": doc_name, "line": i + 1,
                    "kind": "src_ref_missing",
                    "msg": f"源码引用文件不存在:{src_attr}",
                })
                # 保留原 fence,体保持空
                out.append(f"```{lang}")
                if close < n:
                    out.append("```")
                i = close + 1
                continue
            if not rng:
                problems.append({
                    "severity": "error", "doc": doc_name, "line": i + 1,
                    "kind": "src_ref_lines",
                    "msg": f"源码引用缺 lines=\"X-Y\":{src_attr}",
                })
                start = end = 1
            else:
                start, end = int(rng.group(1)), int(rng.group(2))
            src_lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
            if end > len(src_lines) or start < 1 or start > end:
                problems.append({
                    "severity": "error", "doc": doc_name, "line": i + 1,
                    "kind": "src_ref_range",
                    "msg": f"行区间 {start}-{end} 越界(文件 {src_attr} 共 {len(src_lines)} 行)",
                })
                body = "\n".join(src_lines[max(0, start - 1):end])
            else:
                body = "\n".join(src_lines[start - 1:end])
            anchor_id = attrs.get("anchor") or _src_anchor_id(src_attr, start)
            citation = (src_attr, fpath, start, end, anchor_id, body)
        else:
            # 旧写法:检查体首行是否 摘自 注释
            cit_m = CITATION_RE.search(body.split("\n", 1)[0] if body else "")
            if cit_m:
                display = cit_m.group(1) or cit_m.group(3) or ""
                raw_path = cit_m.group(2) or cit_m.group(3) or ""
                start, end = int(cit_m.group(4)), int(cit_m.group(5))
                # 体里去掉那行注释(徽章已替代它)
                body_lines = body.split("\n")
                if body_lines and "摘自" in body_lines[0]:
                    body = "\n".join(body_lines[1:]).lstrip("\n")
                fpath = _resolve_src_path(raw_path, src_dir)
                if fpath is None:
                    problems.append({
                        "severity": "warn", "doc": doc_name, "line": body_start,
                        "kind": "src_ref_missing",
                        "msg": f"出处文件不存在:{raw_path}(徽章仍渲染但链接无效)",
                    })
                    fpath = src_dir / raw_path  # 占位
                anchor_id = _src_anchor_id(raw_path, start)
                citation = (display or raw_path, fpath, start, end, anchor_id, body)

        if citation:
            display, fpath, start, end, anchor_id, body = citation
            # 同文档内 id 去重(首个保留,后续 -2/-3;与标题 slug 的客户端去重同理)
            if anchor_id in anchor_seen:
                anchor_seen[anchor_id] += 1
                anchor_id = f"{anchor_id}-{anchor_seen[anchor_id]}"
            else:
                anchor_seen[anchor_id] = 0
            # 确保 HTML 块前有空行,marked 才能识别为 HTML block 原样透传
            if out and out[-1].strip() != "":
                out.append("")
            out.append(_render_src_block(display, fpath, start, end, body, lang, anchor_id))
            # HTML 块后必须补空行:CommonMark type-6 HTML block 以空行结束,
            # 否则 marked 把后续 markdown 原样吞进 <div>(正文"进入代码块")
            out.append("")
            if collected is not None:
                collected.append({
                    "kind": "src" if src_attr else "cite", "doc": doc_name,
                    "lang": lang, "fpath": str(fpath),
                    "start": start, "end": end, "anchor_id": anchor_id,
                    "body": body,
                })
            i = close + 1
            continue

        # 普通代码块:原样保留(fence 尾巴若有非 src 属性,剥到只剩 lang)
        if collected is not None and lang == "c":
            collected.append({
                "kind": "plain", "doc": doc_name, "lang": lang,
                "fpath": None, "start": None, "end": None,
                "anchor_id": None, "body": body,
            })
        out.append(f"```{lang}" if attrs_str.strip() else lines[i])
        out.extend(lines[body_start:close])
        if close < n:
            out.append("```")
        i = close + 1
    return "\n".join(out)


# ==================== 调用关系图(可选,专题 opt-in) ====================
#
# 嫁接自 zephyr-rtos-html/build_html.py 的调用图功能,三点适配:
#   - 输入不再是重解析 markdown,而是 process_source_blocks 收集的已填代码片段
#     (src= 围栏构建期已从源码拉取,函数定义的行号是精确的);
#   - 源码回填按"笔记引用过的文件优先 + 前缀优先级",替代 zephyr 的 ARM 路径偏好;
#   - 宏排除表换成 SCP-firmware 词频实测版(FWK_* 构造/日志宏排除,
#     小写 fwk_id_*/fwk_list_* 是真实 API,保留)。
#
# 启用条件:专题目录存在 callgraph_overrides.json(opt-in),或 --callgraph 强制。
# 未启用专题零影响:cg-data 不生成,模板占位符全部替换为空串。

# C 控制流关键字(出现在 call 位置时一定是控制结构而非函数调用)
C_CONTROL_KEYWORDS = {
    "if", "while", "for", "switch", "return", "sizeof", "else", "do",
    "case", "break", "continue", "goto", "default", "defined",
}

# C 类型/存储类/限定符关键字(不可能作为函数名)
C_TYPE_KEYWORDS = {
    "void", "int", "char", "float", "double", "long", "short", "unsigned",
    "signed", "bool", "size_t", "ssize_t", "off_t",
    "int8_t", "int16_t", "int32_t", "int64_t",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "uintptr_t", "intptr_t",
    "struct", "enum", "union", "const", "volatile", "static", "inline",
    "extern", "register", "auto", "typedef",
}

# SCP-firmware 伪函数/宏(不应作为调用图节点)。
# 词频实测:FWK_ARRAY_SIZE×180、fwk_assert×155、FWK_LOG_*×246、FWK_ID_* 构造宏×168;
# 小写 fwk_id_*/fwk_list_*(fwk_id.c/fwk_list.c 的真实 API)刻意不在表内。
SCP_EXCLUDE_EXACT = {
    "NULL", "true", "false", "container_of",
    "fwk_assert", "fwk_expect", "fwk_check", "fwk_trap", "fwk_unexpected",
    "FWK_ASSERT", "FWK_WEAK",
    "FWK_ARRAY_SIZE", "FWK_MIN", "FWK_MAX", "FWK_BIT",
    "FWK_ALIGN", "FWK_ALIGN_NEXT", "FWK_S", "FWK_NS", "FWK_M", "FWK_MS", "FWK_US",
    "FWK_LIST_GET", "FWK_PRINTF", "FWK_NONNULL",
    "FWK_READ_ONLY1", "FWK_READ_WRITE1", "FWK_WRITE_ONLY1",
    "FWK_ALLOC_SIZE2", "FWK_ALLOC_ALIGN",
    "FWK_MODULE_STATIC_ELEMENTS", "FWK_MODULE_DYNAMIC_ELEMENTS",
    "FWK_MODULE_STATIC_ELEMENTS_PTR",
    "UINT8_C", "UINT16_C", "UINT32_C", "UINT64_C",
}
SCP_EXCLUDE_PREFIXES = (
    "FWK_LOG_", "FWK_TRACE_", "FWK_ID_", "FWK_HAS_",
    "FWK_ALLOC_", "FWK_READ_", "FWK_WRITE_",
)

C_ALL_KEYWORDS = C_CONTROL_KEYWORDS | C_TYPE_KEYWORDS | SCP_EXCLUDE_EXACT

# callgraph_overrides.json 中作为元数据、不应当作函数的键
OVERRIDE_META_KEYS = {"说明", "格式", "示例", "使用方式", "_meta", "_comment"}


def strip_c_comments_and_strings(code):
    """剥离 C 代码中的注释与字符串字面量,保留行结构(换行符不变、总长不变)。

    用于调用关系分析:避免把注释/字符串中的 identifier( 误识别为函数调用。
    """
    out = []
    i = 0
    n = len(code)
    in_line_cmt = in_block_cmt = in_str = in_char = False
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
    """在 s 中从 open_pos(指向 open_ch)开始,找到配对的 close_ch 位置。处理嵌套。"""
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


# 函数定义候选正则:行首 + 至少一个"类型词 + 空白" + 可选 * + 函数名 + (
# 要求函数名前必须有"类型词+空白",天然排除 macro() 调用、if/while 等控制语句、
# 以及缩进的函数调用(^ 锚定行首)。
_FUNC_DEF_CANDIDATE = re.compile(
    r"^(?P<prefix>(?:[a-zA-Z_]\w*\s+)+\**\s*)"
    r"(?P<name>[a-zA-Z_]\w*)\s*\(",
    re.MULTILINE,
)


def find_function_definitions(code):
    """在 C 代码块中找出所有函数定义。

    判定:参数列表后必须紧跟 {(允许中间有 __attribute__ 等),不能是 ; 或 =。
    返回 [{"name", "signature", "body", "full_text", "start", "end"}],
    start/end 是在原始 code 中的字符偏移。
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
        if name in C_ALL_KEYWORDS:
            pos = m.end()
            continue

        open_paren = m.end() - 1
        close_paren = find_matching(cleaned, open_paren, "(", ")")
        if close_paren is None:
            pos = m.end()
            continue

        # ) 之后:函数定义要求 { 先于 ; 与 = 出现
        after = cleaned[close_paren + 1:]
        next_brace = after.find("{")
        next_semi = after.find(";")
        next_eq = after.find("=")
        if next_brace == -1:
            pos = m.end()
            continue
        if next_semi != -1 and next_semi < next_brace:
            pos = m.end()
            continue
        if next_eq != -1 and next_eq < next_brace:
            pos = m.end()
            continue

        body_open = close_paren + 1 + next_brace
        body_close = find_matching(cleaned, body_open, "{", "}")
        if body_close is None:
            pos = m.end()
            continue

        funcs.append({
            "name": name,
            "signature": cleaned[m.start():close_paren + 1].strip(),
            "body": cleaned[body_open + 1:body_close],
            "full_text": code[m.start():body_close + 1].strip(),
            "start": m.start(),
            "end": body_close + 1,
        })
        pos = body_close + 1  # 跳过函数体,避免体内嵌套"定义"误报

    return funcs


def extract_calls_from_body(body):
    """从函数体(已剥离注释/字符串)提取被调用函数名,按出现顺序去重。"""
    calls = []
    seen = set()
    for m in re.finditer(r"\b([a-zA-Z_]\w*)\s*\(", body):
        name = m.group(1)
        if name in C_ALL_KEYWORDS or name.startswith(SCP_EXCLUDE_PREFIXES):
            continue
        if name not in seen:
            seen.add(name)
            calls.append(name)
    return calls


def default_callgraph_config(src_dir: Path) -> dict:
    """无 callgraph_overrides.json 但 --callgraph 强制启用时的默认配置。"""
    cfg = {
        "source_root": None, "source_mark": "",
        "prefix_priority": [], "exclude_dirs": ["unit_test", "test"],
        "exclude_prefixes": [], "exclude_exact": [],
        "overrides": {},
    }
    src_root = src_dir / "src"
    if src_root.is_dir():
        children = sorted(p for p in src_root.iterdir() if p.is_dir())
        if len(children) == 1:
            cfg["source_root"] = f"src/{children[0].name}"
            cfg["source_mark"] = f"/{children[0].name}/"
    return cfg


def load_callgraph_config(src_dir: Path):
    """读取专题的 callgraph_overrides.json。返回 None 表示未启用(opt-in 门)。

    文件内 _config 键提供专题配置(source_root/source_mark/prefix_priority 等),
    其余键为函数补丁(calls/called_by/...),构建期合并进自动提取结果。
    """
    path = src_dir / "callgraph_overrides.json"
    if not path.is_file():
        return None
    cfg = default_callgraph_config(src_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  警告: callgraph_overrides.json 解析失败: {e}", file=sys.stderr)
        return cfg
    user_cfg = data.pop("_config", None)
    if isinstance(user_cfg, dict):
        for k, v in user_cfg.items():
            if k in cfg and isinstance(v, type(cfg[k])):
                cfg[k] = v
    cfg["overrides"] = {
        k: v for k, v in data.items()
        if not k.startswith("_") and k not in OVERRIDE_META_KEYS and isinstance(v, dict)
    }
    return cfg


def build_call_graph(src_dir: Path, collected: list, cfg: dict):
    """从已收集的代码片段构建函数调用图。

    返回 (graph, defined_in_notes, filled)——graph 为
    {函数名: {calls, called_by, defined_in, referenced_in, signature, code, source}}。
    """
    graph = {}

    def ensure_node(name):
        if name not in graph:
            graph[name] = {
                "calls": [], "called_by": [],
                "defined_in": [], "referenced_in": [],
                "signature": None, "code": None, "source": None,
            }
        return graph[name]

    for entry in collected:
        doc_name, lang = entry["doc"], entry["lang"]
        if doc_name == "README" or lang != "c":
            continue
        body = entry["body"]
        if not body or not body.strip():
            continue
        is_src = entry["kind"] in ("src", "cite") and entry["fpath"]
        defs = find_function_definitions(body)
        for d in defs:
            fn = d["name"]
            node = ensure_node(fn)
            # 首个定义为准(collected 顺序 = 文档阅读顺序)
            if node["signature"] is None:
                node["signature"] = d["signature"]
            if node["code"] is None:
                node["code"] = d["full_text"]
            if node["source"] is None and is_src:
                line_start = entry["start"] + body[:d["start"]].count("\n")
                node["source"] = {
                    "path": f"file://{entry['fpath']}",
                    "line_start": line_start,
                    "line_end": line_start + d["full_text"].count("\n"),
                }
            if doc_name not in node["defined_in"]:
                node["defined_in"].append(doc_name)
            for c in extract_calls_from_body(d["body"]):
                if c == fn:
                    continue
                if c not in node["calls"]:
                    node["calls"].append(c)
                callee = ensure_node(c)
                if doc_name not in callee["referenced_in"]:
                    callee["referenced_in"].append(doc_name)

    defined_in_notes = sum(1 for n in graph.values() if n["code"] is not None)

    # 合并手工覆盖数据(追加去重;signature/code/source 仅在为空时填充)。
    # 补丁的 calls/called_by 也确保节点存在,供反向边与源码回填使用。
    overrides = cfg.get("overrides") or {}
    if overrides:
        for func_name, patch in overrides.items():
            node = ensure_node(func_name)
            for key in ("calls", "called_by", "defined_in", "referenced_in"):
                if key in patch and isinstance(patch[key], list):
                    for v in patch[key]:
                        if v not in node[key]:
                            node[key].append(v)
                        if key in ("calls", "called_by"):
                            ensure_node(v)
            for key in ("signature", "code", "source"):
                if key in patch and node[key] is None:
                    node[key] = patch[key]
        print(f"  合并覆盖数据: {len(overrides)} 个函数", file=sys.stderr)

    # 反向边:谁调了我(仅图内节点;含覆盖数据补出的边)
    for fname, node in graph.items():
        for callee in node["calls"]:
            if callee in graph and fname not in graph[callee]["called_by"]:
                graph[callee]["called_by"].append(fname)

    # 从专题源码树回填缺失定义(引用了但笔记/覆盖数据都没有 code 的函数)
    filled = 0
    source_root = cfg.get("source_root")
    if source_root:
        filled = _fill_from_source(graph, src_dir / source_root, collected, cfg)
        if filled:
            print(f"  从 {source_root} 补全: {filled} 个函数定义", file=sys.stderr)

    return graph, defined_in_notes, filled


def _fill_from_source(graph: dict, source_root: Path, collected: list, cfg: dict) -> int:
    """从专题源码树补全 graph 中缺失的函数定义。

    文件顺序:笔记引用过的 .c/.h(引用出现顺序)→ prefix_priority 各桶 .c→.h
    → 其余 .c→.h。源码树缺失(gitignored 克隆不存在)时静默跳过。
    """
    missing = [name for name, node in graph.items()
               if node["code"] is None and node["referenced_in"]]
    if not missing or not source_root.is_dir():
        return 0

    exclude_dirs = set(cfg.get("exclude_dirs") or [])
    c_files, h_files = [], []
    for p in sorted(source_root.rglob("*")):
        if p.suffix not in (".c", ".h"):
            continue
        if exclude_dirs and exclude_dirs.intersection(p.parts):
            continue
        rel = p.relative_to(source_root).as_posix()
        (c_files if p.suffix == ".c" else h_files).append(rel)

    # 引用过的文件相对路径(按出现顺序,去重)
    cited = []
    for e in collected:
        if e["kind"] not in ("src", "cite") or not e["fpath"]:
            continue
        try:
            rel = Path(e["fpath"]).resolve().relative_to(source_root).as_posix()
        except (ValueError, OSError):
            continue
        if rel not in cited:
            cited.append(rel)

    ordered, seen = [], set()

    def push(rel):
        if rel in seen:
            return
        seen.add(rel)
        p = source_root / rel
        if p.is_file():
            ordered.append(p)

    for rel in cited:
        push(rel)
    prefixes = cfg.get("prefix_priority") or []
    for pref in prefixes:
        for rel in c_files:
            if rel.startswith(pref):
                push(rel)
        for rel in h_files:
            if rel.startswith(pref):
                push(rel)
    for rel in c_files:
        push(rel)
    for rel in h_files:
        push(rel)

    func_patterns = {
        name: re.compile(
            r"^(?:[a-zA-Z_]\w*\s+)+\**\s*" + re.escape(name) + r"\s*\(",
            re.MULTILINE,
        )
        for name in missing
    }

    filled_count = 0
    remaining = set(missing)
    for src_file in ordered:
        if not remaining:
            break
        try:
            code = src_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name in list(remaining):
            if not func_patterns[name].search(code):
                continue
            matched_def = next(
                (d for d in find_function_definitions(code) if d["name"] == name),
                None,
            )
            if matched_def is None:
                continue
            node = graph[name]
            line_start = code[:matched_def["start"]].count("\n") + 1
            node["signature"] = matched_def["signature"]
            node["code"] = matched_def["full_text"]
            node["source"] = {
                "path": f"file://{src_file.resolve()}",
                "line_start": line_start,
                "line_end": line_start + matched_def["full_text"].count("\n"),
            }
            for c in extract_calls_from_body(matched_def["body"]):
                if c == name:
                    continue
                if c not in node["calls"]:
                    node["calls"].append(c)
                callee = ensure_graph_node(graph, c)
                if name not in callee["called_by"]:
                    callee["called_by"].append(name)
            remaining.discard(name)
            filled_count += 1
    return filled_count


def ensure_graph_node(graph: dict, name: str) -> dict:
    if name not in graph:
        graph[name] = {
            "calls": [], "called_by": [],
            "defined_in": [], "referenced_in": [],
            "signature": None, "code": None, "source": None,
        }
    return graph[name]


# ==================== 校验与统计 ====================


def count_stats(content: str) -> dict:
    """统计一篇文档的渲染元素数量(供 --json 可解析输出)。"""
    return {
        "figures": len(IMAGE_RE.findall(content)),
        "mermaid": len(re.findall(r"^```mermaid\b", content, re.MULTILINE)),
        "codeblocks": len(re.findall(r"^```\w+", content, re.MULTILINE)),
        "formulas": len(re.findall(r"\$\$[^\n]+?\$\$", content)) + content.count("\\("),
    }


def check_links_images(content: str, src_dir: Path, doc_stems: set,
                       problems: list, doc_name: str, frag_links: list | None = None):
    """校验文档内链接与图片:死链、缺图。

    frag_links 非 None 时收集 #锚点 链接 (doc, line, frag, target_stem|None),
    供 check_anchor_links 校验(None = 同文档内跳转)。
    """
    # 图片
    for m in IMAGE_RE.finditer(content):
        url = m.group(2).strip().split("#", 1)[0].split("?", 1)[0]
        if url.startswith(("http://", "https://", "data:", "mailto:")):
            continue
        img_path = Path(url) if url.startswith("/") else (src_dir / url).resolve()
        if not img_path.is_file():
            problems.append({
                "severity": "error", "doc": doc_name, "line": content[:m.start()].count("\n") + 1,
                "kind": "image_missing", "msg": f"图片不存在:{url}",
            })
    # 链接
    for m in LINK_RE.finditer(content):
        url = m.group(2).strip()
        if url.startswith("#"):
            # 同文档锚点跳转
            if frag_links is not None and url[1:]:
                frag_links.append((doc_name, content[:m.start()].count("\n") + 1,
                                   url[1:], None))
            continue
        if url.startswith(("http://", "https://", "mailto:", "data:", "file:")):
            continue
        path_part, _, anchor = url.partition("#")
        pp = path_part.strip()
        if pp.startswith("./"):
            pp = pp[2:]
        if not pp:
            continue
        stem = pp[:-3] if pp.endswith(".md") else pp
        if stem in doc_stems:
            # 文档集内,有效;锚点部分另行校验
            if anchor and frag_links is not None:
                frag_links.append((doc_name, content[:m.start()].count("\n") + 1,
                                   anchor, stem))
            continue
        # 本地文件链接
        target = (src_dir / path_part).resolve() if not path_part.startswith("/") else Path(path_part)
        if not target.is_file() and not target.is_dir():
            problems.append({
                "severity": "warn", "doc": doc_name, "line": content[:m.start()].count("\n") + 1,
                "kind": "link_dead", "msg": f"链接目标不存在:{url}",
            })


# ==================== 文档加载 ====================


def load_documents(src_dir: Path):
    """加载 README.md + 编号章节。

    返回 (docs, problems, stats, collected):
      docs      — [(stem, title, content, toc), ...]
      problems  — [{severity, doc, line, kind, msg}, ...](校验问题)
      stats     — {stem: {figures, mermaid, codeblocks, formulas}}
      collected — 源码引用/普通 C 代码块收集项,供调用图构建(process_source_blocks)

    content 已处理源码引用(内联/徽章)、内嵌图片、重写链接、转义。
    """
    docs = []
    problems = []
    stats = {}
    doc_stems = set()
    collected: list = []
    frag_links: list = []
    doc_ids: dict = {}  # stem → {id: 出现次数}(标题 slug + 源码引用锚点)

    # 先收集所有 stem(供 rewrite_links 判断文档集内引用)
    readme = src_dir / "README.md"
    if readme.is_file():
        doc_stems.add("README")
    for p in sorted(src_dir.glob(DOC_GLOB)):
        doc_stems.add(p.stem)

    def _process_one(stem: str, raw: str, fallback_title: str):
        stats[stem] = count_stats(raw)
        ids = doc_ids.setdefault(stem, {})
        seen_h: dict = {}
        # H1-H4 标题 slug(JS assignHeadingIds 同款去重:首个原样,后续 -1/-2)
        for hm in re.finditer(r"^(#{1,4})\s+(.+?)\s*$", raw, re.MULTILINE):
            slug = heading_to_slug(hm.group(2))
            if not slug:
                continue
            if slug in seen_h:
                seen_h[slug] += 1
                sid = f"{slug}-{seen_h[slug]}"
            else:
                seen_h[slug] = 0
                sid = slug
            ids[sid] = ids.get(sid, 0) + 1
        # 源码引用处理在前:见原始 ./src/ 路径,emit 原始 HTML(rewrite_links 不再动它)
        content = process_source_blocks(raw, src_dir, problems, stem, collected)
        content = _rstrip_code_fences(content)
        content = rewrite_links(embed_images(content, src_dir), src_dir, doc_stems)
        content = escape_for_script(content)
        # 链接/图片校验(对原始文本,避免被重写干扰)
        check_links_images(raw, src_dir, doc_stems, problems, stem, frag_links)
        title = extract_title(raw, fallback_title)
        toc = extract_toc(raw)
        return (stem, title, content, toc)

    if readme.is_file():
        docs.append(_process_one("README", readme.read_text(encoding="utf-8"), "README"))

    for p in sorted(src_dir.glob(DOC_GLOB)):
        docs.append(_process_one(p.stem, p.read_text(encoding="utf-8"), p.stem))

    # 源码引用锚点并入 id 集,再校验 #锚点 链接
    for e in collected:
        if e.get("anchor_id"):
            ids = doc_ids.setdefault(e["doc"], {})
            ids[e["anchor_id"]] = ids.get(e["anchor_id"], 0) + 1
    check_anchor_links(frag_links, doc_ids, problems)

    return docs, problems, stats, collected


def check_anchor_links(frag_links: list, doc_ids: dict, problems: list):
    """校验 #锚点 链接指向的 id 真实存在(标题 slug 或源码引用锚点)。

    覆盖同文档(#frag)与跨文档(./NN-x.md#frag)两类;重复 id 一并报告。
    链接目标不在文档集内(如 file://)不在此校验,由死链检查覆盖。
    """
    for stem, ids in sorted(doc_ids.items()):
        for aid, cnt in ids.items():
            if cnt > 1:
                problems.append({
                    "severity": "warn", "doc": stem, "line": 0,
                    "kind": "anchor_duplicate",
                    "msg": f"锚点 id 重复({cnt} 处):#{aid}",
                })
    for doc_name, line, frag, target_stem in frag_links:
        target = target_stem or doc_name
        ids = doc_ids.get(target)
        if ids is None:
            continue
        if frag not in ids:
            problems.append({
                "severity": "warn", "doc": doc_name, "line": line,
                "kind": "anchor_missing",
                "msg": f"锚点不存在:#{frag}(目标文档:{target})",
            })


# ==================== HTML 构建 ====================


def build_sidebar(docs, site_title: str) -> str:
    """构建侧边栏 HTML:搜索框 + 文档列表(含 H2 TOC)。"""
    parts = [
        '<aside class="sidebar" id="sidebar">',
        f'<div class="sidebar-header"><span class="sidebar-title">{html.escape(site_title)}</span>',
        '<button class="sidebar-close" id="sidebar-close" title="收起" aria-label="收起侧边栏">✕</button></div>',
        '<div class="sidebar-search">',
        '<input type="text" id="search-input" placeholder="搜索文档标题..." '
        'autocomplete="off" oninput="filterSidebar()">',
        '</div>',
        '<nav class="doc-list" id="doc-list">',
    ]
    for stem, title, _, toc in docs:
        parts.append(
            f'<div class="doc-item" data-doc="{html.escape(stem)}" '
            f'data-title="{html.escape(title.lower())}">'
            f'<a class="doc-link" href="#/{stem}" '
            f'onclick="loadDoc(\'{stem}\'); return false;">{html.escape(title)}</a>'
        )
        if toc:
            parts.append('<ul class="toc-list">')
            for slug, text in toc:
                parts.append(
                    f'<li><a href="#/{stem}#{slug}" '
                    f'onclick="loadDoc(\'{stem}\', \'{slug}\'); return false;">'
                    f'{html.escape(text)}</a></li>'
                )
            parts.append('</ul>')
        parts.append('</div>')
    parts.append('</nav></aside>')
    return "\n".join(parts)


def build_scripts_block(docs) -> str:
    """将每篇 md 嵌入 <script type="text/markdown"> 标签。"""
    parts = []
    for stem, title, content, _ in docs:
        parts.append(
            f'<script type="text/markdown" data-name="{html.escape(stem)}" '
            f'data-title="{html.escape(title)}">\n{content}\n</script>'
        )
    return "\n".join(parts)


def build_docs_meta_json(docs) -> str:
    """生成前端用的 DOCS 元数据 JSON。"""
    meta = [
        {"name": stem, "title": title, "toc": [{"id": s, "text": t} for s, t in toc]}
        for stem, title, _, toc in docs
    ]
    return json.dumps(meta, ensure_ascii=False)


# ---- 资源引用生成 ----


KATEX_FONT_RE = re.compile(r"url\((?:fonts/)?(KaTeX_[A-Za-z0-9_-]+\.(?:woff2|woff))\)")


def inline_katex_fonts(css_text: str, vendor_dir: Path) -> str:
    """将 katex.min.css 中的 url(fonts/X.woff2|woff) 替换为 data:font/...;base64,...。

    KaTeX 依赖 ~60 个自定义字体引用(数学符号、希腊字母、AMS 符号等)。
    若不内嵌,离线/他机打开时数学公式会退化为系统字体,符号可能错位或缺字。

    只内嵌 woff2 与 woff(cdnjs 0.16.9 提供这两种)。.ttf 引用不处理——
    按 CSS Fonts 规范,浏览器只取 src 中第一个它支持的格式,woff2 在前且
    现代浏览器均支持,故 ttf 永远不会被请求,保留为相对路径是安全的死回退。
    字体缓存到 vendor_dir/fonts/。下载失败则保留原相对路径。
    """
    fonts_dir = vendor_dir / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    base_url = "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/fonts/"

    def repl(m):
        fname = m.group(1)
        cache = fonts_dir / fname
        data = None
        if cache.is_file():
            try:
                data = cache.read_bytes()
            except OSError:
                pass
        if data is None:
            try:
                req = urllib.request.Request(base_url + fname,
                                             headers={"User-Agent": "build_html.py/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                cache.write_bytes(data)
            except Exception as e:
                print(f"  ⚠️  KaTeX 字体下载失败 {fname}: {e};保留相对路径", file=sys.stderr)
                return m.group(0)
        mime = "font/woff2" if fname.endswith(".woff2") else "font/woff"
        b64 = base64.b64encode(data).decode("ascii")
        return f"url(data:{mime};base64,{b64})"

    return KATEX_FONT_RE.sub(repl, css_text)


def make_asset_refs(vendor: dict, vendor_dir: Path):
    """把 vendor 字典转成可插入 HTML 的 <style>/<script> 标签字符串。

    返回 (css_block, js_block)。
    内嵌用 <style>/<script>,CDN 用外链。
    KaTeX CSS 内嵌时一并内嵌其字体(保证离线数学公式渲染质量)。
    """
    css_parts = []
    js_parts = []

    # KaTeX CSS(+ 内嵌字体)
    k = vendor["katex.min.css"]
    if k[0] == "inline":
        katex_css = inline_katex_fonts(k[1], vendor_dir)
        css_parts.append(f"<style>\n/* katex.min.css (+ fonts) */\n{katex_css}\n</style>")
    else:
        css_parts.append(f'<link rel="stylesheet" href="{k[1]}">')

    # highlight.js github theme
    g = vendor["github.min.css"]
    if g[0] == "inline":
        css_parts.append(f"<style>\n/* github.min.css (hljs) */\n{g[1]}\n</style>")
    else:
        css_parts.append(f'<link rel="stylesheet" href="{g[1]}">')

    # JS 库:marked → mermaid → katex → auto-render → highlight → 额外语言
    js_order = [
        "marked.min.js",
        "mermaid.min.js",
        "katex.min.js",
        "auto-render.min.js",
        "highlight.min.js",
        "armasm.min.js",
        "x86asm.min.js",
    ]
    for fname in js_order:
        v = vendor[fname]
        if v[0] == "inline":
            js_parts.append(f"<script>\n/* {fname} */\n{v[1]}\n</script>")
        else:
            js_parts.append(f'<script src="{v[1]}"></script>')

    return "\n".join(css_parts), "\n".join(js_parts)


# ==================== 调用图前端资产(自 zephyr-rtos-html 版移植,三处适配见上方说明) ====================

CG_CSS = r"""
/* cg-* 样式使用的变量别名:映射到本脚本既有的 :root 变量 */
:root {
  --fg: var(--text);
  --fg-muted: var(--text-muted);
  --sidebar-hover: var(--bg-soft);
  --sidebar-bg: var(--bg-sidebar);
  --howto-bg: var(--accent-soft);
  --howto-border: var(--accent);
  --core-point-bg: var(--accent-soft);
  --core-point-border: var(--accent);
  --blockquote-bg: var(--bg-soft);
  --blockquote-border: var(--border);
  --search-bg: var(--bg);
  --code-fg: var(--text);
}

/* ===== 调用关系图：内联按钮 + 抽屉 ===== */

/* 代码块下方的「调用图」按钮 */
.cg-btn {
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
}
.cg-btn:hover {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--howto-bg);
}

/* 正文中的函数名可点击链接 */
.cg-inline-link {
  cursor: pointer !important;
  border-bottom: 1px dashed var(--accent);
}
.cg-inline-link:hover {
  background: var(--howto-bg) !important;
  color: var(--accent) !important;
}

/* 正文中的源码文件链接 */
.cg-src-link-inline {
  cursor: pointer !important;
  border-bottom: 1px dashed var(--accent) !important;
}
.cg-src-link-inline:hover {
  background: var(--howto-bg) !important;
  color: var(--accent) !important;
}

/* 文件视图：左栏信息卡片 */
.cg-file-info {
  padding: 24px 16px;
  text-align: center;
}
.cg-file-info-icon {
  font-size: 32px;
  margin-bottom: 8px;
}
.cg-file-info-path {
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 13px;
  color: var(--fg);
  word-break: break-all;
  margin-bottom: 4px;
}
.cg-file-info-lines {
  font-size: 12px;
  color: var(--fg-muted);
  margin-bottom: 12px;
}
.cg-file-info-hint {
  font-size: 11px;
  color: var(--fg-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* ---- 遮罩层 ---- */
.cg-backdrop {
  position: fixed;
  top: var(--topbar-h, 52px);
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0,0,0,0.25);
  z-index: 140;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.25s ease;
}
body.cg-drawer-open .cg-backdrop {
  opacity: 1;
  pointer-events: auto;
}

/* ---- 右侧抽屉（overlay 模式） ---- */
.cg-drawer {
  position: fixed;
  top: var(--topbar-h, 52px);
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
}
.cg-drawer.open {
  transform: translateX(0);
}

.cg-drawer-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--sidebar-bg);
  flex-shrink: 0;
}
.cg-drawer-nav {
  display: flex;
  gap: 2px;
}
.cg-nav-btn {
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
}
.cg-nav-btn:hover:not(:disabled) {
  background: var(--sidebar-hover);
  border-color: var(--accent);
  color: var(--accent);
}
.cg-nav-btn:disabled {
  opacity: 0.3;
  cursor: default;
}
.cg-drawer-title {
  flex: 1;
  font-size: 14px;
  font-weight: 600;
  color: var(--fg);
}
.cg-drawer-close {
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
}
.cg-drawer-close:hover {
  background: var(--sidebar-hover);
  color: var(--fg);
}

/* ---- 搜索框 ---- */
.cg-search-box {
  position: relative;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.cg-search-box input {
  width: 100%;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--search-bg);
  color: var(--fg);
  font-size: 13px;
  outline: none;
}
.cg-search-box input:focus {
  border-color: var(--accent);
}
.cg-search-results {
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
}
.cg-search-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  cursor: pointer;
  border-bottom: 1px solid var(--border);
}
.cg-search-item:last-child {
  border-bottom: none;
}
.cg-search-item:hover {
  background: var(--sidebar-hover);
}
.cg-search-name {
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 13px;
  color: var(--accent);
}
.cg-search-name.cg-search-ext {
  color: var(--fg-muted);
}
.cg-search-doc {
  font-size: 11px;
  color: var(--fg-muted);
}
.cg-search-empty {
  padding: 12px;
  text-align: center;
  color: var(--fg-muted);
  font-size: 13px;
}

/* ---- 抽屉主体 ---- */
.cg-drawer-body {
  flex: 1;
  overflow: hidden;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.cg-empty {
  padding: 32px;
  text-align: center;
  color: var(--fg-muted);
  font-size: 14px;
}

/* ---- 函数信息区 ---- */
.cg-info {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
.cg-fn-name-lg {
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 18px;
  font-weight: 600;
  color: var(--fg);
  margin-bottom: 4px;
}
.cg-signature {
  margin: 4px 0 8px;
  padding: 8px 10px;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  overflow-x: auto;
}
.cg-signature code {
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 12px;
  color: var(--code-fg);
  background: none;
  padding: 0;
  white-space: pre;
}
.cg-meta-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  margin: 4px 0;
  font-size: 12px;
}
.cg-meta-label {
  color: var(--fg-muted);
  font-weight: 600;
  margin-right: 4px;
}
.cg-doc-tag {
  display: inline-block;
  padding: 1px 8px;
  background: var(--howto-bg);
  border: 1px solid var(--howto-border);
  border-radius: 10px;
  font-size: 11px;
  color: var(--fg);
  cursor: pointer;
  transition: all 0.1s;
}
.cg-doc-tag:hover {
  background: var(--accent);
  color: white;
  border-color: var(--accent);
}
.cg-doc-tag.cg-doc-ext {
  background: var(--blockquote-bg);
  border-color: var(--blockquote-border);
}
.cg-more {
  font-size: 11px;
  color: var(--fg-muted);
  margin-left: 4px;
}
.cg-src-link {
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 11px;
  color: var(--accent);
  text-decoration: none;
}
.cg-src-link:hover {
  text-decoration: underline;
}

/* ---- 标签栏 ---- */
.cg-tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.cg-tab {
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
}
.cg-tab:hover {
  color: var(--fg);
  background: var(--sidebar-hover);
}
.cg-tab.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}

/* ---- 调用树 ---- */
.cg-tree-container {
  padding: 8px 0;
  flex: 1;
}
.cg-tree {
  list-style: none;
  margin: 0;
  padding: 0 0 0 20px;
  border-left: 1px solid var(--border);
}
.cg-tree.cg-tree-root {
  padding-left: 16px;
  margin-left: 16px;
}
.cg-tree.collapsed {
  display: none;
}
.cg-tree-empty {
  padding: 8px 16px;
  color: var(--fg-muted);
  font-size: 12px;
  font-style: italic;
}

/* ---- 空状态（叶子节点 / 仅引用函数） ---- */
.cg-empty-state {
  padding: 20px 16px;
  text-align: center;
}
.cg-empty-state-icon {
  font-size: 28px;
  margin-bottom: 8px;
}
.cg-empty-state-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--fg);
  margin-bottom: 6px;
}
.cg-empty-state-desc {
  font-size: 12px;
  color: var(--fg-muted);
  line-height: 1.6;
  margin-bottom: 10px;
}
.cg-empty-state-hint {
  font-size: 12px;
  color: var(--fg-muted);
  margin: 6px 0;
}
.cg-empty-state-hint a {
  color: var(--accent);
  cursor: pointer;
  text-decoration: underline;
}
.cg-empty-state-refs {
  margin-top: 12px;
  text-align: left;
}
.cg-empty-state-refs-label {
  font-size: 11px;
  color: var(--fg-muted);
  margin-bottom: 6px;
}
.cg-empty-state-refs-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.cg-node {
  position: relative;
  margin: 1px 0;
  line-height: 1.8;
}
.cg-node::before {
  content: '';
  position: absolute;
  left: -20px;
  top: 14px;
  width: 16px;
  height: 0;
  border-top: 1px solid var(--border);
}
.cg-toggle {
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
}
.cg-toggle:hover {
  color: var(--accent);
}
.cg-toggle.expanded {
  /* 视觉反馈由文本字符变化体现 */
}
.cg-toggle-placeholder {
  display: inline-block;
  width: 16px;
  flex-shrink: 0;
}
.cg-fn-link {
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 13px;
  cursor: pointer;
  color: var(--accent);
  padding: 1px 4px;
  border-radius: 3px;
  transition: background 0.1s;
}
.cg-fn-link:hover {
  background: var(--sidebar-hover);
  text-decoration: underline;
}
.cg-fn-link.cg-fn-external {
  color: var(--fg-muted);
}
.cg-fn-link.cg-fn-nocode {
  border-bottom: 1px dashed var(--fg-muted);
}
.cg-badge {
  display: inline-block;
  font-size: 10px;
  padding: 0 6px;
  border-radius: 8px;
  background: var(--code-bg);
  color: var(--fg-muted);
  margin-left: 4px;
  line-height: 1.5;
}
.cg-badge-cycle {
  background: var(--core-point-bg);
  color: var(--core-point-border);
}
.cg-badge-undef {
  background: var(--blockquote-bg);
  color: var(--fg-muted);
  font-style: italic;
}
.cg-badge-code {
  background: var(--code-bg);
  color: var(--accent);
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 11px;
  font-weight: 600;
}

/* ---- 函数名展开状态 ---- */
.cg-fn-expanded {
  background: var(--howto-bg);
  font-weight: 600;
}

/* ---- 内联展开区域 ---- */
.cg-inline-expand {
  margin: 8px 0 8px 24px;
  padding: 12px;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
}
.cg-inline-signature {
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 11px;
  color: var(--code-fg);
  margin-bottom: 8px;
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.5;
}
.cg-inline-code-section {
  margin: 8px 0;
}
.cg-inline-code-section summary {
  cursor: pointer;
  font-size: 11px;
  color: var(--fg-muted);
  user-select: none;
  padding: 2px 0;
}
.cg-inline-code-section summary:hover {
  color: var(--accent);
}
.cg-inline-code {
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
}
.cg-inline-code code {
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  color: var(--code-fg);
  background: none;
  white-space: pre;
}
.cg-inline-source-link {
  margin-top: 8px;
  font-size: 11px;
}
.cg-inline-source-link a {
  color: var(--accent);
  text-decoration: none;
}
.cg-inline-source-link a:hover {
  text-decoration: underline;
}

/* ---- 代码区 ---- */
.cg-code-section {
  border-top: 1px solid var(--border);
  margin: 0;
}
.cg-code-section summary {
  padding: 8px 16px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  color: var(--fg);
  user-select: none;
  border-bottom: 1px solid transparent;
  transition: all 0.1s;
}
.cg-code-section summary:hover {
  background: var(--sidebar-hover);
}
.cg-code-section[open] summary {
  border-bottom-color: var(--border);
}
.cg-code {
  margin: 0;
  border: none;
  border-radius: 0;
  background: var(--code-bg);
  overflow-x: auto;
  max-height: 50vh;
  overflow-y: auto;
}
.cg-code > code {
  display: block;
  padding: 10px 14px;
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: var(--code-fg);
  white-space: pre;
  background: none;
}

/* ---- 分栏布局 ---- */
.cg-split-pane {
  display: flex;
  flex: 1;
  overflow: hidden;
  min-height: 0;
}
.cg-pane-left {
  width: 340px;
  min-width: 280px;
  overflow-y: auto;
  border-right: 1px solid var(--border);
  padding: 0;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}
.cg-pane-right {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 0;
}

/* ---- 左栏选中状态 ---- */
.cg-fn-selected {
  background: var(--howto-bg);
  font-weight: 600;
}

/* ---- 右栏详情面板 ---- */
.cg-detail {
  padding: 16px;
}
.cg-detail-header {
  margin-bottom: 12px;
}
.cg-detail-header h3 {
  margin: 0 0 8px 0;
  font-size: 18px;
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  color: var(--fg);
}
.cg-detail-docs {
  margin: 6px 0;
  font-size: 12px;
}
.cg-detail-signature {
  padding: 8px 12px;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  margin: 12px 0;
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 12px;
  overflow-x: auto;
}
.cg-detail-signature code {
  color: var(--code-fg);
  background: none;
  white-space: pre;
}
.cg-detail-source-link {
  margin: 8px 0;
  font-size: 12px;
}
.cg-detail-code-section {
  margin: 16px 0;
}
.cg-detail-section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--fg-muted);
  margin: 12px 0 6px 0;
  text-transform: uppercase;
}
.cg-detail-pre {
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
}
.cg-detail-pre code {
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  color: var(--code-fg);
  background: none;
  white-space: pre;
}
/* 文件视图代码块：允许更高 */
.cg-file-pre {
  max-height: 70vh;
}
.cg-detail-relations {
  margin-top: 16px;
}
.cg-detail-fn-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.cg-detail-fn {
  padding: 2px 8px;
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 3px;
  font-family: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.1s;
}
.cg-detail-fn:hover {
  background: var(--howto-bg);
  border-color: var(--accent);
}

/* ---- 面包屑 ---- */
.cg-breadcrumb {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  font-size: 12px;
  font-family: "JetBrains Mono", monospace;
  color: var(--fg-muted);
  flex-wrap: wrap;
  min-height: 20px;
}
.cg-breadcrumb-sep {
  color: var(--border);
  margin: 0 2px;
}
.cg-breadcrumb-link {
  cursor: pointer;
  color: var(--accent);
  padding: 2px 4px;
  border-radius: 3px;
  transition: background 0.1s;
}
.cg-breadcrumb-link:hover {
  background: var(--sidebar-hover);
  text-decoration: underline;
}
.cg-breadcrumb-current {
  font-weight: 600;
  color: var(--fg);
  padding: 2px 4px;
}
.cg-breadcrumb-root {
  cursor: pointer;
  font-size: 14px;
  padding: 2px 6px;
  border-radius: 3px;
  transition: background 0.1s;
}
.cg-breadcrumb-root:hover {
  background: var(--sidebar-hover);
}

/* ---- 详情占位符 ---- */
.cg-detail-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--fg-muted);
  font-size: 14px;
  padding: 32px;
}

/* ---- 左栏树容器调整 ---- */
.cg-pane-left .cg-tree-container {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}
.cg-pane-left .cg-tabs {
  flex-shrink: 0;
}


/* ---- 移动端 ---- */
@media (max-width: 1200px) {
  .cg-drawer {
    width: 100vw;
    max-width: 100vw;
  }
  .cg-split-pane {
    flex-direction: column;
  }
  .cg-pane-left {
    width: 100%;
    min-width: 0;
    max-width: none;
    max-height: 40vh;
    border-right: none;
    border-bottom: 1px solid var(--border);
  }
  .cg-pane-right {
    width: 100%;
    min-width: 0;
  }
}

/* 顶栏「调用图」按钮 */
.topbar .cg-topbar-btn {
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid var(--accent);
  border-radius: 6px;
  padding: 4px 12px;
  font-size: 12.5px;
  cursor: pointer;
  white-space: nowrap;
}
.topbar .cg-topbar-btn:hover {
  background: var(--accent);
  color: #fff;
}
"""

CALLLGRAPH_JS = r"""
// ============================================================
// 调用图浏览器 (Call Graph Explorer)
// 设计参考：VS Code Call Hierarchy + Source Insight
// ============================================================

var CG = (function () {
  // 根构建脚本注入的配置(sourceMark 用于把 file:// 绝对路径切短;excludePrefixes 用于运行期名过滤)
  var CG_CONFIG = window.__CG_CONFIG__ || { sourceMark: '', excludePrefixes: [] };
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
    'NULL','true','false','container_of',
    'fwk_assert','fwk_expect','fwk_check','fwk_trap','fwk_unexpected',
    'FWK_ASSERT','FWK_WEAK','FWK_ARRAY_SIZE','FWK_MIN','FWK_MAX','FWK_BIT',
    'FWK_ALIGN','FWK_ALIGN_NEXT','FWK_S','FWK_NS','FWK_M','FWK_MS','FWK_US',
    'FWK_LIST_GET','FWK_PRINTF','FWK_NONNULL','FWK_READ_ONLY1',
    'FWK_READ_WRITE1','FWK_WRITE_ONLY1','FWK_ALLOC_SIZE2','FWK_ALLOC_ALIGN',
    'FWK_MODULE_STATIC_ELEMENTS','FWK_MODULE_DYNAMIC_ELEMENTS',
    'FWK_MODULE_STATIC_ELEMENTS_PTR','UINT8_C','UINT16_C','UINT32_C','UINT64_C'
  ]);

  // 名字是否应排除(关键字 + 配置里的前缀),与 Python 端 SCP_EXCLUDE 对应
  function isExcludedName(name) {
    if (KEYWORDS.has(name)) return true;
    var pfx = CG_CONFIG.excludePrefixes || [];
    for (var i = 0; i < pfx.length; i++) {
      if (name.indexOf(pfx[i]) === 0) return true;
    }
    return false;
  }

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
      var shortPath = CG_CONFIG.sourceMark
        ? (srcText.split(CG_CONFIG.sourceMark).pop() || srcText)
        : srcText;
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
      var shortPath = CG_CONFIG.sourceMark
        ? (srcText.split(CG_CONFIG.sourceMark).pop() || srcText)
        : srcText;
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
      if (isExcludedName(name)) return;
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
      if (isExcludedName(name)) { re.lastIndex = m.index + 1; continue; }
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
function initCallGraph() { CG.refresh(); }"""


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TITLE}}</title>
{{ASSET_CSS}}
<style>
/* ==================== 基础布局 ==================== */
:root {
  --bg: #ffffff;
  --bg-soft: #f6f8fa;
  --bg-sidebar: #fbfbfd;
  --border: #d0d7de;
  --border-soft: #e6e8eb;
  --text: #1f2328;
  --text-soft: #59636e;
  --text-muted: #818b98;
  --accent: #0969da;
  --accent-soft: #ddf4ff;
  --code-bg: #f6f8fa;
  --shadow: 0 1px 3px rgba(0,0,0,.08), 0 8px 24px rgba(0,0,0,.06);
  --sidebar-w: 300px;
  --topbar-h: 52px;
  --content-max: 860px;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; height: 100%; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
               "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC",
               Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 15px;
  line-height: 1.7;
  -webkit-font-smoothing: antialiased;
}

/* ==================== 顶栏 ==================== */
.topbar {
  position: fixed; top: 0; left: 0; right: 0; height: var(--topbar-h);
  background: var(--bg);
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 12px;
  padding: 0 16px; z-index: 100;
}
.topbar-hamburger {
  display: none; background: none; border: 1px solid var(--border);
  border-radius: 6px; padding: 4px 10px; cursor: pointer; font-size: 16px;
}
.topbar-title { font-weight: 600; font-size: 15px; color: var(--text); }
.topbar-progress {
  position: absolute; bottom: 0; left: 0; height: 2px;
  background: var(--accent); width: 0; transition: width .1s;
}
.topbar-meta {
  margin-left: auto; color: var(--text-muted); font-size: 12px;
}

/* ==================== 侧边栏 ==================== */
.sidebar {
  position: fixed; top: var(--topbar-h); left: 0; bottom: 0;
  width: var(--sidebar-w); background: var(--bg-sidebar);
  border-right: 1px solid var(--border);
  overflow-y: auto; overflow-x: hidden; z-index: 90;
  display: flex; flex-direction: column;
}
.sidebar-header {
  display: none; padding: 10px 14px; border-bottom: 1px solid var(--border-soft);
  align-items: center; justify-content: space-between;
}
.sidebar-title { font-weight: 600; font-size: 14px; }
.sidebar-close { display: none; background: none; border: none; font-size: 18px; cursor: pointer; color: var(--text-soft); }
.sidebar-search { padding: 10px 14px; position: sticky; top: 0; background: var(--bg-sidebar); z-index: 2; }
.sidebar-search input {
  width: 100%; padding: 6px 10px; border: 1px solid var(--border);
  border-radius: 6px; font-size: 13px; outline: none; background: var(--bg);
}
.sidebar-search input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.doc-list { padding: 4px 8px 32px; flex: 1; }
.doc-item { margin: 1px 0; border-radius: 6px; }
.doc-item:hover { background: var(--bg-soft); }
.doc-link {
  display: block; padding: 5px 10px; color: var(--text);
  text-decoration: none; font-size: 13.5px; border-radius: 6px;
}
.doc-item.active > .doc-link { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
.doc-link:hover { color: var(--accent); }
.toc-list {
  list-style: none; margin: 2px 0 6px; padding: 0 0 0 18px;
  border-left: 2px solid var(--border-soft); margin-left: 14px;
}
.toc-list li a {
  display: block; padding: 3px 10px; color: var(--text-soft);
  text-decoration: none; font-size: 12.5px; border-radius: 4px;
}
.toc-list li a:hover { color: var(--accent); background: var(--bg-soft); }
.toc-list li a.active { color: var(--accent); font-weight: 600; }
.doc-item.hidden { display: none; }

/* ==================== 主内容 ==================== */
.main {
  margin-left: var(--sidebar-w);
  margin-top: var(--topbar-h);
  padding: 32px 40px 120px;
  min-height: calc(100vh - var(--topbar-h));
}
.content { max-width: var(--content-max); margin: 0 auto; }
.content h1, .content h2, .content h3, .content h4 {
  font-weight: 600; line-height: 1.3; margin: 1.6em 0 .6em; scroll-margin-top: 70px;
}
.content h1 { font-size: 1.9em; padding-bottom: .3em; border-bottom: 1px solid var(--border); }
.content h2 { font-size: 1.5em; padding-bottom: .3em; border-bottom: 1px solid var(--border-soft); }
.content h3 { font-size: 1.22em; }
.content h4 { font-size: 1.05em; }
.content p { margin: .8em 0; }
.content a { color: var(--accent); text-decoration: none; }
.content a:hover { text-decoration: underline; }
.content ul, .content ol { padding-left: 1.8em; }
.content li { margin: .25em 0; }
.content blockquote {
  margin: 1em 0; padding: .6em 1em; color: var(--text-soft);
  background: var(--bg-soft); border-left: 3px solid var(--border);
  border-radius: 0 6px 6px 0;
}
.content blockquote p { margin: .3em 0; }
.content blockquote > :first-child { margin-top: 0; }
.content blockquote > :last-child { margin-bottom: 0; }
/* "核心要点" 引用块(> **核心要点**:...)高亮 */
.content blockquote > p:first-child > strong:first-child { color: var(--accent); }
.content img.md-img { max-width: 100%; height: auto; border-radius: 8px; box-shadow: var(--shadow); margin: .8em 0; }
.content hr { border: none; border-top: 1px solid var(--border-soft); margin: 2em 0; }
.content table {
  border-collapse: collapse; width: 100%; margin: 1em 0;
  font-size: 13.5px; display: block; overflow-x: auto;
}
.content table th, .content table td {
  border: 1px solid var(--border); padding: 6px 12px; text-align: left;
}
.content table th { background: var(--bg-soft); font-weight: 600; }
.content table tr:nth-child(2n) { background: var(--bg-soft); }

/* 代码块 */
.content pre {
  background: var(--code-bg); border: 1px solid var(--border-soft);
  border-radius: 8px; padding: 10px 14px; overflow-x: auto;
  margin: 1em 0; font-size: 13px; line-height: 1.45;
}
.content pre code { background: none; padding: 0; border: none; font-size: inherit; }
.content code {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo,
               "Courier New", monospace;
  background: var(--code-bg); padding: 2px 5px; border-radius: 4px;
  font-size: .88em; color: #1f2328;
}
.content pre code.hljs { padding: 0; background: none; }

/* Mermaid */
.content .mermaid { display: flex; justify-content: center; margin: 1em 0; }
.content .mermaid svg { max-width: 100%; height: auto; }

/* 源码引用块:出处徽章 + 行号 */
.content .src-block { margin: 1.2em 0; }
.content .src-block + .src-block { margin-top: 0.4em; }
.content .src-block + ul, .content .src-block + ol { margin-top: 0.8em; }
.content .src-badge {
  font-size: 12px; color: var(--text-soft);
  background: var(--bg-soft); border: 1px solid var(--border-soft);
  border-bottom: none; border-radius: 6px 6px 0 0;
  padding: 4px 10px; display: inline-block;
}
.content .src-badge a { color: var(--accent); text-decoration: none; }
.content .src-badge a:hover { text-decoration: underline; }
.content .src-pre {
  margin-top: 0; border-radius: 0 6px 6px 6px;
  counter-reset: src-ln 0;
}
.content .src-pre[data-start] { counter-reset: src-ln calc(var(--start, 1) - 1); }
.content .src-pre .src-line { display: block; line-height: 1.4; }
.content .src-pre .src-line::before {
  counter-increment: src-ln; content: counter(src-ln);
  display: inline-block; width: 3em; margin-right: 12px;
  text-align: right; color: var(--text-muted);
  user-select: none; border-right: 1px solid var(--border-soft);
  padding-right: 8px; font-variant-numeric: tabular-nums;
}

/* KaTeX 行内公式不要被 code 样式覆盖 */
.content .katex { font-size: 1.05em; }

/* 上一篇/下一篇 */
.doc-nav {
  display: flex; justify-content: space-between; gap: 12px;
  margin-top: 48px; padding-top: 20px; border-top: 1px solid var(--border-soft);
}
.doc-nav a {
  flex: 1; padding: 10px 14px; border: 1px solid var(--border);
  border-radius: 8px; text-decoration: none; color: var(--text);
  font-size: 13px; transition: border-color .15s, background .15s;
}
.doc-nav a:hover { border-color: var(--accent); background: var(--accent-soft); }
.doc-nav a .nav-label { display: block; color: var(--text-muted); font-size: 11px; margin-bottom: 2px; }
.doc-nav a.next { text-align: right; }

/* 加载/错误态 */
.loading, .error-msg {
  text-align: center; padding: 60px 20px; color: var(--text-muted);
}
.error-msg { color: #cf222e; }
.spinner {
  display: inline-block; width: 28px; height: 28px;
  border: 3px solid var(--border-soft); border-top-color: var(--accent);
  border-radius: 50%; animation: spin .8s linear infinite; margin-right: 10px;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* 遮罩(移动端侧边栏打开时) */
.backdrop {
  display: none; position: fixed; inset: 0; background: rgba(0,0,0,.3);
  z-index: 80;
}
.backdrop.show { display: block; }

/* ==================== 响应式 ==================== */
@media (max-width: 900px) {
  :root { --sidebar-w: 280px; }
  .sidebar {
    transform: translateX(-100%); transition: transform .25s ease;
    box-shadow: var(--shadow);
  }
  .sidebar.open { transform: translateX(0); }
  .sidebar-header { display: flex; }
  .sidebar-close { display: block; }
  .topbar-hamburger { display: inline-block; }
  .main { margin-left: 0; padding: 20px 16px 100px; }
}
</style>
{{CG_CSS}}
</head>
<body>

<div class="topbar">
  <button class="topbar-hamburger" id="hamburger" onclick="toggleSidebar()" aria-label="菜单">☰</button>
  <span class="topbar-title">{{TITLE}}</span>
  <span class="topbar-meta" id="topbar-meta"></span>
  {{CG_BUTTON}}
  <div class="topbar-progress" id="progress"></div>
</div>

<div class="backdrop" id="backdrop" onclick="toggleSidebar()"></div>

{{SIDEBAR}}

<main class="main">
  <div class="content" id="content">
    <div class="loading"><span class="spinner"></span>正在加载文档…</div>
  </div>
</main>

{{ASSET_JS}}

<!-- 嵌入的 markdown 文档 -->
{{SCRIPTS}}
{{CG_DATA}}
{{CG_JS}}

<script>
// ==================== 应用状态 ====================
const DOCS = {{DOCS_META}};
const STORAGE_KEY = "md-html-last-doc";
let currentDoc = null;

const el = {
  content: document.getElementById('content'),
  sidebar: document.getElementById('sidebar'),
  searchInput: document.getElementById('search-input'),
  docList: document.getElementById('doc-list'),
  progress: document.getElementById('progress'),
  topbarMeta: document.getElementById('topbar-meta'),
  hamburger: document.getElementById('hamburger'),
  backdrop: document.getElementById('backdrop'),
};

// ==================== Markdown 渲染配置 ====================
function setupMarked() {
  // marked v9.1.6 兼容配置
  const opts = { gfm: true, breaks: false, headerIds: false, mangle: false };
  try { marked.setOptions(opts); } catch (e) {}
  // 自定义 renderer:不在 heading 上加 id(由 assignHeadingIds 统一处理,避免中文被丢弃)
  try {
    const renderer = new marked.Renderer();
    renderer.heading = function (text, level) {
      // 兼容 v9 (text, level, raw, slugger) 与 v12+ ({text, depth}) 两种签名
      if (typeof text === 'object' && text !== null) {
        const t = text.text || ''; const d = text.depth || 2;
        return '<h' + d + '>' + t + '</h' + d + '>\n';
      }
      return '<h' + level + '>' + text + '</h' + level + '>\n';
    };
    marked.use({ renderer: renderer });
  } catch (e) {
    try { marked.setOptions({ renderer: new marked.Renderer() }); } catch(e2) {}
  }
}

// ==================== 数学公式保护 ====================
// marked 不认识 LaTeX:$..$ 与 $$..$$ 里的下划线、反斜杠会被 GFM 规则重写
// (\underbrace{}_{...} 变斜体、下划线被吞),KaTeX 拿到的是残缺公式。
// 策略:解析前把数学片段抽成纯文字占位符,解析完原样放回(见 loadDoc)。
function extractMathSpans(md) {
  var store = [];
  function stash(s) { store.push(s); return 'ZZMATHSPAN' + (store.length - 1) + 'ZZMATHSPAN'; }
  var lines = md.split('\n');
  var out = [];
  var inFence = false;
  var blockBuf = null;              // 非 null:正在收集跨行块级 $$ 公式的内容
  for (var li = 0; li < lines.length; li++) {
    var L = lines[li];
    if (/^\s{0,3}(`{3,}|~{3,})/.test(L)) {
      inFence = !inFence;           // 围栏开/闭行原样保留,$ 不参与配对
      blockBuf = null;
      out.push(L);
      continue;
    }
    if (inFence) { out.push(L); continue; }

    // 行内代码段先占位,避免 `a $b c d$ e` 这类误配
    var codes = [];
    var work = L.replace(/(`+)([\s\S]*?)\1/g, function (m) {
      codes.push(m);
      return '' + (codes.length - 1) + '';
    });

    // 块级 $$..$$ 可跨行
    var emit = [];
    var rest = work;
    if (blockBuf !== null) {
      var cl = rest.indexOf('$$');
      if (cl >= 0) {
        blockBuf += '\n' + rest.slice(0, cl);
        emit.push(stash(blockBuf));
        blockBuf = null;
        rest = rest.slice(cl + 2);
      } else {
        blockBuf += '\n' + rest;
        rest = '';
      }
    }
    while (blockBuf === null) {
      var d = rest.indexOf('$$');
      if (d < 0) break;
      emit.push(rest.slice(0, d));
      var after = rest.slice(d + 2);
      var c2 = after.indexOf('$$');
      if (c2 >= 0) {
        emit.push(stash('$$' + after.slice(0, c2) + '$$'));
        rest = after.slice(c2 + 2);
      } else {
        blockBuf = after;
        rest = '';
      }
    }
    emit.push(rest);

    // 行内 $..$:同行、内容非空、首尾无空白;$ 转义不作起始或终止
    var line2 = emit.join('').replace(/(^|[^\\$])\$(?!\s)((?:\\.|[^\n\\$])+?)(?<![\s\\])\$/g,
      function (m, pre, inner) { return pre + stash('$' + inner + '$'); });
    line2 = line2.replace(/(\d+)/g, function (m, k) { return codes[+k]; });
    out.push(line2);
  }
  return { md: out.join('\n'), store: store };
}

// ==================== slugify(与 Python 端一致) ====================
function slugify(text) {
  let s = String(text).replace(/[^\u4e00-\u9fa5A-Za-z0-9\s-]/g, '');
  s = s.replace(/\s+/g, '-').trim().toLowerCase();
  return s;
}

function stripHtml(html) {
  const d = document.createElement('div');
  d.innerHTML = html;
  return d.textContent || '';
}

// 给所有标题分配 id(保留中文),处理重复
function assignHeadingIds(root) {
  const seen = {};
  root.querySelectorAll('h1, h2, h3, h4').forEach(function (h) {
    let slug = slugify(h.textContent || '');
    if (!slug) slug = 'heading';
    if (seen[slug] !== undefined) { seen[slug]++; slug = slug + '-' + seen[slug]; }
    else { seen[slug] = 0; }
    h.id = slug;
  });
}

// ==================== Mermaid 渲染 ====================
function renderMermaid(root) {
  if (typeof mermaid === 'undefined') return;
  const blocks = root.querySelectorAll('code.language-mermaid');
  if (blocks.length === 0) return;
  try {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'neutral',
      securityLevel: 'loose',
      fontFamily: 'inherit',
      flowchart: { useMaxWidth: true, htmlLabels: true, curve: 'basis' },
      sequence: { useMaxWidth: true, actorMargin: 50, boxMargin: 10 },
      themeVariables: {
        fontSize: '14px',
        primaryColor: '#ddf4ff', primaryBorderColor: '#0969da',
        primaryTextColor: '#1f2328', lineColor: '#656d76',
        secondaryColor: '#f6f8fa', tertiaryColor: '#ffffff'
      }
    });
  } catch (e) {}
  const nodes = [];
  blocks.forEach(function (code) {
    const pre = code.parentElement;
    if (!pre || pre.tagName !== 'PRE') return;
    const src = code.textContent;
    const div = document.createElement('div');
    div.className = 'mermaid';
    div.textContent = src;
    pre.replaceWith(div);
    nodes.push(div);
  });
  if (nodes.length) {
    try {
      mermaid.run({ nodes: nodes }).catch(function (e) {
        console.warn('mermaid render error:', e);
      });
    } catch (e) { console.warn('mermaid.run:', e); }
  }
}

// ==================== 代码高亮 ====================
function highlightCode(root) {
  if (typeof hljs === 'undefined') return;
  root.querySelectorAll('pre code').forEach(function (block) {
    if (block.classList.contains('language-mermaid')) return;  // mermaid 单独处理
    if (block.classList.contains('hljs')) return;              // 已处理
    try { hljs.highlightElement(block); } catch (e) {}
  });
  // 源码引用块:按行包裹 + 行号(CSS counter),起始行号由 data-start 控制。
  // 不能对 innerHTML 直接 split('\n'):hljs 的 span 常跨多行,切碎后标签不配对,
  // 浏览器把错乱的行内/块级嵌套拆成匿名块盒,行距就乱了。这里走 token 流重组,
  // 跨行元素在行界处自然留待后续内容、每行重开浅克隆,DOM 全程配对完好。
  root.querySelectorAll('pre.src-pre').forEach(function (pre) {
    var start = parseInt(pre.getAttribute('data-start') || '1', 10);
    pre.style.setProperty('--start', start);
    var code = pre.querySelector('code');
    if (!code || !code.childNodes.length) return;

    // 1) 拍平成 token 流(open/close/text)
    var tokens = [];
    (function flatten(node) {
      node.childNodes.forEach(function (n) {
        if (n.nodeType === Node.TEXT_NODE) tokens.push({ t: 'txt', s: n.nodeValue });
        else if (n.nodeType === Node.ELEMENT_NODE) {
          tokens.push({ t: 'open', proto: n });
          flatten(n);
          tokens.push({ t: 'close', proto: n });
        }
      });
    })(code);

    // 2) 按行重建 DOM:每行一个 <span class="src-line">,
    //    尚未闭合的元素在新行开头浅克隆重开(close 时沿 parentNode 回退)
    var lines = [], cur = null, openProtos = [];
    function mkLine() {
      cur = { root: document.createElement('span'), tail: null, raw: '' };
      cur.root.className = 'src-line';
      cur.tail = cur.root;
      openProtos.forEach(function (p) {
        var cl = p.cloneNode(false);
        cur.tail.appendChild(cl);
        cur.tail = cl;
      });
      lines.push(cur);
    }
    mkLine();
    tokens.forEach(function (tk) {
      if (tk.t === 'txt') {
        var parts = tk.s.split('\n');
        parts.forEach(function (p, i) {
          if (i > 0) mkLine();
          if (p) { cur.tail.appendChild(document.createTextNode(p)); cur.raw += p; }
        });
      } else if (tk.t === 'open') {
        var cl = tk.proto.cloneNode(false);
        cur.tail.appendChild(cl);
        cur.tail = cl;
        openProtos.push(tk.proto);
      } else {
        openProtos.pop();
        cur.tail = cur.tail.parentNode;
      }
    });

    // 3) 收尾:源码体不以换行结尾,最后一行为空即为伪行,去掉;
    //    空行补一个空格占位,保证行盒高度一致(行号不错位)
    if (lines.length > 1 && lines[lines.length - 1].raw === '') lines.pop();
    lines.forEach(function (ln) {
      if (ln.raw.trim() === '') ln.root.appendChild(document.createTextNode(' '));
      code.appendChild(ln.root);
    });
    while (code.firstChild !== lines[0].root && code.firstChild) {
      code.removeChild(code.firstChild);
    }
  });
}

// ==================== KaTeX 数学公式 ====================
function renderMath(root) {
  if (typeof renderMathInElement === 'undefined') return;
  try {
    renderMathInElement(root, {
      delimiters: [
        { left: '$$', right: '$$', display: true },
        { left: '$', right: '$', display: false },
        { left: '\\(', right: '\\)', display: false },
        { left: '\\[', right: '\\]', display: true }
      ],
      ignoredTags: ['script', 'style', 'noscript', 'code', 'pre', 'textarea'],
      ignoredClasses: ['mermaid', 'katex', 'katex-display'],
      throwOnError: false
    });
  } catch (e) { console.warn('katex:', e); }
}

// ==================== 文档加载 ====================
function getEmbeddedDoc(name) {
  const s = document.querySelector('script[type="text/markdown"][data-name="' + CSS.escape(name) + '"]');
  return s ? s.textContent : null;
}

function loadDoc(name, anchor) {
  const doc = DOCS.find(function (d) { return d.name === name; });
  if (!doc) {
    el.content.innerHTML = '<div class="error-msg"><h2>文档不存在</h2><p>找不到文档:' +
      '<code>' + name + '</code></p></div>';
    return;
  }
  const md = getEmbeddedDoc(name);
  if (md === null) {
    el.content.innerHTML = '<div class="error-msg"><h2>文档内容缺失</h2></div>';
    return;
  }

  let htmlText;
  const guarded = extractMathSpans(md);   // 先抽出数学片段,防止 marked 吃掉 \underbrace{}_{ } 的下划线
  try { htmlText = marked.parse(guarded.md); }
  catch (e) {
    el.content.innerHTML = '<div class="error-msg"><h2>Markdown 解析失败</h2><pre>' +
      String(e) + '</pre></div>';
    return;
  }
  // 公式片段原样放回(HTML 转义;KaTeX auto-render 靠文本节点识别 $ 分隔符)
  htmlText = htmlText.replace(/ZZMATHSPAN(\d+)ZZMATHSPAN/g, function (m, idx) {
    var t = guarded.store[+idx];
    return t === undefined ? m : escapeHtml(t);
  });

  el.content.innerHTML = htmlText;
  assignHeadingIds(el.content);
  renderMermaid(el.content);    // 同步替换 DOM,异步渲染 SVG
  highlightCode(el.content);
  renderMath(el.content);       // ignoredClasses 跳过 mermaid
  if (window.initCallGraph) initCallGraph();  // 代码块调用图按钮 + 行内函数链接(需在高亮后跑)

  // 上一篇/下一篇
  appendDocNav(doc);

  // 更新侧边栏高亮
  updateSidebarActive(name);

  // 顶部元信息
  const idx = DOCS.findIndex(function (d) { return d.name === name; });
  el.topbarMeta.textContent = (idx + 1) + ' / ' + DOCS.length;

  // 记忆
  currentDoc = name;
  try { localStorage.setItem(STORAGE_KEY, name); } catch (e) {}

  // 滚动
  if (anchor) {
    setTimeout(function () {
      const target = document.getElementById(anchor);
      if (target) target.scrollIntoView({ behavior: 'auto', block: 'start' });
    }, 60);
  } else {
    window.scrollTo(0, 0);
  }

  // 关闭移动端侧边栏
  if (window.innerWidth <= 900) closeSidebar();

  // 进度条
  updateProgress();
}

function appendDocNav(doc) {
  const idx = DOCS.findIndex(function (d) { return d.name === doc.name; });
  const prev = idx > 0 ? DOCS[idx - 1] : null;
  const next = idx < DOCS.length - 1 ? DOCS[idx + 1] : null;
  if (!prev && !next) return;
  const nav = document.createElement('div');
  nav.className = 'doc-nav';
  if (prev) {
    nav.innerHTML += '<a class="prev" href="#/' + prev.name + '" onclick="loadDoc(\'' +
      prev.name + '\'); return false;"><span class="nav-label">← 上一篇</span>' +
      escapeHtml(prev.title) + '</a>';
  } else {
    nav.innerHTML += '<span></span>';
  }
  if (next) {
    nav.innerHTML += '<a class="next" href="#/' + next.name + '" onclick="loadDoc(\'' +
      next.name + '\'); return false;"><span class="nav-label">下一篇 →</span>' +
      escapeHtml(next.title) + '</a>';
  }
  el.content.appendChild(nav);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

// ==================== 侧边栏 ====================
function updateSidebarActive(name) {
  el.docList.querySelectorAll('.doc-item').forEach(function (item) {
    item.classList.toggle('active', item.dataset.doc === name);
  });
  // 滚动 active 项可见
  const active = el.docList.querySelector('.doc-item.active');
  if (active) active.scrollIntoView({ block: 'nearest' });
}

function filterSidebar() {
  const q = (el.searchInput.value || '').toLowerCase().trim();
  el.docList.querySelectorAll('.doc-item').forEach(function (item) {
    const match = !q || (item.dataset.title || '').indexOf(q) !== -1;
    item.classList.toggle('hidden', !match);
  });
}

function toggleSidebar() {
  el.sidebar.classList.toggle('open');
  el.backdrop.classList.toggle('show', el.sidebar.classList.contains('open'));
}
function closeSidebar() {
  el.sidebar.classList.remove('open');
  el.backdrop.classList.remove('show');
}

// ==================== 进度条 ====================
function updateProgress() {
  const st = window.scrollY;
  const sh = document.documentElement.scrollHeight - window.innerHeight;
  const pct = sh > 0 ? (st / sh * 100) : 0;
  el.progress.style.width = pct + '%';
}

// ==================== 路由 ====================
function loadFromHash() {
  const hash = location.hash.slice(1);  // 去掉 #
  if (!hash) {
    // 无 hash:读取 localStorage 或默认第一篇
    let start = DOCS[0] ? DOCS[0].name : null;
    try {
      const last = localStorage.getItem(STORAGE_KEY);
      if (last && DOCS.find(function (d) { return d.name === last; })) start = last;
    } catch (e) {}
    if (start) loadDoc(start);
    return;
  }
  // hash 格式:/doc-name 或 /doc-name#anchor
  if (hash[0] === '/') {
    const rest = hash.slice(1);
    const [name, anchor] = rest.split('#');
    if (name && DOCS.find(function (d) { return d.name === name; })) {
      loadDoc(name, anchor || null);
      return;
    }
  }
  // 兼容纯锚点 #anchor(当前文档内跳转)
  if (hash) {
    const t = document.getElementById(hash);
    if (t) t.scrollIntoView();
  }
}

// ==================== 初始化 ====================
function init() {
  if (typeof marked === 'undefined') {
    el.content.innerHTML = '<div class="error-msg"><h2>前端库加载失败</h2>' +
      '<p>marked.js 未能加载。若以 file:// 打开且使用 CDN,浏览器可能阻止混合内容。</p>' +
      '<p>建议:重新运行构建脚本内嵌前端库(默认行为)。</p></div>';
    return;
  }
  setupMarked();
  window.addEventListener('hashchange', loadFromHash);
  window.addEventListener('scroll', updateProgress, { passive: true });
  el.searchInput.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { el.searchInput.value = ''; filterSidebar(); }
  });
  document.addEventListener('keydown', function (e) {
    if ((e.altKey || e.metaKey) && e.key === 'ArrowLeft')  { e.preventDefault(); navDoc(-1); }
    if ((e.altKey || e.metaKey) && e.key === 'ArrowRight') { e.preventDefault(); navDoc(1); }
  });
  if (window.CG) CG.init();  // 调用图浏览器(未启用专题无 CG,自动跳过)
  loadFromHash();
}

function navDoc(delta) {
  if (!currentDoc) return;
  const idx = DOCS.findIndex(function (d) { return d.name === currentDoc; });
  const ni = idx + delta;
  if (ni >= 0 && ni < DOCS.length) {
    location.hash = '#/' + DOCS[ni].name;
  }
}

window.loadDoc = loadDoc;
window.filterSidebar = filterSidebar;
window.toggleSidebar = toggleSidebar;
init();
</script>

</body>
</html>
"""


def build_html(docs, site_title: str, vendor: dict, vendor_dir: Path,
               cg: dict | None = None) -> str:
    """组装最终 HTML。cg 为调用图资源(css/button/data/js),未启用时为 None。"""
    sidebar_html = build_sidebar(docs, site_title)
    scripts_html = build_scripts_block(docs)
    docs_meta_json = build_docs_meta_json(docs)
    css_block, js_block = make_asset_refs(vendor, vendor_dir)

    out = HTML_TEMPLATE
    out = out.replace("{{TITLE}}", html.escape(site_title))
    out = out.replace("{{SIDEBAR}}", sidebar_html)
    out = out.replace("{{SCRIPTS}}", scripts_html)
    out = out.replace("{{DOCS_META}}", docs_meta_json)
    out = out.replace("{{ASSET_CSS}}", css_block)
    out = out.replace("{{ASSET_JS}}", js_block)
    out = out.replace("{{CG_CSS}}", cg["css"] if cg else "")
    out = out.replace("{{CG_BUTTON}}", cg["button"] if cg else "")
    out = out.replace("{{CG_DATA}}", cg["data"] if cg else "")
    out = out.replace("{{CG_JS}}", cg["js"] if cg else "")
    return out


# ==================== 入口 ====================


def main():
    ap = argparse.ArgumentParser(
        description="将 Markdown 笔记目录转换为单文件 HTML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python3 build_html.py trusted-firmware\n"
               "  python3 build_html.py nccl --output nccl/index.html --title 'NCCL 学习笔记'\n"
               "  python3 build_html.py zephyr-rtos --output zephyr-rtos-html/index.html\n"
               "  python3 build_html.py trusted-firmware --cdn   # 不内嵌前端库,使用 CDN\n"
               "  python3 build_html.py trusted-firmware --check # 只校验不构建\n"
               "  python3 build_html.py trusted-firmware --check --json  # 机器可读校验结果",
    )
    ap.add_argument("src_dir", type=str, help="Markdown 文档目录(含 README.md + 编号章节)")
    ap.add_argument("-o", "--output", type=str, default=None,
                    help=f"输出 HTML 文件路径(默认:<src-dir>/{DEFAULT_OUTPUT_NAME})")
    ap.add_argument("-t", "--title", type=str, default=None,
                    help="站点标题(默认:从 README H1 提取,或用目录名)")
    ap.add_argument("--vendor-dir", type=str, default=str(DEFAULT_VENDOR_DIR),
                    help=f"前端库缓存目录(默认:{DEFAULT_VENDOR_DIR})")
    ap.add_argument("--cdn", action="store_true",
                    help="不内嵌前端库,使用 CDN 链接(文件小,但查看时需联网)")
    ap.add_argument("--check", action="store_true",
                    help="只校验(死链/缺图/坏 mermaid/源码引用问题),不写 HTML。有 error 退出码非 0。")
    ap.add_argument("--json", action="store_true",
                    help="输出机器可读 JSON(每篇渲染统计 + 问题列表),供 agent 解析。")
    ap.add_argument("--callgraph", dest="cg", action="store_true", default=None,
                    help="强制启用调用图浏览器(默认:专题目录存在 callgraph_overrides.json 时启用)")
    ap.add_argument("--no-callgraph", dest="cg", action="store_false",
                    help="禁用调用图浏览器")
    args = ap.parse_args()

    src_dir = Path(args.src_dir).resolve()
    if not src_dir.is_dir():
        print(f"错误:源目录不存在:{src_dir}", file=sys.stderr)
        sys.exit(1)

    # 加载文档(同时校验、收集统计、处理源码引用)
    docs, problems, stats, collected = load_documents(src_dir)
    if not docs:
        print(f"错误:未在 {src_dir} 找到 README.md 或 {DOC_GLOB}", file=sys.stderr)
        sys.exit(1)

    n_err = sum(1 for p in problems if p["severity"] == "error")
    n_warn = sum(1 for p in problems if p["severity"] == "warn")

    # ---- --check / --json:只输出不构建 ----
    if args.check:
        if args.json:
            payload = {
                "src_dir": str(src_dir),
                "docs": [
                    {"name": s, "title": t, "stats": stats.get(s, {})}
                    for s, t, _, _ in docs
                ],
                "problems": problems,
                "summary": {"docs": len(docs), "errors": n_err, "warnings": n_warn},
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"校验 {src_dir}:{len(docs)} 篇文档,{n_err} 错误,{n_warn} 警告", file=sys.stderr)
            for p in sorted(problems, key=lambda x: (x["severity"] != "error", x["doc"], x["line"])):
                sev = "✖" if p["severity"] == "error" else "⚠"
                print(f"  {sev} {p['doc']}:{p['line']} [{p['kind']}] {p['msg']}", file=sys.stderr)
            if n_err == 0:
                print("✅ 校验通过(无 error)", file=sys.stderr)
        sys.exit(1 if n_err else 0)

    # ---- 正常构建 ----
    # 输出路径
    if args.output:
        output_file = Path(args.output).resolve()
    else:
        output_file = src_dir / DEFAULT_OUTPUT_NAME
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # 站点标题
    site_title = args.title
    if not site_title:
        readme = src_dir / "README.md"
        if readme.is_file():
            rc = readme.read_text(encoding="utf-8")
            site_title = extract_title(rc, src_dir.name)
        else:
            site_title = src_dir.name

    print(f"源目录:  {src_dir}", file=sys.stderr)
    print(f"输出:    {output_file}", file=sys.stderr)
    print(f"标题:    {site_title}", file=sys.stderr)
    print(f"\n加载文档 ... {len(docs)} 篇", file=sys.stderr)
    for stem, title, _, toc in docs:
        st = stats.get(stem, {})
        print(f"    - {stem}: {title} ({len(toc)} H2, "
              f"{st.get('mermaid', 0)} 图, {st.get('codeblocks', 0)} 代码块)", file=sys.stderr)
    if problems:
        print(f"  校验:{n_err} 错误,{n_warn} 警告(详见 --check)", file=sys.stderr)

    # 前端库
    vendor_dir = Path(args.vendor_dir).resolve()
    print(f"\n准备前端库 (vendor: {vendor_dir}) ...", file=sys.stderr)
    vendor = download_vendor(vendor_dir, use_cdn=args.cdn)
    inline_n = sum(1 for v in vendor.values() if v[0] == "inline")
    print(f"  内嵌 {inline_n} / {len(vendor)} 个库", file=sys.stderr)

    # 调用图(opt-in:专题目录存在 callgraph_overrides.json,或 --callgraph 强制)
    cg = None
    cg_enabled = args.cg if args.cg is not None else (src_dir / "callgraph_overrides.json").is_file()
    if cg_enabled:
        cfg = load_callgraph_config(src_dir) or default_callgraph_config(src_dir)
        graph, defined_n, filled_n = build_call_graph(src_dir, collected, cfg)
        cg_json = json.dumps(graph, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        print(f"\n调用图:{len(graph)} 函数(笔记内定义 {defined_n},源码回填 {filled_n}),"
              f"JSON {len(cg_json.encode('utf-8')) / 1024:.0f} KB", file=sys.stderr)
        cg_conf = {"sourceMark": cfg.get("source_mark") or "",
                   "excludePrefixes": list(cfg.get("exclude_prefixes") or [])}
        cg = {
            "css": f"<style>\n{CG_CSS}\n</style>",
            "button": ('<button class="cg-topbar-btn" onclick="CG.openDrawer()" '
                       'title="打开调用图浏览器(搜索函数、查看调用/被调用关系)">调用图</button>'),
            "data": f'<script id="cg-data" type="application/json">{cg_json}</script>',
            "js": ('<script>window.__CG_CONFIG__ = '
                   + json.dumps(cg_conf, ensure_ascii=False) + ';</script>\n'
                   + f"<script>\n{CALLLGRAPH_JS}\n</script>"),
        }

    # 构建
    print(f"\n构建 HTML ...", file=sys.stderr)
    html_content = build_html(docs, site_title, vendor, vendor_dir, cg)

    # 写入
    output_file.write_text(html_content, encoding="utf-8")
    size_kb = output_file.stat().st_size / 1024
    print(f"\n✅ 完成:{output_file}", file=sys.stderr)
    print(f"   大小:{size_kb:.1f} KB ({size_kb/1024:.2f} MB)", file=sys.stderr)
    print(f"   打开:file://{output_file}", file=sys.stderr)
    print(f"   或:  cd {output_file.parent} && python3 -m http.server 8000", file=sys.stderr)


if __name__ == "__main__":
    main()