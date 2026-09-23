# AI + 币圈 X 创作者日报

每天整理 AI 与加密行业各最多 5 条 RSS 原始候选，附来源、时间、RSS 摘要和原文链接。当前免费模式不调用 AI 模型、不生成推文草稿；请人工核实并改写后再发布，不会自动发布推文。

## 免费推送到个人微信

使用 GitHub Actions 定时运行，不需要自己的服务器，也不需要电脑开机。个人微信请配置 WxPusher **标准推送**（`appToken` + `UID`）；WxPusher 官方说明基础推送服务免费。SPT 极简推送在本次配置中只到达 WxPusher 客户端，因此不作为微信主通道。

1. 登录 [WxPusher 管理后台](https://wxpusher.zjiecode.com/admin/)，创建一个推送应用并取得应用的 `appToken`。
2. 使用目标微信账号扫描该应用的关注二维码，关注应用；然后在管理后台的微信用户列表中取得该用户的 `UID`。官方标准接口通过 `appToken` 和 `UID` 发送消息，接口成功后可查询发送状态。[标准 API 文档](https://wxpusher.zjiecode.com/docs/api-reference.html)
3. 打开 GitHub 仓库的 `Settings → Secrets and variables → Actions → New repository secret`，添加以下两个 Repository secrets：

   | Secret 名称 | 内容 |
   |---|---|
   | `WXPUSHER_APP_TOKEN` | 第 1 步创建的应用密钥 |
   | `WXPUSHER_UIDS` | 第 2 步取得的 UID；单人填写一个，多个 UID 用英文逗号分隔 |

   标准推送配置完成后会优先使用它，不会再同时推送到旧的 SPT 客户端通道。不要把 `appToken`、UID 或 SPT 发在聊天里或写进公开代码。

4. 在仓库 `Actions` 页面选择 `Get Hot News → Run workflow`，勾选 `wxpusher_test` 只发一条短测试消息（不会运行日报）。确认微信收到后，日常任务会按北京时间约 08:37 运行；GitHub 定时任务偶尔会延迟几分钟。

## 费用与维护

- 不需要购买或维护服务器。
- 当前 RSS-only 模式不需要 `AI_API_KEY`，不会产生模型 API 调用费用。
- WxPusher 官方将基础推送服务列为免费；服务限制可能变化，请以[官方文档](https://wxpusher.zjiecode.com/docs/)为准。
- GitHub Actions 的额度取决于仓库公开状态及账号套餐；详见[GitHub Actions 计费说明](https://docs.github.com/en/billing/concepts/product-billing/github-actions)。
- 已移除仓库自带的 7 天签到停用门槛。`Clean Workflow History` 仅用于手动清理 Actions 历史记录，不要为保持定时任务运行而触发它。

## 内容标准

每天 AI 与币圈各最多列出 5 条 RSS 候选；候选不足、有缺失时会少报，不会为凑数编造。标题、摘要、时间均来自外部 RSS，未经事实核验；发布前应打开原文、确认时间和事实，并加入自己的解释。内容不保证流量或涨粉，也不提供币价预测或买卖建议。
