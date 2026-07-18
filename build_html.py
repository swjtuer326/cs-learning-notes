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


# ==================== 文档加载 ====================


def load_documents(src_dir: Path):
    """加载 README.md + 编号章节。返回 [(stem, title, content, toc), ...]。

    content 已内嵌图片、重写链接、转义为可安全嵌入 <script> 的文本。
    """
    docs = []
    doc_stems = set()

    # 先收集所有 stem(供 rewrite_links 判断文档集内引用)
    readme = src_dir / "README.md"
    if readme.is_file():
        doc_stems.add("README")
    for p in sorted(src_dir.glob(DOC_GLOB)):
        doc_stems.add(p.stem)

    # README 放最前
    if readme.is_file():
        raw = readme.read_text(encoding="utf-8")
        content = rewrite_links(embed_images(raw, src_dir), src_dir, doc_stems)
        content = escape_for_script(content)
        title = extract_title(raw, "README")
        toc = extract_toc(raw)
        docs.append(("README", title, content, toc))

    for p in sorted(src_dir.glob(DOC_GLOB)):
        raw = p.read_text(encoding="utf-8")
        content = rewrite_links(embed_images(raw, src_dir), src_dir, doc_stems)
        content = escape_for_script(content)
        title = extract_title(raw, p.stem)
        toc = extract_toc(raw)
        docs.append((p.stem, title, content, toc))

    return docs


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
  border-radius: 8px; padding: 12px 14px; overflow-x: auto;
  margin: 1em 0; font-size: 13px; line-height: 1.5;
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
  // 额外语言注册
  if (typeof hljs !== 'undefined' && typeof hljs.registerLanguage === 'function') {
    // armasm / x86asm 在独立 <script> 里会自行注册到 window.hljs
  }
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
  try { htmlText = marked.parse(md); }
  catch (e) {
    el.content.innerHTML = '<div class="error-msg"><h2>Markdown 解析失败</h2><pre>' +
      String(e) + '</pre></div>';
    return;
  }

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
               "  python3 build_html.py trusted-firmware --cdn   # 不内嵌前端库,使用 CDN",
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
    args = ap.parse_args()

    src_dir = Path(args.src_dir).resolve()
    if not src_dir.is_dir():
        print(f"错误:源目录不存在:{src_dir}", file=sys.stderr)
        sys.exit(1)

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

    # 加载文档
    print(f"\n加载文档 ...", file=sys.stderr)
    docs = load_documents(src_dir)
    if not docs:
        print(f"错误:未在 {src_dir} 找到 README.md 或 {DOC_GLOB}", file=sys.stderr)
        sys.exit(1)
    print(f"  共 {len(docs)} 篇文档", file=sys.stderr)
    for stem, title, _, toc in docs:
        print(f"    - {stem}: {title} ({len(toc)} H2)", file=sys.stderr)

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