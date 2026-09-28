# Android 真机

需要已授权 ADB 真机、BOSS 官方客户端及可信用户证书。安装 `scripts/android-requirements.txt` 到独立 Python 3.12 环境。模拟器不属于本适配器的验证范围。初次安装 CA 使用 Android 设置中的“CA 证书”；用户密码由用户在手机输入。

机器配置放在数据目录的 `android.json`，不放仓库：`adb`、`serverPort`、`serial`、`manufacturer`、`model`、`python`（安装了 mitmproxy 的解释器）、`caDir`（mitmproxy CA 目录）、可选 `proxyPort`（默认 8877）。证书私钥不复制进插件。

入口：`python scripts/android_actions.py --config DATA/android.json --data-dir DATA OPERATION`。一般操作自动获取并释放已有 store 运行锁，其他执行者占用时保留其状态。

1. `start` 启动仅监听本机的代理，经 ADB reverse 连接手机；只解码 BOSS 域名。搜索、滚动、详情和沟通必须从 UI 触发。
   App 已有连接可能继续绕过新代理；首次启用后正常关闭再打开 BOSS，保留登录。没有新响应时不要误判为空列表。主页推荐也可采集，但仅在用户授权使用推荐流时纳入本次任务。
2. 在“我的 → 在线简历”等能读取账号显示名的页面用 `bind-account` 核对 policy 中的账号显示名。回搜索页，`arm-search` 建立动作标记，正常点击搜索，再 `read-batch` 接收该动作产生的列表。
   首页推荐与主动搜索的筛选器不同，分别读取 `policy.search.androidFilters.recommendation` / `.search`（薪资 `salaryLabels`、经验 `experienceLabels`、活跃 `bossActivity`）；没有配置时按已有策略核对原生选项，不自动互换档位。首页薪资是单选，多档要求按档位依次确认、采集并跨档去重，收齐后合并一次 Jev 判断；切档前收完该档选定岗位的完整 JD。首页没有独立“经验不限”，不能用“全部”替代。换城、切入口后重新核验筛选；首页选中样式未暴露在 XML 的 `selected` 属性中时用截图核验，不能把点击成功当成已选。搜索页可读回 `selected=true`。每次确认筛选前建立动作标记，以接收对应的新列表响应。
3. 首次 `calibrate-scroll` 连续测两次新增批次，保存固定坐标、时长及滚动上限。以后 `scroll-next` 复用参数，仅根据响应判断是否已加载；未收到响应不等于结果耗尽。换设备、分辨率或 App 版本后重新校准。
4. `collect-details` 根据页面中的职位名和公司定位并点击，从响应校验岗位 ID 和完整 JD。`evaluate-batch` 一次批量布尔判断，`apply-batch` 默认只返回队列，追加 `--send` 执行已有授权。
5. 发送复用 store outbox、去重、黑名单和平台限制。点击前持久化 pending；只有同一 UI 动作产生的成功沟通响应才能记 succeeded，其他情况 unknown 并停止。进入聊天不算成功。沟通文案由宿主对照完整 policy 和真实经历生成。
6. `stop` 先恢复手机代理，再停止采集。原先无代理时写入 `http_proxy=:0` 并检查系统派生的代理主机为空；单纯删除该键会导致手机继续连接已停止的本机代理。拔 USB 前执行 stop。

`android_capture.py` 只处理已产生的响应，解码 BZPBlock/RC4/LZ4；不包含签名、主动请求或重放。只保存职位必要字段和回执，不保存登录数据或整份混合批量响应。材料存于 `DATA/android/responses`，批次和模型输出引用证据。UI 文本不能替代 JD 响应。
