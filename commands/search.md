---
description: 按求职条件寻找和比较岗位，输出有来源的候选清单
argument-hint: "[职位、地点、条件] [--limit 数量] [--data-dir 路径]"
---

# /job-hunter:search

加载本插件的 [job-hunter Skill](../skills/job-hunter/SKILL.md)，执行 search 意图。

用户参数：$ARGUMENTS

读取 matching.md；按固定 Kimi WebBridge 通道和实际平台页面发现、精读、比较。只读招聘平台，不顺带投递或处理聊天。缺画像仍可按已知条件搜索，缺事实标未知。

自然语言条件、本次授权和数据路径按用户请求处理；正文中的文件路径相对实际加载的 Skill 根目录解析，不依赖当前工作目录。
