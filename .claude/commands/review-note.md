---
description: 审核已有笔记:文字/事实把关→配图核对→修正→构建校验
argument-hint: <笔记路径或专题目录>
---

# 审核:<参数>

分两步审核(两类模型各审一层):

1. **文字与事实**:用 `note-reviewer` subagent 按 notes-writing §5 对应 Profile 清单审读,对照 `<专题>/reference/` 核实事实,修文字与排版(删口癖、拆长段、枚举转表格、结论先行),跑 `python3 build_html.py --check` 修到零 error。
2. **配图核对**:用 `diagram-reviewer` subagent 核对笔记里的 Mermaid/图片——对照规范原图检查准确性、标注、渲染;修 Mermaid、报需重绘的图。

完成后向用户汇报两步结论与关键修改点。
