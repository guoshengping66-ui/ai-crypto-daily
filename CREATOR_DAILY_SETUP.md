# AI + 币圈 X 创作者日报

此配置每天收集 AI 与加密行业新闻，筛出各自最多 5 条选题，生成中文摘要、传播角度和可编辑的 X 短帖，再通过企业微信应用关联到个人微信。程序不会自动发布推文。

## 每日内容

- AI 选题最多 5 条，币圈选题最多 5 条；同一事件只放一个分区。
- 每条包括事实摘要、读者价值、传播角度、220 字以内草稿和原文链接。
- 另给当天优先发布顺序、需要人工核对的事实和具体运营建议。
- 过去 24 小时合格信息不足时会如实少报，不造新闻补数。

RSS 源覆盖 OpenAI、Google AI、DeepMind、Hugging Face、NVIDIA、arXiv，以及 CoinDesk、Cointelegraph、The Block、Decrypt、Blockworks、CryptoSlate 和 Ethereum Foundation。来源返回时间可能变化，失效时应更新源地址。

## GitHub Actions 配置

1. 在 GitHub 新建一个空仓库（不要勾选自动创建 README、License 或 `.gitignore`）。若想尽量不产生 Actions 费用，可设为 Public；标准 GitHub-hosted runner 在公开仓库当前免费。若选 Private，Actions 使用账号套餐内的月度分钟数，超额可能收费。代码里不放任何密钥。
2. 在本机 PowerShell 完成登录并将这份配置推送到刚创建的空仓库。把下面的用户名和仓库名换成自己的：

   ```powershell
   gh auth login
   git -C "C:\Users\23977\Documents\ChatGPT\New project\trendradar" remote add personal https://github.com/<用户名>/<仓库名>.git
   git -C "C:\Users\23977\Documents\ChatGPT\New project\trendradar" push personal master
   ```

   如果你的电脑没有 `gh` 命令，可先安装 GitHub CLI，或配置 GitHub HTTPS 登录后再推送。不要把 token 粘贴到聊天或代码文件。
3. 在仓库的 `Settings → Secrets and variables → Actions` 添加以下 Repository secrets：

   | Secret 名称 | 内容 |
   |---|---|
   | `AI_API_KEY` | 硅基流动 API Key |
   | `WEWORK_WEBHOOK_URL` | 已关联个人微信的企业微信应用 Webhook 地址 |
   | `WEWORK_MSG_TYPE` | 固定填 `text` |

   `AI_MODEL` 和 `AI_API_BASE` 可留空。默认模型为 `XingChenAGI/Xing4.0-29B`，接口地址已设为硅基流动。硅基流动模型的免费状态、认证要求和速率限制可能变化；启用前请在[模型价格页](https://siliconflow.cn/pricing)确认当前价格。账户免费模型列表说明见[官方限流文档](https://api-docs.siliconflow.cn/docs/userguide/faqs/rate-limit-and-upgradation)。

4. 在企业微信中创建应用并关联到你的个人微信，复制应用 Webhook 地址放入 `WEWORK_WEBHOOK_URL`。TrendRadar 的个人微信说明见[项目 README](https://github.com/sansan0/TrendRadar#-快速开始)里的“个人微信推送”。此方式通过企业微信应用转到个人微信，消息是纯文本。
5. 到仓库 `Actions` 页面启用工作流，选择 `Get Hot News → Run workflow` 手动发一份样稿。确认微信能收到后，计划任务会每天北京时间约 08:37 执行；GitHub 可能延迟几分钟。

无需服务器或 Cloudflare R2。日报按天运行，历史热度轨迹不会跨任务保存。公共仓库使用标准 runner 当前免费；私有仓库按 GitHub 套餐额度计费，具体以[官方 Actions 计费说明](https://docs.github.com/en/billing/concepts/product-billing/github-actions)为准。硅基流动模型当前价格见[模型中心](https://siliconflow.cn/models)；免费模型需完成实名认证并受固定限流，规则可能调整，详见[官方限流说明](https://api-docs.siliconflow.cn/docs/userguide/faqs/rate-limit-and-upgradation)。

## 已知运行限制

TrendRadar 上游的 GitHub Actions 工作流带有 7 天有效期，需要至少每 7 天在 `Actions → Check In → Run workflow` 手动续期；忘记续期后工作流会自动停用。这个限制来自上游项目当前的工作流设计。Docker/本地运行不受这项限制，但电脑需要在计划运行时开机并联网。

## 传播与事实标准

筛选优先考虑新信息、实际影响、讨论空间和可写角度。模型生成的是草稿，不保证流量或涨粉；发布前应打开来源核对数字、时间、产品状态和币圈安全事件，加入自己的解释或实测。草稿不得冒充亲测，也不提供价格预测或买卖建议。
