---
description: 查看求职进度、候选、草稿及待核对事项，默认只读本地
argument-hint: "[日期] [--data-dir 路径]"
---

# /job-hunter:report

加载本插件的 [job-hunter Skill](../skills/job-hunter/SKILL.md)，执行 report 意图。

用户参数：$ARGUMENTS

按 workflows.md 的 report 和 state-schema.md 汇总。区分打招呼、正式申请、回复与未知结果，标出数据时间；只读报告不重复发通知或操作浏览器。

自然语言条件、本次授权和数据路径按用户请求处理；正文中的文件路径相对实际加载的 Skill 根目录解析，不依赖当前工作目录。
