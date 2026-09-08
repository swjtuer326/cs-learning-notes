---
description: 新建学习专题:调研规划→逐篇撰写→配图→审核→构建校验
argument-hint: <专题名> [Profile A|B]
---

# 新建专题:<专题名>

按下面的步骤走,各步骤由对应角色的 subagent 完成:

1. **规划**:用 `note-planner` 调研规划(定类型、备规范到 reference/、出大纲和待核实清单)。先把规划结果给用户确认,再继续。
2. **读图**:用 `diagram-builder` 抽来源关键原图到 `images/`,再用 `figure-reader` 读图,把图释写到 `_work/figure-notes.md`。
3. **撰写**:按大纲逐篇用 `note-writer` 写(涉及图的段落以 figure-notes 为准);每篇完成后简报。
4. **配图**:和用户确认哪些篇目还要补图,用 `diagram-builder` 画。
5. **审核与构建**:先用 `note-reviewer` 审核文字/事实并跑 `python3 build_html.py --check` 到零 error;再用 `diagram-reviewer` 核对配图。

全程遵循 notes-writing skill;每阶段给用户简报,可随时介入。
