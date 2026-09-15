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

state version 2 包含 startedAt、runLock、jobs、threads、actions、blocks、scheduler、legacy；浏览安全模块按需增加 browsing，不清空原状态。时间均使用带时区时间戳。脚本使用操作系统文件锁串行化短事务，再通过同目录临时文件 + 原子替换保存 JSON。不要把状态目录放在不保证本机文件锁语义的网络共享上，也不要多机同步后并发运行。

runLock.token 跨浏览器操作维持运行归属；每次修改刷新 heartbeatAt，长时间观察可 lock renew。没有“10 分钟后自动抢锁”。旧进程崩溃时先确认原运行结束、检查 pending / unknown，才用原 token release；活跃运行的锁不可释放。操作系统的 .store.lock 文件保留，不能删除或作为过期锁处理。

update 只接受 jobs / threads / blocks / scheduler 的一个键，以浅合并保留未修改字段。嵌套值整体替换。actions、runLock、legacy 等受保护字段不能通过 update 改写，外发必须用 begin / resolve。可直接只读 profile、日志和 show / report；本地文件修改仍需本轮运行锁。

## 浏览断点

`state.browsing[platform]` 由 [browsing_safety.py](../scripts/browsing_safety.py) 在正式单步入口内维护，沿用同一个运行锁和原子写入；不要通过手写 JSON 或新建 data-dir 重置步骤。它不是另一套投递成功统计，外发结果仍以 actions 为准。

| 字段 | 含义 |
|---|---|
| version | 浏览状态自身版本，不替换 state version 2 |
| query / accountLabel / session / policyFingerprint / profileFingerprint | 当前城市、关键词、经验标签意图、账号显示名、通道会话，以及查询绑定的策略与本地资料版本 |
| batch | 当前自然加载批次的 ID、岗位 keys、查询与可见筛选证据；不固定岗位数量 |
| candidates | 当前及已见岗位的卡片、列表决定、详情、JD 判断证据、审核所用资料 / 策略版本和必要 actionId |
| seen | 已导入批次的稳定岗位 ID，重复观察或页面自发预取不会自动扩充当前批次 |
| activeKey | 当前唯一正在处理的详情岗位；结束判断和必要外发后才清除 |
| pending | 已登记而尚待页面证据核对的单步浏览动作，beforeObservation 指向该步骤登记前的观察；不是 outbox pending 的替代物 |
| phase | idle / configuring / batch / detail-opening / detail / await-list 等当前步骤 |
| lastObservation / events | 已加载页面观察证据、时间、分类、session、runTokenHash，以及已准备的页面步骤记录；runTokenHash 用于拒绝把旧运行观察当成本轮证据 |

卡片从 unreviewed 经 screen 进入 shortlisted、skipped 或 deferred；只有 shortlisted 能打开详情。详情 apply 决定要求明确的资格判断证据，随后与 outbox 关联；skipped / deferred 或实际外发收尾后才结束此候选。批次中仍有未判断、已入选未处理、活动详情或待核对浏览步骤时，不能滚动或更换查询。

profileFingerprint 是本地 profile.md 字节的 SHA-256，不包含网页个人资料。查询、详情审核和 outbox 保留各自使用的资料版本；资料或策略变化后先暂挂未发旧候选、核对已开始外发，再由 start-query 归档旧浏览决定并按新配置重筛。已见列表及旧 skipped 不能继续作为新配置的排除结论；actions 中的成功、unknown、pending 仍参与去重，不随浏览归档清除。

accountLabel 仅为页面显示名证据；当前实现未验证稳定唯一账号 ID。线上年限、意向岗位等变化不会自动同步到本地 profileFingerprint，须按正常可访问页面及用户确认信息更新对应本地事实和策略。同步前记录缺口，不能仅凭显示名相同复用旧资料审核。

页面证据保存在 `logs/browsing/`，包含观察时间、当前 session、页面分类及允许读取的渲染内容。被动 inspect 只更新观察证据，不导入新候选、不清除已有访问限制；capture-list 才按当前筛选证据固定批次。页面证据属于个人数据，不打包进插件，也不声称纯 DOM 观察产生了新的网络请求。

运行恢复先读浏览状态与平台限制，再重新 inspect 当前已加载页面；检查 lastObservation.runTokenHash 与当前运行一致，不默认重新打开详情。中断的 open-detail 可在取得不同于 pending.beforeObservation 的本轮新观察后，通过 defer-detail 暂挂为 deferred 并保留证据；这个本地步骤不重新点击，不适用于 submit pending。

submit pending 通过原 actionId 的 reconcile 核对；无匹配回执保留 unknown，不能因新运行就重发。begin 已成功而浏览 submit 尚未登记的中断，由原 action.browsingContext 中的 batchId、listEvidence、detailEvidence 与当前候选匹配后收尾；不清掉 outbox，也不另建动作。用户手动改变页面时，先核对身份、筛选与当前步骤是否相符，不用旧快照继续提交。具体 CLI 与当前支持范围见 [runtime.md](runtime.md)。

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
- contentMode 缺省或为 text 时沿用非空消息正文及文本审核；platform-default 仅支持已授权的 Boss greet，拒绝非空 content、预填 observedContent、附件及 answers。begin 将 content 与 observedContent 记录为 null，表示平台决定的招呼文本尚未观察；发送成功不意味着文本已知。此模式不扩大授权范围、不改变去重。
- browser_actions 提交时自行记录 browsingContext.submitObservation，指向本次点击前的原始页面；默认招呼的核验要求该基线无成功提示、账号和岗位匹配，以及新的同账号同岗位明确成功提示。action.evidence 指向保存了原始回执的观察文件；没有气泡时保留 observedContent:null。调用方不能用任意历史记录代替该基线。
- attachments 为真实附件的绝对路径数组，begin 记录 SHA-256。answers 为申请表字段到完整答案的映射。上传平台现有简历时，在 answers 中明确 resumeVersion / 可见文件信息及用户授权。
- 用户明确要求对已由本人接管的线程执行这一次动作时，可设置 oneShotHandover: true，并在 authorizationEvidence 记录本次具体指令。它仅放行本次动作，不清除 humanTakenOver；长期自动授权不能触发它。needsReview 仍需先核对上下文再解除。
- maxLength 必须来自页面已知限制；未知时脚本使用 800 作为本地草稿检查值，不代表平台上限。表单各字段的长度和必填检查由 Agent 按页面执行。
- begin 登记并执行可机器判断的策略及资料版本检查，把审核使用的 profileFingerprint 与 policyFingerprint 绑定 outbox；check-action 提交前重新比较。资料变化后拒绝沿用旧审核，不直接替换动作指纹。旧 pending 缺少 profileFingerprint 时仅允许核对已有结果，不能继续发送。
- profileFingerprint 只反映本地文件字节，不能验证线上简历已同步，也不能证明授权来源与页面事实真实；Agent 仍需核对。

## 去重、额度与回执

begin 用动作种类、平台、targetKey、来信标识构造去重键，其中 reply / commitment 共用消息类别；改正文或改回复类别不能绕过同一来信的防重。pending / unknown / succeeded 都占用该键。failed 只在有明确“没有提交”的证据时使用，此后才允许新尝试。成功状态不可回退成失败；未知可在核对后更新为成功或失败。

同一天各动作的 pending / unknown / succeeded 共同计入当日数量；failed 不占。policy.dailyLimits 为非负整数时检查本地额度，显式为 null 时不加本地数量上限，仍执行去重、平台暂停、接管和回执检查。--limit 能为 null 临时增加限制，或进一步收紧已有整数上限，不能提高额度。用户的“本轮最多 N 个”还须在本轮计数控制，不把它误当完整日额度。按 policy 时区确定 date；跨日未知记录仍阻止同一动作重试。旧 daily 的 greet / reply 数量也计入，迁移不能重置当天额度。

每次 resolve 同时更新 state 内 events，并尽力追加 logs/audit-日期.jsonl。state 是权威记录；日志写入失败会输出 warning，不能因此重发。成功需要可见的目标、正文 / 申请编号和时间证据。工具只保证先记录、限制重试，不保证网站端严格恰好一次。

## 暂停、报告与保留

blocks 以平台 ID 为键，或 * 表示所有平台。发送配额记录 active、kinds、reason、recovery，只限制对应发送。平台访问限制另记录 `scope: "access"` / `blocksAccess: true`、reason、evidence、firstEvidence、observedAt，以及平台明确给出的 `retryNotBefore`；它同时阻止正式入口的新导航、滚动、刷新、详情打开和外发。旧记录中明确的安全验证、访问受限等原因继续生效，不因字段尚未升级而忽略。

账号上下文位于 `accountContexts[platform]`：`activeContextId`、`contexts[id]`（本地明确 ID、accountLabel、session、用户声明与页面证据）及选择事件。显示名不是网站稳定 ID；独立账号依据用户明确区分并通过当前页面核对。`select-account-context` 会归档旧浏览状态，保留外发状态和 policy/profile。旧未绑定限制显式归属时只添加 `accountContextId`、`applicability: "observed-context"` 及归属证据，原限制 active、原始证据、恢复时间均保留。新限制记录在 `contextBlocks[platform][contextId]`。已有 `applicability: "all-contexts"` 且含 `scopeEvidence` 的共享限制，以及 `appliesToContextIds` 指定范围内的限制，仍适用于对应上下文。

限制作用范围与限制种类分开：`reportedScope: "ip"` 保留当时页面的 IP 文案，并不自行把旧观察扩张为永久全局范围。未绑定的旧记录仍生效，只有显式账号选择及证据归属能改变适用范围；新账号正常不会解除旧账号限制。新 action 绑定当前 `accountContextId`，提交前还要匹配当前上下文；不同上下文不会改变 outbox 防重键。

`retryNotBefore` 是最早可核验时间，不是自动解除时间。每轮先本地读取，未到恢复条件不进行心跳试探；不能仅手动 update 为 inactive。首版须在原账号已恢复的当前页面上 inspect，随后通过本地 `clear-access-block` 核对最新正常页面、原账号 / session、解除证据及 retryNotBefore 后清除。普通观察到正常页面不会自动清除原限制；新账号正常页面也不能替旧账号解除。平台沟通配额与访问限制分别判断，不能把“还剩 N 次”当作耗尽，也不能用尚有发送配额来覆盖访问限制。

scheduler 以宿主任务 ID 为键，保存任务名称、实际时区、执行窗口、状态、最后确认时间；本地记录不是任务存在的证明。

report 只读并返回各动作的累计分状态统计、未核对 ID、暂停与是否包含旧版记录。按日期报告从 actions.date 与 logs 对应日期汇总；legacy 历史独列，不冒充 v2 成功回执。读取报告不再发通知、建任务或打开浏览器。

不要定时删除已联系记录。大 JD / 历史全文可以归档到 logs，但保留职位 / 会话 key、已成功与待核对动作、来信 ID 和必要时间证据。策略变化只重评候选，不抹掉外发历史。备份数据目录即可换机，勿将个人资料提交到插件仓库。
