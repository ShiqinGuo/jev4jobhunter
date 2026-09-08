# Job Hunter

根据个人简历搜索职位、准备或执行已授权的投递和回复，并追踪面试进度。提供 Codex 插件包装及可独立安装的 Skill。

当前版本 **0.3.0**，以 [MIT](LICENSE) 许可证开源。提供授权校验、申请防重、运行恢复和日报补报。本版的自动化逻辑经过隔离测试；网站操作仍需在实际环境验证。

## 开始使用

运行脚本需要 Python 3.10+，全部为标准库。网站操作需要实际可用的浏览器通道；默认优先已安装的 Kimi WebBridge，并沿用用户验证过的站点通道。ADB 是可选登录恢复能力。

在新对话指定完整 Skill 路径即可试用：

> 读取这个目录下 skills/job-hunter/SKILL.md，根据我的简历在我指定的城市找5个岗位，先给候选和理由。个人数据放在我指定的独立目录。

其他常用请求：

- “把这家公司和这些别名加入排除名单。”
- “只准备回复草稿；这三个岗位我明确授权申请。”
- “使用我已确认的简历，回复这个招聘方的问题。”
- “检查安装副本、运行锁、未核对发送和漏掉的日报。”

默认生成候选和草稿。一次性明确授权可以执行，不必修改长期模式或反复确认。能力不足时说明实际缺口。

## 安装与升级

插件入口为 `.codex-plugin/plugin.json`。核心 Skill 必须连同 references、scripts、agents 一起安装，不能只复制 SKILL.md。[OpenAI 插件打包文档](https://developers.openai.com/plugins/build/plugins)说明插件结构；[Skill 文档](https://learn.chatgpt.com/docs/build-skills)说明技能机制。

独立 Skill：把 `skills/job-hunter` 整个目录放入宿主支持的 Skill 发现目录，在新对话加载。已有同名副本时先备份并确认目标位置。

Codex 插件：使用提供 `plugin add` 的新版 Codex CLI 执行：

```sh
codex plugin marketplace add ShiqinGuo/job-hunter
codex plugin add job-hunter@job-hunter
```

仓库中的 `.agents/plugins/marketplace.json` 指向根目录插件。CLI 只有 marketplace 子命令时，在客户端插件目录中安装，或使用客户端附带的新版本可执行文件；PATH 中的旧 npm 版本可能不同。升级时先执行 `codex plugin marketplace upgrade job-hunter`，再执行上面的 add 命令。插件更新后在新对话加载。

Claude Code：

```sh
claude plugin marketplace add ShiqinGuo/job-hunter
claude plugin install job-hunter@job-hunter
```

已有安装使用 `claude plugin marketplace update job-hunter` 和 `claude plugin update job-hunter@job-hunter`，然后重启会话。

比较源码与安装副本：
```sh
python -B skills/job-hunter/scripts/doctor.py --compare-skill /absolute/path/to/installed/job-hunter
```

different 表示文件不一致，不能宣称已同步。可选 --codex 指定原生可执行文件，只查询 plugin --help；Windows PowerShell 包装入口可直接手工运行帮助命令。

## 数据和执行

data-dir 优先级：明确 --data-dir → JOB_HUNTER_HOME → ~/.job-hunter。同一账号共享一个目录，不把个人数据放入插件源码。

- profile.md：当前经历事实和更正来源。
- policy.json：唯一执行策略，含授权、城市、排除项、文件版本、浏览器与计划。
- state.json：候选、会话、发送回执、平台暂停和运行记录。
- logs/、drafts/：个人历史报告、配置备份与草稿。

```sh
python -B skills/job-hunter/scripts/store.py --data-dir ./demo-data init
python -B skills/job-hunter/scripts/store.py --data-dir ./demo-data effective-config
python -B skills/job-hunter/scripts/doctor.py --data-dir ./demo-data
```

已有方案用带指纹比较的 set-policy 更新，保留未修改字段。升级继续使用 v2 状态，不清空历史。新动作字段和一次性授权示例见 [运行指南](skills/job-hunter/references/runtime.md)。

## 定时任务

插件记录运行，宿主负责触发。让 Agent 按用户时间配置真实宿主任务并核验回执；长期提示引用当前 policy，不复制整套个人条件。

每轮取得锁，查询 run_ledger.py due 并记录 start / finish。脚本区分日报生成与交付；设置 schedule.startDate 后，可发现限定天数内完全漏跑的日报。过期回复时段合并为当前一个检查，旧未知发送继续防重。按批次投递并在平台限额后停止相应动作。

```sh
python -B skills/job-hunter/scripts/run_ledger.py --data-dir ./demo-data due
python -B skills/job-hunter/scripts/store.py --data-dir ./demo-data report --date 2026-09-08
```

主机睡眠、浏览器断连、短信读取和页面改版是否可恢复，需要当前环境实测。诊断不会保证未来无人值守成功；恢复和交付核验见运行指南。

## 验证与打包

```sh
python -B -m unittest discover -s tests -v
python -B scripts/package.py --output ../job-hunter-0.3.0.zip
```

测试使用临时目录与模拟传输，不触碰求职账号。CI 配置覆盖 Windows / Linux、Python 3.10 / 3.12；本机验证不代表 CI 已运行。打包按明确范围收集源码，排除个人状态、缓存和临时目录，附文件校验清单。

Boss 的既有现场经验见 [平台参考](skills/job-hunter/references/platform-boss.md)；其他站点走通用只读发现与逐项验证，本版没有逐站点验收。

问题报告优先附 doctor 输出和脱敏复现步骤，不附个人状态、简历、短信和聊天。贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)，变更见 [CHANGELOG.md](CHANGELOG.md)，剩余工作见 [ROADMAP.md](ROADMAP.md)。
