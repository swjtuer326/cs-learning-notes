# CLAUDE.md — 学习笔记写作规范(已迁移至 skill)

本仓库学习笔记的写作规范已迁移至项目 skill **`notes-writing`**,位于 `.claude/skills/notes-writing/`。写、改、审任何 `.md` 学习笔记时自动触发;也可用 `/notes-writing` 手动调用。

规范刻意简短:只保留真正减少读者困惑的硬约束,其余靠写作者判断。区分**工程/系统/源码类**与**科普/综述类**两类文档,行文有别——写前先定位本篇属于哪类。详见 skill 的 `SKILL.md`(通用底座 + A/B 两个 profile + 按 profile 审核清单)与 `references/conventions.md`(排版查表)。

> 既有笔记中以散文形式引用的 `CLAUDE.md §1.8` / `§1.8.3`(三层背景、跨实现/跨架构对比)现对应 **A 类(工程/系统/源码类)补强**中的"概述建立上下文"。

## 写作流水线(按阶段分流 subagent)

新专题/审核走 `.claude/commands/` 与 `.claude/agents/` 预置的流水线,不用每次手写流程:

- `/new-note <专题名>` 新建专题:`note-planner`(规划,opus)→ `figure-reader`(视觉读图,glm-5.3-flash)→ `note-writer`(撰写,固定 DeepSeek-V4-Pro)→ `diagram-builder`(抽论文原图 + 补图,固定 glm-5.3-flash)→ `note-reviewer`(文字/事实审核+构建,opus)→ `diagram-reviewer`(配图核对,glm-5.3-flash)。
- `/review-note <路径>` 审核已有笔记。

各阶段混用不同模型 subagent 完成重活,主线程保持轻量;入口命令每阶段向用户简报、可介入。agent/命令细节见各文件自带说明。
