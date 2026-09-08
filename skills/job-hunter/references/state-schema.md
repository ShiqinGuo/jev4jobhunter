# 本地状态与外发记录

使用 Python 3.10+ 标准库。脚本路径相对 Skill 根目录；在插件模式和独立 Skill 模式下都是 scripts/store.py、scripts/audit_gate.py。不依赖根目录的 PowerShell 包装。

## 常用命令

以下示例在 Skill 根目录执行，使用隔离的 ./demo-data。实际运行统一换成本方案的绝对路径。中文正文写 UTF-8 JSON / 文本文件，不拼进 shell 命令。
```sh
python scripts/store.py --data-dir ./demo-data init
python scripts/store.py --data-dir ./demo-data check-policy
python scripts/store.py --data-dir ./demo-data lock acquire
python scripts/store.py --data-dir ./demo-data show
python scripts/store.py --data-dir ./demo-data report
python scripts/audit_gate.py --text-file message.txt --max-len 800
```

从 lock acquire 返回值取 token，不自行生成。下列 TOKEN 和 ACTION_ID 是上一步实际输出值的占位说明：
```sh
python scripts/store.py --data-dir ./demo-data update --token TOKEN --section jobs --key boss:job123 --file candidate.json
python scripts/store.py --data-dir ./demo-data begin --token TOKEN --file action.json
python scripts/store.py --data-dir ./demo-data resolve --token TOKEN --id ACTION_ID --status succeeded --evidence "当前目标会话出现匹配的新气泡及送达标志"
python scripts/store.py --data-dir ./demo-data lock release --token TOKEN
```

store 命令成功退出 0，失败退出 1 并输出 error JSON；begin 失败后不得操作页面。audit_gate 通过退出 0、内容问题退出 1、参数 / 文件问题退出 3。它不判断事实、承诺与授权，也不要求中文占比；合理的短回复或不同会话相同内容可通过。URL 需在 policy.allowedLinks 中；独立审核可用 --allow-link 传同一已授权链接。

新版本的 begin / check-action 校验结构化授权、账号、排除条件与简历版本；下面 action 示例还需按当前策略补齐 accountLabel / targetFacts 或 oneShotAuthorization。生效配置、升级兼容与定时轮次字段统一见 [runtime.md](runtime.md)。

## 互斥与写入

state version 2 包含 startedAt、runLock、jobs、threads、actions、blocks、scheduler、legacy；均使用带时区时间戳。脚本使用操作系统文件锁串行化短事务，再通过同目录临时文件 + 原子替换保存 JSON。不要把状态目录放在不保证本机文件锁语义的网络共享上，也不要多机同步后并发运行。

runLock.token 跨浏览器操作维持运行归属；每次修改刷新 heartbeatAt，长时间观察可 lock renew。没有“10 分钟后自动抢锁”。旧进程崩溃时先确认原运行结束、检查 pending / unknown，才用原 token release；活跃运行的锁不可释放。操作系统的 .store.lock 文件保留，不能删除或作为过期锁处理。

update 只接受 jobs / threads / blocks / scheduler 的一个键，以浅合并保留未修改字段。嵌套值整体替换。actions、runLock、legacy 等受保护字段不能通过 update 改写，外发必须用 begin / resolve。可直接只读 profile、日志和 show / report；本地文件修改仍需本轮运行锁。

## 候选与会话

jobs 的 key 为 platform:稳定职位ID，值建议含 title、company、url、status、salary 原文、jdSummary、匹配证据、未知项、evaluatedAt、profileFingerprint、policyFingerprint。候选 status 为 candidate / shortlisted / skipped；实际投递结果以 actions 为准。相同职位跨平台的稳定别名用 sameOpportunityAs 数组，双向登记。

threads 的 key 用稳定会话 ID；若只有低可信本地组合标识，记录 identityConfidence，发消息前核对。值含 company、hrName、jobKeys、stage、humanTakenOver、needsReview、lastInboundId、history。stage 可为 talking / pendingHuman / interview / closed；保留足够的作者和消息时间，不仅存最后一行预览。

人接管：我方新消息先查 actions 的内容、目标、时间及旧 audit。证据确认为用户发的才设 humanTakenOver；证据不足设 needsReview。resume 唯一定位并刷新上下文后清除对应标记，不改变原授权范围。

## action.json 示例

回复动作示例；context 与 authorizationEvidence 必须来自实际观察和当前授权，不能照抄示例作为授权。
```json
{
  "kind": "reply",
  "platform": "boss",
  "targetKey": "boss:thread123",
  "inboundId": "message-id-or-stable-turn-fingerprint",
  "context": "公司、HR、岗位及其最新问题的可核对摘要",
  "authorizationEvidence": "本次用户明确要求向该 HR 发送已审阅回复",
  "content": "您好，我主要负责订单服务的后端开发，使用 Python 和 PostgreSQL。",
  "maxLength": 800,
  "attachments": [],
  "answers": {}
}
```

- kind：greet（打招呼）、application（网站申请）、reply（普通消息）、share_resume（单独发简历）、commitment（确认时间 / 薪资等具体承诺）。动作分类不授予权限。
- greet / application 的 targetKey 是职位；reply / share_resume / commitment 是会话，后者必须带 inboundId。主动跟进无来信时，用已授权的跟进事件生成稳定 ID；同一事件跨运行不能重新生成随机 ID。
- attachments 为真实附件的绝对路径数组，begin 记录 SHA-256。answers 为申请表字段到完整答案的映射。上传平台现有简历时，在 answers 中明确 resumeVersion / 可见文件信息及用户授权。
- 用户明确要求对已由本人接管的线程执行这一次动作时，可设置 oneShotHandover: true，并在 authorizationEvidence 记录本次具体指令。它仅放行本次动作，不清除 humanTakenOver；长期自动授权不能触发它。needsReview 仍需先核对上下文再解除。
- maxLength 必须来自页面已知限制；未知时脚本使用 800 作为本地草稿检查值，不代表平台上限。表单各字段的长度和必填检查由 Agent 按页面执行。
- begin 登记并执行可机器判断的策略检查，不调用浏览器；脚本无法证明授权来源与页面事实真实，Agent 仍需核对。

## 去重、额度与回执

begin 用动作种类、平台、targetKey、来信标识构造去重键，其中 reply / commitment 共用消息类别；改正文或改回复类别不能绕过同一来信的防重。pending / unknown / succeeded 都占用该键。failed 只在有明确“没有提交”的证据时使用，此后才允许新尝试。成功状态不可回退成失败；未知可在核对后更新为成功或失败。

同一天各动作的 pending / unknown / succeeded 共同计入当日数量；failed 不占。policy.dailyLimits 为非负整数时检查本地额度，显式为 null 时不加本地数量上限，仍执行去重、平台暂停、接管和回执检查。--limit 能为 null 临时增加限制，或进一步收紧已有整数上限，不能提高额度。用户的“本轮最多 N 个”还须在本轮计数控制，不把它误当完整日额度。按 policy 时区确定 date；跨日未知记录仍阻止同一动作重试。旧 daily 的 greet / reply 数量也计入，迁移不能重置当天额度。

每次 resolve 同时更新 state 内 events，并尽力追加 logs/audit-日期.jsonl。state 是权威记录；日志写入失败会输出 warning，不能因此重发。成功需要可见的目标、正文 / 申请编号和时间证据。工具只保证先记录、限制重试，不保证网站端严格恰好一次。

## 暂停、报告与保留

blocks 以平台 ID 为键，或 * 表示所有平台，记录 active、kinds、reason、recovery。解除时经现场核验再 update {"active": false}；不按日期自动解除登录 / 验证暂停。平台额度恢复条件以提示为准，由 Agent 到时核对。

scheduler 以宿主任务 ID 为键，保存任务名称、实际时区、执行窗口、状态、最后确认时间；本地记录不是任务存在的证明。

report 只读并返回各动作的累计分状态统计、未核对 ID、暂停与是否包含旧版记录。按日期报告从 actions.date 与 logs 对应日期汇总；legacy 历史独列，不冒充 v2 成功回执。读取报告不再发通知、建任务或打开浏览器。

不要定时删除已联系记录。大 JD / 历史全文可以归档到 logs，但保留职位 / 会话 key、已成功与待核对动作、来信 ID 和必要时间证据。策略变化只重评候选，不抹掉外发历史。备份数据目录即可换机，勿将个人资料提交到插件仓库。
