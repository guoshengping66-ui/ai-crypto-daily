# AI + 币圈 X 创作者日报

每天抓取 AI 与加密行业热点，筛选最多各 5 条，生成中文摘要、传播角度、可编辑的 X 草稿和来源链接。不会自动发布推文。

## 免费推送到个人微信

使用 GitHub Actions 定时运行，不需要自己的服务器，也不需要电脑开机。通知通过 WxPusher API 发送；WxPusher 官方说明将其推送服务列为免费。要在个人微信里收消息，需要安装 WxPusher App 并绑定微信 ClawBot（iLink）。

1. 在手机安装 [WxPusher App](https://wxpusher.zjiecode.com/download/)，按应用内指引登录。
2. 在 App 的“我的 → 推送渠道”中绑定并启用“微信 ClawBot”。如果只想收在 WxPusher App，也可以跳过这步。
3. 按 [WxPusher 官方文档](https://github.com/wxpusher/wxpusher-docs)获取自己的 SPT（Simple Push Token）。不要使用他人的 SPT 或演示凭证；SPT 等同于接收凭证，泄露后别人可以向你发送消息。
4. 打开 GitHub 仓库的 `Settings → Secrets and variables → Actions → New repository secret`，添加以下两个 Repository secrets：

   | Secret 名称 | 内容 |
   |---|---|
   | `AI_API_KEY` | 硅基流动 API Key |
   | `WXPUSHER_SPT` | 你自己的 WxPusher SPT |

   不要把密钥写进代码或发在聊天里。`AI_MODEL` 和 `AI_API_BASE` 可不添加：默认模型是硅基流动 `XingChenAGI/Xing4.0-29B`，接口已配置。

5. 在仓库 `Actions` 页面选择 `Get Hot News → Run workflow` 手动运行一次，确认能收到消息。之后会按北京时间约 08:37 每天运行；GitHub 定时任务偶尔会延迟几分钟。

## 微信 ClawBot 限制

WxPusher 官方说明：微信 ClawBot 每次激活后 24 小时内最多接收 10 条推送，用尽后需要在微信里回复 ClawBot 任意内容重新激活。日报较长时可能拆成多条；如果微信通道暂时未激活，可在 WxPusher App 内查看或开启其系统通知。[官方通道说明](https://github.com/wxpusher/wxpusher-docs)

## 费用与维护

- 不需要购买或维护服务器。
- WxPusher 官方当前将推送服务标为免费；外部服务的规则仍可能调整。
- 硅基流动所选模型当前可能提供免费额度/免费状态，但需要遵循平台认证、限流和额度规则；价格与政策可能变化。请以[硅基流动模型中心](https://www.siliconflow.cn/models)和[限流说明](https://api-docs.siliconflow.cn/docs/userguide/faqs/rate-limit-and-upgradation)为准。
- GitHub Actions 是否产生费用取决于仓库公开状态及账号套餐额度；公开仓库的标准 runner 当前免费，私有仓库受账号套餐额度和计费规则约束，详见[GitHub Actions 计费说明](https://docs.github.com/en/billing/concepts/product-billing/github-actions)。
- 当前仓库沿用上游 `Check In` 工作流的 7 天有效期，需要至少每 7 天在 `Actions → Check In → Run workflow` 手动续期，否则定时工作流可能停用。该续期工作流会清理仓库已有的 Actions 运行记录；如需留存日志，请先下载。

## 内容标准

AI 每天最多给出 5 条 AI 和 5 条币圈选题；过去 24 小时合格信息不足时会少报，不会为凑数编造。每条提供事实摘要、读者价值、传播角度、220 字以内草稿及来源。发布前仍应核实来源、数字和时间，并加入自己的观点；草稿不保证流量或涨粉，也不提供币价预测或买卖建议。
