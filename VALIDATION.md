# 本地验证记录

日期：2026-09-08。环境：Windows，Python 3.12.7。功能测试使用临时目录中的合成资料，没有访问真实招聘账号或短信。

- `python -B -m unittest discover -s tests -v`：48项通过。包括原有26项及22项新增测试。
- Codex plugin-creator 的 validate_plugin.py：通过。
- skill-creator 的 quick_validate.py：通过。
- doctor.py 无参数只读诊断：Python与Skill结构可用，浏览器账号正确标为未验证。
- 打包测试验证同一输入生成相同文件摘要、排除个人数据、拒绝覆盖已有输出；实际压缩包逐文件校验。
- 功能提交 `0f305a4` 的 [GitHub Actions](https://github.com/ShiqinGuo/job-hunter/actions/runs/34200703713)：Windows / Linux × Python 3.10 / 3.12 四组全部通过。
- 使用独立 Codex 配置目录，从 `ShiqinGuo/job-hunter` 添加 marketplace 并安装成功；本机 Codex 和 Claude Code 安装副本的 Skill 内容均与源码一致。

重点行为：草稿策略拒绝外发；已有一次性授权不改变长期模式；账号与平台范围匹配；公司别名、猎头、招聘项目排除；授权附件hash；配置并发变更与提交前重查；邀面与确认分开；错过回复时段合并；漏跑日报发现；日报生成与交付分开；中断恢复保留未知动作；传输超时不重试提交。

未验证：新版真实浏览器提交、各招聘网站语义兼容、各宿主通知回执、无人值守登录。既有现场经验保留为平台参考，不计入这版代码的端到端验收。

安装时先确认运行结束，再按 runtime.md 迁移新动作参数，在新对话验收。个人简历、策略和申请历史不包含在本仓库或发布包中。
