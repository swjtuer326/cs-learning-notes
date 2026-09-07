---
description: 新建学习专题:调研规划→逐篇撰写→配图→审核→构建校验
argument-hint: <专题名> [Profile A|B]
---

# 新建专题:<专题名>

执行本仓库的专题写作流水线(各阶段由对应模型/角色的 subagent 承担):

1. **调研规划**:用 `note-planner` subagent 对「<专题名>」做调研规划(定 Profile、备 reference/ 规范、出大纲+待核实清单)。把规划结果摘要给用户确认,用户同意后再继续。
2. **视觉读图**:用 `diagram-builder` subagent 抽论文/规范关键原图到 `images/`,再用 `figure-reader` subagent 读图产出 `_work/figure-notes.md`(精确图释)。
3. **逐篇撰写**:按规划,逐个用 `note-writer` subagent 撰写编号笔记(图驱动段落以 figure-notes 为准);每篇完成后简报。
4. **配图**:向用户确认哪些篇目还需要补图,用 `diagram-builder` subagent 绘制。
5. **审核与构建**:先用 `note-reviewer` subagent 做文字/事实把关并跑 `python3 build_html.py --check` 修到零 error;再用 `diagram-reviewer` subagent 做配图核对(有视觉,能对照规范原图)。两步都通过才算完成。

全程遵守 notes-writing skill 规范;每阶段结束给用户简报,让用户能介入。
