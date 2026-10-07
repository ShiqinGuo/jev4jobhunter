# Android 手机端

固化入口为 `scripts/android_actions.py`。ADB 与抓取依赖在用户环境，数据在明确的 DATA 目录。只通过官方 App 的正常 UI 搜索、读取和发送；采集器只观察这些操作产生的响应。

## 配置与启动

`DATA/android.json` 必需字段：`adb`、`serial`（当前 transport）、`manufacturer`、`model`、`python`（抓取运行时）、`caDir`。可选 `serverPort`、`proxyPort`、`cityInput`。物理设备通过 `ro.serialno` 绑定，换无线 transport 或 ADB server 不重置业务状态。连接恢复先使用 android-adb 技能；当前配置是已核验可用实例，不能凭历史路径覆盖。

首次升级旧手机端数据，先执行一次 `migrate`。迁移不连接平台、不调用 Jev、不发送消息，保留 JD、决定和既有动作；缺原始 marker 的旧 unknown 保留 unknown。

中文长文本沟通使用本插件的 `NativeText.java`，通过正常可见输入框的 Accessibility `ACTION_SET_TEXT` 写入并读回。先用 `build_native_input.py --sdk SDK --jdk JDK --output SESSION_OUTPUT/native-text.jar` 构建，将绝对路径写入配置 `nativeTextJar`。构建需 Android 36 SDK、Build Tools 35 和 JDK；运行只需现有 ADB。Android 16 的独立 UIAutomator runner 需要显式加载系统 `android.test.base.jar`。助手不安装 App、不改输入法、不借剪贴板；编译产物属于运行依赖，保留在指定输出目录。

```text
python scripts/android_actions.py --data-dir DATA migrate
python scripts/android_actions.py --config DATA/android.json --data-dir DATA --output-dir SESSION_OUTPUT preview --source search --city 杭州 --query python --count 20
python scripts/android_actions.py --config DATA/android.json --data-dir DATA --output-dir SESSION_OUTPUT run --source search --city 杭州 --query python --count 20 --send --max-send 20
```

`--count` 是完整 JD 的采集目标；`--max-send` 是这次调用新增确认成功的上限。当天用户明确上限保存在 `scheduler.manualApplicationRun`，跨调用按动作事实计算；unknown 仍占保留额度。`preview` 不外发；默认复用当前批次，`--fresh` 归档旧批次并加载新批，不要求旧批清空。`--exclude-file` 可传 `{"keys": [...]}`。

搜索、城市和筛选读取最新 policy。中文搜索词优先使用已有历史或建议；没有可用输入能力时报告具体缺口。首页薪资按原生单选档位轮换、去重，整批一次 Jev。列表摘要始终不是全文。缺响应不表示空列表，一次无新响应不表示查询耗尽。

## 消息与主动沟通

`message-check` 核对账号后读取近期消息页，返回 `recentPages`、`atEnd`、我方送达状态与可能收到的消息。页数有界；无送达标识的附件或系统通知也可能进入 `possibleIncoming`，须打开相关会话阅读，不能直接算招聘方回复。

`open-conversation --target-key boss:ID` 按当前批次记录的公司及招聘方定位既有会话；`conversation` 返回正文、方向、送达标识及摘要。普通回复或本次授权的主动说明均先完整复核 policy、真实画像与 JD，再保存请求 JSON：`kind`、`platform`、`targetKey`、`targetFacts`、`inboundId`、`content`、`context`、`authorizationEvidence`、`conversationDigest`。主动说明的 `inboundId` 明确使用 `trial:日期:用途:岗位ID`，上下文注明尚无招聘方回复；不伪装成收到来信。如依赖单次授权，附同作用域的 `oneShotAuthorization`。

执行 `reply-current --request-file SESSION_OUTPUT/request.json` 可审阅草稿；明确授权后加 `--send`。助手在同一 UIAutomator 连接中写入草稿、读回最新对话；摘要改变则取消未提交动作。只有 `store.start_mobile_action` 落盘成功后才写入一次发送许可，随后点击唯一的原生发送图标。助手返回异常时仍核对原动作的送达气泡；完整 runner 日志单独保存，结束码 `-1` 本身不是失败。已尝试动作保持原 ID 与原 marker，未知结果不重新发送；未获点击许可的旧草稿可取消并创建新的已审阅尝试。

`reply-current` 仅发送文字，拒绝带 `attachments` 的请求；不能把文件元信息登记成已分享。附件、在线简历分享属于独立 UI 动作，本入口尚未实现。

## 状态与恢复

`batch.json` schema 3 保存设备与账号 binding、JD、选择范围、语义摘要和决定。布局按宽高与 App 版本独立保存到 `layouts.json`；协议能力保存在 `protocol.json`。布局变化在当前账号和搜索范围内重校准，保留 JD、决定和所有外发记录。决定只依赖完整 JD、画像和初投判断输入，发送权限与排除项仍在每次点击前复核。

换批归档到 `batches/`；`judgments.json` 是可从归档重建的决定索引。账号、完整 JD 摘要和判断上下文相同才复用，通信措辞或布局变化不重新调用 Jev。打开旧会话按当前完整记录、账号归属的动作事实及归档查找，不依赖当前批次恰好仍包含该岗位。

动作权威仍是 `state.json/actions`。batch 不保存投递结果副本，展示结果从动作记录重建。手机动作使用既有 `pending/unknown/succeeded/failed` 结果，另有 `submissionStage`：

- `prepared`：没有进入可能点击的区间，可以重新准备目标。
- `in_flight`：原动作 ID 和 marker 已持久化，点击可能发生过，只能核对。
- `unknown`：没有匹配结果，继续核对原动作。
- `succeeded/failed`：匹配送达或业务拒绝，终态不可降级。
- `legacy_unknown`：迁移时没有可靠原标记，不伪造关联、不重发。

全局 `responses/action.json` 只给采集器快照标记。恢复读取动作里的不可变 marker，滚动和打开详情覆盖全局标记也不会丢失原始发送身份。监听建立失败、状态写入失败、程序错误保留具体异常；点击后出错仍保留 in_flight。诊断和送达分别记录。

UI 证据要求账号 binding、公司、岗位、原发起时间、对应正文、我方方向和同一气泡送达状态匹配。进入聊天、输入框清空、HTTP 200 都不足以单独确认成功。不能借恢复生成新 action 重发。

## 操作

| 操作 | 用途 |
|---|---|
| migrate | 一次性迁移旧批次和手机动作 |
| inspect | 读取当前身份、布局与可见 UI |
| preview / run | 完整只读采集或已授权采集投递 |
| evaluate-batch | 整批未有有效判断的完整 JD 一次 Jev；未知调用不重试 |
| apply-batch | 默认 dry；`--send --max-send N` 复核当前 JD 并投递 |
| reconcile-batch | 从原动作读取 marker 核对结果，不点击发送 |
| message-check | 读取近期消息页，保存累计投递后的检查断点 |
| open-conversation | 按目标公司、招聘方定位既有会话并返回最新摘要 |
| conversation | 读取当前会话及摘要，供宿主结合完整 policy 审阅 |
| reply-current | `--request-file FILE` 准备回复；加 `--send` 后核对最新会话、保存动作、发送并验送达 |
| report | 从权威状态汇总当天动作，无平台访问 |
| start / stop | 排查用被动采集启停；正常流程自动收尾 |

回复 request 保存 `targetKey`、完整 `targetFacts`、`inboundId`、`conversationDigest`、`content`、`context` 与 `authorizationEvidence`。宿主按 profile、当前 JD 和完整 policy 准备事实与话术；人工接管、待复核会话由 store 拦截。一次明确授权的主动匹配说明用 `oneShotAuthorization` 精确绑定岗位、回复种类和会话摘要，不扩大成长期催发。

CLI 自动持有唯一运行锁。只在原进程完成后启动下一次；工具返回 session ID 时继续等待该进程。截图和过程报告放 `--output-dir` 指定的仓库外会话目录，运行状态与必要响应保存在 DATA。

## 协议与代理

`boss_protocol.py` 区分 HTTP 内容、负载解码和业务 envelope。批量响应先分子路由，无关路由错误不能结束当前等待；非零业务 code 是拒绝，解码失败是诊断。实际不支持的密钥或格式保存受控响应样本和必要响应头，不收集请求凭据、不猜密钥、不重放请求。UI 核对成功不表示解码问题已经修复。

收尾先显式恢复原代理；原先无代理时写 `http_proxy=:0`，验证主值和派生 host/port 后移除本实例的 reverse，最后停止监听。恢复失败保留监听并报告；不能只删除 Android 代理键。
