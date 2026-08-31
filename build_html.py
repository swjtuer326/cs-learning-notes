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


def process_source_blocks(content: str, src_dir: Path, problems: list, doc_name: str) -> str:
    """识别并转换源码引用代码块(新语法 + 旧注释)。

    新语法空体 fence 从源码拉取填入;两种写法都渲染成带徽章/行号/锚点的 HTML。
    问题(文件缺失、行区间越界)记入 problems。
    """
    lines = content.split("\n")
    out: list[str] = []
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
            # 确保 HTML 块前有空行,marked 才能识别为 HTML block 原样透传
            if out and out[-1].strip() != "":
                out.append("")
            out.append(_render_src_block(display, fpath, start, end, body, lang, anchor_id))
            # HTML 块后必须补空行:CommonMark type-6 HTML block 以空行结束,
            # 否则 marked 把后续 markdown 原样吞进 <div>(正文"进入代码块")
            out.append("")
            i = close + 1
            continue

        # 普通代码块:原样保留(fence 尾巴若有非 src 属性,剥到只剩 lang)
        out.append(f"```{lang}" if attrs_str.strip() else lines[i])
        out.extend(lines[body_start:close])
        if close < n:
            out.append("```")
        i = close + 1
    return "\n".join(out)


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
                       problems: list, doc_name: str):
    """校验文档内链接与图片:死链、缺图。"""
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
        if url.startswith(("http://", "https://", "mailto:", "#", "data:", "file:")):
            continue
        path_part, _, anchor = url.partition("#")
        pp = path_part.strip()
        if pp.startswith("./"):
            pp = pp[2:]
        if not pp:
            continue
        stem = pp[:-3] if pp.endswith(".md") else pp
        if stem in doc_stems:
            continue  # 文档集内,有效
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

    返回 (docs, problems, stats):
      docs    — [(stem, title, content, toc), ...]
      problems — [{severity, doc, line, kind, msg}, ...](校验问题)
      stats   — {stem: {figures, mermaid, codeblocks, formulas}}

    content 已处理源码引用(内联/徽章)、内嵌图片、重写链接、转义。
    """
    docs = []
    problems = []
    stats = {}
    doc_stems = set()

    # 先收集所有 stem(供 rewrite_links 判断文档集内引用)
    readme = src_dir / "README.md"
    if readme.is_file():
        doc_stems.add("README")
    for p in sorted(src_dir.glob(DOC_GLOB)):
        doc_stems.add(p.stem)

    def _process_one(stem: str, raw: str, fallback_title: str):
        stats[stem] = count_stats(raw)
        # 源码引用处理在前:见原始 ./src/ 路径,emit 原始 HTML(rewrite_links 不再动它)
        content = process_source_blocks(raw, src_dir, problems, stem)
        content = _rstrip_code_fences(content)
        content = rewrite_links(embed_images(content, src_dir), src_dir, doc_stems)
        content = escape_for_script(content)
        # 链接/图片校验(对原始文本,避免被重写干扰)
        check_links_images(raw, src_dir, doc_stems, problems, stem)
        title = extract_title(raw, fallback_title)
        toc = extract_toc(raw)
        return (stem, title, content, toc)

    if readme.is_file():
        docs.append(_process_one("README", readme.read_text(encoding="utf-8"), "README"))

    for p in sorted(src_dir.glob(DOC_GLOB)):
        docs.append(_process_one(p.stem, p.read_text(encoding="utf-8"), p.stem))

    return docs, problems, stats


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
</head>
<body>

<div class="topbar">
  <button class="topbar-hamburger" id="hamburger" onclick="toggleSidebar()" aria-label="菜单">☰</button>
  <span class="topbar-title">{{TITLE}}</span>
  <span class="topbar-meta" id="topbar-meta"></span>
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


def build_html(docs, site_title: str, vendor: dict, vendor_dir: Path) -> str:
    """组装最终 HTML。"""
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
    args = ap.parse_args()

    src_dir = Path(args.src_dir).resolve()
    if not src_dir.is_dir():
        print(f"错误:源目录不存在:{src_dir}", file=sys.stderr)
        sys.exit(1)

    # 加载文档(同时校验、收集统计、处理源码引用)
    docs, problems, stats = load_documents(src_dir)
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

    # 构建
    print(f"\n构建 HTML ...", file=sys.stderr)
    html_content = build_html(docs, site_title, vendor, vendor_dir)

    # 写入
    output_file.write_text(html_content, encoding="utf-8")
    size_kb = output_file.stat().st_size / 1024
    print(f"\n✅ 完成:{output_file}", file=sys.stderr)
    print(f"   大小:{size_kb:.1f} KB ({size_kb/1024:.2f} MB)", file=sys.stderr)
    print(f"   打开:file://{output_file}", file=sys.stderr)
    print(f"   或:  cd {output_file.parent} && python3 -m http.server 8000", file=sys.stderr)


if __name__ == "__main__":
    main()