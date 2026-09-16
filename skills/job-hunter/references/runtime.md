# 生效配置、发送检查与定时恢复

脚本相对于本 Skill 目录；示例 data-dir 是独立测试方案，请替换成用户选定目录。Python 3.10+，无第三方运行依赖。

## 唯一生效来源

- `profile.md` 保存经历事实及更正出处。事实变化只改这里；政策类偏好存入 `policy.json`，不要复制到画像末尾。
- `policy.json` 保存当前执行策略：浏览器选择、城市、薪资、排除项、授权与时段。长对话和日志保留修改原因，不另做一套运行配置。
- `state.json` 保存已经发生的事实、待核对外发、会话与轮次；不反向覆盖用户策略。
- 浏览安全状态保存当前查询、列表去向、单个活动详情和平台访问限制，跨轮次继续；不能用新的运行、账号标签或临时脚本把断点清零。规则与能力边界见 [browsing-safety.md](browsing-safety.md)。
- 宿主定时提示只包含 Skill 路径、data-dir、读取当前策略与交付要求。已有旧提示或 automation-guide.md 仅作历史交接；其中与用户最新要求冲突的执行条件以新 policy 为准。首次升级时主动合并已确认的旧规则，不丢弃仍有效的授权。

读取当前配置：
```sh
python scripts/store.py --data-dir ./demo-data effective-config
```

`profileFingerprint` 是本地 `profile.md` 原始字节的 SHA-256 摘要，`policyFingerprint` 对应当前 policy。查询、候选详情审核和 outbox 绑定所用的资料与策略版本；begin 和 check-action 重新比较资料版本，发生变化时拒绝继续按旧审核提交。这个摘要只能证明本地文件版本，不能证明资料内容正确，也不是 Boss 在线简历的摘要。

用户更改线上年限或意向岗位后，先在正常可访问页面核对当前显示信息及用户确认的事实，再分别同步到本地 profile 与 policy，保存来源；网页变化不会自动更新本地摘要。资料尚未同步时保留待核对项，不能假定旧画像已切成新测试配置；仅有已加载页面时只检查这些现有内容，不为补字段主动扩大线上读取。

用户的新事实和条件立即生效。切换资料或策略前先收尾旧批次：未发送候选本地暂挂，已开始外发只核对原记录；不继续按旧资料发送。随后 start-query 归档旧配置下的浏览决定，按新资料和策略重新筛选；成功、unknown、pending 的 outbox 与去重历史继续保留。测试配置的改变不改变既有外发授权与平台限制。

修改前取得运行锁，保留未改字段，准备完整 JSON。用刚读取的 `policyFingerprint` 进行比较后更新；并发变化会拒绝覆盖并要求重新读取。授权来源必须来自用户，不是脚本自行授予。
```sh
python scripts/store.py --data-dir ./demo-data set-policy --token TOKEN --file updated-policy.json --expected-fingerprint FINGERPRINT --evidence "用户在当前对话明确修改的条件"
```

旧配置存 logs/policy-<指纹>.json，仅供恢复与审计。不要把这些个人配置备份打进插件包。

## 浏览操作的正式入口

网页通道固定为 Kimi WebBridge，不能切到 `mcp__cua_repl` / `cua.*`。每次压缩、新轮次或中断恢复，在任何浏览器调用前重读 SKILL、drivers、当前 policy.browser 和 Kimi 技能，然后执行：
```sh
python scripts/browser_actions.py --data-dir ./demo-data --operation resume-context
```
该命令只读本地，不需要 token/session，不访问浏览器；返回 provider、禁止备用通道、策略指纹、已保存 session、账号上下文、限制和未完成步骤。已有策略中的 preferred=kimi-webbridge 保持兼容；新方案保存 required=kimi-webbridge、preferred=kimi-webbridge、allowFallback=false。当前入口在实际传输前拒绝其他 provider 或启用 fallback 的配置。

先在本地检查状态，不为诊断主动访问招聘网站：
```sh
python scripts/browser_actions.py --data-dir ./demo-data --platform boss --operation status
```

取得运行锁后，使用已归属的实际 session。下面仅观察当前已经加载的 DOM，不导航、不滚动、不导入候选，也不证明搜索筛选已经设置：
```sh
python scripts/browser_actions.py --data-dir ./demo-data --token TOKEN --platform boss --session SESSION --operation inspect
```

正式页面步骤通过 `browser_actions.py --data-dir DATA_DIR --token TOKEN --platform boss --session SESSION --operation OP --file input.json` 执行。输入文件为 UTF-8 JSON，只包含本次步骤的数据；一次命令对应一个语义步骤，不提供任意 JavaScript、选择器数组或多岗位循环。

`start-query` 输入示例，值须与本人当前 policy 和平台可见选项一致：
```json
{
  "city": "杭州",
  "keyword": "Python后端",
  "experience": ["应届生", "1年以内", "1-3年"],
  "salary": ["10-20K"],
  "accountLabel": "本人求职账号"
}
```

此步骤只保存意图；还需通过 `control` 设置实际筛选并 `inspect` 读回，不能把示例当作已完成网页操作。

`search.queries` 是非空的允许关键词列表，start-query 按原文精确校验；相关词、大小写或空格变体不自行加入。`targets.keywords` 仅保留画像检索兼容用途，不是扩大查询的授权。用户固定经验或薪资选项时，在对应 experienceFilter / salaryFilter 设置 enabled、allowedLabels、selectedLabels；selectedLabels 是本次必须完整选择的集合，入口拒绝擅自缩小或扩大。未配置 selectedLabels 的通用方案仍允许在 allowedLabels 内选择子集。个人选项不写入通用插件默认值。

| operation | 数据与前置条件 |
|---|---|
| `start-query` | 保存本轮 city、keyword、experience、可选 salary、accountLabel 意图及当前资料 / 策略指纹；旧批次收尾后，配置变化会归档旧浏览决定并重新筛选 |
| `select-account-context` | 纯本地记录用户明确区分的账号上下文，核对当前正常页面与显示名；归属旧限制、归档旧浏览上下文并保留全部 outbox，不修改 policy/profile |
| `control` | 使用当前快照中的搜索 / 筛选控件 ID 与 value，配置期间单步操作；悬停菜单可指定 `interaction: "hover"`，仅移动到当前可见菜单中心，之后重新观察选项 |
| `capture-list` | 验证实际账号及页面选中条件，固定当前自然加载的新卡片批次 |
| `screen` | 一个 key、shortlisted / skipped / deferred 决定与 evidence；依据当前列表粗筛 |
| `open-detail` | 一个已经 shortlisted 的 key，且没有其他活动详情 |
| `defer-detail` | 一个 key 与 evidence，仅暂挂中断的 open-detail；必须先有该步骤之后、本轮新的被动 inspect 证据，不重新点击 |
| `review-detail` | 当前 key、apply / skipped / deferred 决定、evidence 和 eligibilityPassed；依据实际 JD |
| `submit` | 当前 apply 岗位的 request，沿用 store.begin 动作 JSON；一次提交一次核验，不循环发送 |
| `reconcile` | 只核对当前已加载页面与原 outbox 动作，不因未知而重发 |
| `dismiss-receipt` | 已有回执时单击当前确认层的“留在此页” |
| `scroll` | 当前批次每项已有去向，详情及外发已收尾；正常滚动一次后再 capture-list 核对新增 ID |
| `finish-list-read` | 已处理批次滚动到可见尾部，后续观察无新增、无加载状态时，用 evidence 结束此次读取；纯本地收尾，不宣称结果已穷尽 |
| `clear-access-block` | 本地解除：最新 inspect 为正常页面，仍是原查询绑定的账号 / session，填写 evidence，且平台给出的 retryNotBefore 已过；本操作不访问网页 |

首版浏览器适配支持 Boss 已识别页面上的搜索、筛选、单个详情和“立即沟通”。`submit` 不泛指普通回复、附件上传或任意表单提交；其它网站、新 UI、任意直链或尚未适配的网页操作返回 unsupported 并保存具体缺口，不直接调用裸传输。既有 store / audit 脚本仍可离线准备、检查和核对记录，这不证明其对应的网页动作已接入安全入口。

当前结构化查询校验覆盖 city、keyword、experience、已配置的 salary 及账号显示名；policy 中其他硬条件仍须根据当前可见平台筛选设置并保存证据，脚本不自动理解任意策略文本。`search.salaryFilter` 启用时，`salary` 必须是其允许的 Boss 标签，并在页面读回中完全匹配；薪资标签是搜索范围，不代替对职位薪资口径的详情判断。`accountLabel` 来自页面显示名，与 session 一起提供当前身份线索；当前适配器没有验证稳定唯一账号 ID，不能把同名视为同一账号，也不能用它证明线上资料版本一致。列表粗筛和 JD 判断分别记录。`eligibilityPassed` 依据完整JD、当前用户允许的投递范围与身份/职责判断，不把已允许年限的个人工龄差异重新当成否决条件。脚本不替 Agent 判断自然语言要求。平台只支持单选且用户没有固定 selectedLabels 时可在 allowedLabels 内分次搜索；固定集合无法在平台表达时记录具体差异，不擅自删项。

用户明确选择独立新账号时，先 `inspect` 记录已加载正常页面，再调用本地 `select-account-context`，文件结构如下。`contextId` 是本地明确上下文 ID，不冒充网站 ID；旧限制未绑定时须提供原上下文和原显示名。已有上下文返回时沿用原 ID，不要求重复声明新账号；改浏览器 session 仍须新观察和显式选择。已核验全局范围限制不能在此步骤归为单账号。

```json
{
  "contextId": "boss-test-account",
  "accountLabel": "当前页面显示名",
  "userDeclaredIndependent": true,
  "originalContextId": "boss-original-account",
  "originalAccountLabel": "原账号显示名",
  "evidence": "本次用户明确区分账号的原话及当前正常页面依据"
}
```

这一步不改政策，后续 query/accountLabel 仍须匹配当前显式配置。Engine 自动给新 action 注入 `accountContextId`；直接使用 store 的调用方须显式填写它。`begin` 和 `check-action` 均校验当前上下文，未知动作与成功动作保持原有去重。已加载页面可通过所选通道只读构造真实 DOM observation 后调用 `Safety.observe(page)`；实际动作仍按 `preflight`、`reserve`、单次页面操作、`observe` 顺序，不能把无真实来源的 JSON 当页面证据。

`inspect` 是被动观察；`capture-list` 才把当前已加载列表登记为受控批次。页面操作量、卡片数量、详情数量和成功外发分别统计，不能把纯 DOM 快照次数写成网络请求次数。未识别的新 UI、缺失页面证据或入口不支持的操作应保存具体缺口，不能直接改用 webbridge_client.py、curl 或循环 evaluate 完成。

脚本状态和浏览器页面可能因用户操作或断连发生偏离。恢复时先读本地限制与断点，再被动观察当前页面；未能证明原动作结果时保留 unknown，不清除原记录，也不从头扩大读取。平台恢复时间未到时，不为更新日报、登录检查或心跳发起新页面请求。首次版本不主动刷新试探解禁；原账号已正常恢复的页面可以 inspect 取证，再执行本地 clear-access-block。用户明确指定的独立新账号在当前正常页面核对后，可通过 select-account-context 记录适用范围并按正常流程测试；这不解除旧账号限制。详见浏览安全策略。

恢复后必须重新 inspect 当前已加载页面：旧观察的 runTokenHash 不能替代本轮证据，这不要求重新打开或抓取详情。若 open-detail 中断且新观察仍不能确认详情已打开，可以用 `defer-detail` 暂挂当前 key 并保存 evidence；它只结束这个读取步骤，不取消已发送消息。submit 的 pending 仍通过 `reconcile` 核对，无回执记 unknown，禁止重发。

若进程在 store.begin 成功后、浏览 submit 登记前退出，reconcile 根据原动作的 browsingContext（批次、列表证据和详情证据）关联候选并收尾，不新建外发动作，也不把“还没取得回执”推断成未发送。

## 可执行排除与一次性授权

这些是可选策略字段，不是所有求职者的默认偏好：
```json
{
  "targets": {
    "excludedCompanies": [
      {"name": "示例公司", "aliases": ["页面上的公司简称"], "reason": "用户明确排除"}
    ]
  },
  "search": {
    "excludeHeadhunterPosted": true,
    "excludedOpportunityGroups": [{"key": "example-project", "aliases": ["项目别称"]}]
  }
}
```

公司规则按名称和明确别名匹配，阻止该公司的全部后续外发；不自动模糊匹配公司集团。猎头和招聘项目规则作用于新沟通、申请及简历发送。已记录的排除身份不能被本次请求的相反描述覆盖，用户更正后先修正对应记录及依据。自然语言 `targets.exclude` 仍由 Agent 阅读，不宣称脚本能理解任意文本规则。

动作填写 `accountLabel`，匹配 policy 中目标平台的账号。平台/对象长期授权可用 `authorization.scope.platforms` 与 `targetKeys` 限定。空的 scope 列表不代表无限授权。

发送前现场观察 `targetFacts`，用于关联职位与招聘方：
```json
{
  "accountLabel": "本人求职账号",
  "targetFacts": {
    "company": "示例用人单位",
    "publisherType": "direct",
    "evidence": "页面显示公司招聘人员身份，已核对完整职位信息",
    "jobKey": "boss:stable-job-id"
  }
}
```

`publisherType` 取 direct / headhunter / unknown。启用猎头排除时，新沟通、申请、简历发送需要本次页面的 direct 证据；未知应先读页面核对，不能凭名称猜测或为了过检查填写 direct。职位已识别的 `opportunityGroup` 写入 jobs 并通过 thread.jobKeys 或 targetFacts.jobKey 关联，跨招聘人重复仍能被排除。

长期 draft / ask 不允许仅凭一段 authorizationEvidence 登记外发。已有用户一次性明确授权时，直接把它记录为 `oneShotAuthorization`，无需重复提问、无需修改长期策略：
```json
{
  "kind": "reply",
  "platform": "boss",
  "targetKey": "boss:stable-thread-id",
  "inboundId": "visible-message-id",
  "evidence": "用户明确要求发送这条回复"
}
```

该对象位于动作的 oneShotAuthorization 字段，kind/platform/targetKey/inboundId 必须与本次动作一致。它可以给一次性目标授权，不能绕过用户公司排除、账号校验或平台限额。人工接管线程的 oneShotHandover 同时需要此明确授权。

`policy.resume.sha256` 约束附件内容；相同文件名但内容变了也会拒绝。其他已授权附件需在一次性授权的 attachments 中写明绝对 path 与 sha256。发送平台已存简历时，可在 answers 填 resumeSha256 与 platformResumeEvidence，后者记录页面文件版本与已授权上传版本的对应依据；脚本不能从页面替你验证文件内容。

## 提交前复核与面试证据

begin 成功只表示登记 pending。真正点击提交前执行：
```sh
python scripts/store.py --data-dir ./demo-data check-action --token TOKEN --id ACTION_ID
```

它重查 policyFingerprint、profileFingerprint、附件内容、账号显示名、排除项、接管状态、时段和暂停项。资料版本变化后必须按新事实重新审核，不能把旧动作的指纹改成新值来继续提交。失败后不点击；确认还没提交才能 resolve failed。页面是否正确、材料是否真实、授权是否确实来自用户仍需 Agent 判断，检查通过不是平台回执。

线程 stage=interview 必须含 interviewInvite.inboundId/evidence。interviewConfirmed=true 还须有 interview.startAt（带时区）、mode、confirmationEvidence。面试时间可用性与双方是否确实同意由 Agent 按用户策略核对，不能仅为补字段而把询问约面记作邀请。

## 每轮定时记录

`run_ledger.py` 只记录运行，不注册调度器、不操作浏览器。schedule.windows 沿用 search-apply/start、reply-check/start/end/intervalMinutes、daily-report/time。启用时记录 schedule.startDate；catchUpDays 默认7，可设1–30。当天迟到日报和时间窗内最近一个回复轮次会列为待办；旧回复轮次合并，避免醒来后连跑几十次。跨午夜回复窗口拆为两个窗口。

```sh
python scripts/run_ledger.py --data-dir ./demo-data due
python scripts/run_ledger.py --data-dir ./demo-data start --token TOKEN --mode reply-check --date 2026-09-08 --slot 15:00
python scripts/run_ledger.py --data-dir ./demo-data finish --token TOKEN --key run:reply-check:2026-09-08:15:00 --status completed --evidence "已核对变更会话与未读列表"
```

先在本地读取浏览安全限制，再获取 store 运行锁并 start。平台访问限制未到恢复条件时，不打开浏览器试探，以当前本地证据记录本轮未访问与断点。恢复旧轮次会返回待核对动作 ID；访问受限时只用已加载页面与本地材料核对，不能重发 unknown。仍按 policy.search.batchBeforeReply 优先处理未满的投递批次；这个数量不是详情读取批次。due 只列待办，不替 Agent 选择执行顺序。日报优先补交，不因投递批次未满而丢失，未同步内容注明时间和范围。

日报 finish completed 需要 `--artifact logs/<日期>-daily-summary.md`，文件必须存在于 data-dir。输出报告给用户之后，下一次可记录宿主实际回执：
```sh
python scripts/run_ledger.py --data-dir ./demo-data delivered --token TOKEN --key RUN_KEY --receipt "宿主确认交付的消息或结果标识"
```

completed 表示生成，delivered 表示已交付；不可提前填回执。宿主只能在最终回答后交付时，下一轮先检查上一轮宿主结果并补记 delivered，再判断是否需重新交付。拿不到回执记为交付待核对，不能盲目重复推送。配置 startDate 后，即使上日完全没有触发，也能在 catchUpDays 范围内发现缺失日报；补报只统计该日期的数据并标明缺口。

运行锁不按时间抢占；中断后先核对原执行者已停止及 pending/unknown，再按原 token 释放。doctor 只提示 runBusy，不自动清锁。

## 诊断与升级

```sh
python scripts/doctor.py
python scripts/doctor.py --data-dir ./demo-data
python scripts/doctor.py --compare-skill /absolute/path/to/installed/job-hunter
python scripts/store.py --data-dir ./demo-data report --date 2026-09-08
```

doctor 仅检查本地结构、Python、配置、锁与待核对数量，不输出账户名、正文、文件内容或运行 token。compare-skill 用文件摘要判断副本是否一致。可选 --codex 指定原生可执行文件，只运行 plugin --help 检查 add 能力；不修改安装。

升级保留 v2 状态，无需重新 init 或清空历史。新外发按上述结构记录账号/一次性授权/目标事实及资料版本；旧 pending 缺少 policyFingerprint 或 profileFingerprint 时只核对已有回执，不直接继续提交，也不补一个当前指纹冒充原审核版本。旧线程只在修改面试字段时需要补齐对应证据。已有访问限制和恢复时间须保留，不能因为新增浏览安全模块就当作未受限；旧临时脚本的批量详情、预加载或裸传输不是兼容入口。

## BOSS 默认招呼的提交范围

0.4.0 的 `browser_actions.py submit` 仅支持 `contentMode: "platform-default"`。请求必须按 store 的规则记录平台默认发送方式与明确授权；未知的默认正文保持 `content: null`，不能编造。该入口没有自定义招呼编辑器适配，`text` 模式或省略模式会在浏览器调用前拒绝。普通回复和附件同样不在当前网页适配范围内。

回执核对使用原动作、账号上下文、岗位身份和提交前页面证据。平台默认招呼在发送后观察到的正文另存为 `observedContent`。历史 `text` 动作不能靠页面全文中出现相同文字就确认发送；当前适配缺少文本气泡级核验时保留 unknown，等有匹配回执再处理，不能重发。
