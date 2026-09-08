---
description: 审核已有笔记:文字/事实→配图核对→修正→构建校验
argument-hint: <笔记路径或专题目录>
---

# 审核:<参数>

分两层审核,两个角色各审一层:

1. **文字与事实**:用 `note-reviewer` 按 notes-writing §5 对应 Profile 的清单审读,对照 `<专题>/reference/` 核实事实,修文字排版,跑 `python3 build_html.py --check` 到零 error。
2. **配图**:用 `diagram-reviewer` 核对笔记里的 Mermaid/图片是否和来源一致、标注与渲染是否正确;改 Mermaid、标出要重画的图。

完成后向用户汇报两层结论和关键修改。
