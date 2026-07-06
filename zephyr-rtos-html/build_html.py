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
from pathlib import Path

# 配置
NOTES_DIR = Path(__file__).parent.parent / "zephyr-rtos"
PROJECT_ROOT = Path(__file__).parent.parent  # 用于图片路径解析
OUTPUT_FILE = Path(__file__).parent / "index.html"

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

    return HTML_TEMPLATE.format(
        scripts=scripts_html,
        sidebar=sidebar_html,
        build_time=__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
        doc_count=len(docs),
    )


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
</style>
</head>
<body data-theme="light">

<div class="toolbar">
  <button class="menu-toggle" onclick="toggleSidebar()">☰</button>
  <span class="toolbar-title">📘 Zephyr RTOS 学习笔记</span>
  <span class="toolbar-spacer"></span>
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
