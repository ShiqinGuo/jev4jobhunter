# Jev 批量布尔判断

收齐当前批次完整 JD 后，一次调用 `https://api.typesafe.ai/v1/systemone`。每岗一个 noul 问题：“该岗位是否值得尝试沟通？”概率按 0.5 转为布尔决定。积极尝试不等于资格全部满足，完整 policy 的匹配复核放到沟通阶段。

- Android：`collect-details` → `evaluate-batch` → `apply-batch --send`。
- 网页：`collect-details` → `evaluate-details` 自动保存整组决定 → 逐项 `submit-reviewed-detail`。
- 不再同时询问 choice 与资格概率，不再要求宿主重判同批材料。JD 缺失、明确黑名单、重复投递和平台限制由代码处理。

只传岗位必要字段、画像中的教育/经历/项目/技能及方向，不传整份历史、运行规则、Cookie、URL 参数或授权字段。密钥取 `TYPESAFE_API_KEY`，Windows 可读取当前用户环境注册表。结果保存模型版本、概率、usage、耗时和材料指纹；画像、策略或 JD 变化后才重新判断。

服务失败、答案不完整或上下文改变时保留队列，不生成通过决定。结果未知的发送保留 unknown，不重发。Jev 输出布尔投递决定，不生成招聘话术。
