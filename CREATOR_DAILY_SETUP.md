# 每日 AI + Web3 热点

每天北京时间08:37由GitHub Actions计划运行，向Bark分别发送AI 3条、Web3 3条简短热点。GitHub定时运行可能延迟，新闻按实际采集时间前滚动24小时筛选。不需要自有服务器或电脑开机。

每条只含事件标题、一句话简述、原文时间和来源链接。内容不足时只用同一质量门槛下的已核验候选补充；没有足够合格新事件时少发。不会自动发布到社交平台。

## 配置

在仓库 Settings → Secrets and variables → Actions 设置：

| Secret | 用途 |
|---|---|
| BARK_URL | Bark设备推送URL |
| AI_API_KEY | 现有模型服务的API Key |
| AI_MODEL | 可选，覆盖配置中的模型 |
| AI_API_BASE | 可选，覆盖模型接口地址 |

不把密钥或Bark设备URL写进仓库。模型沿用当前硅基流动兼容接口，实际模型费用和额度由服务商决定。新增RSS及HN关注信号无需额外付费数据API。

## 测试与检查

- Actions → Get Hot News → Run workflow，勾选 bark_test：仅发一条短测试通知。
- 勾选 dry_run：抓取并筛选热点，不调用模型、不发送通知、不更新已推送历史。
- 两项都不勾选：生成并推送当次日报。
- 查看运行的 creator-daily-运行ID-尝试次数 附件，下载 output/creator_daily/run.json，核对实际正文、筛选数量、入选依据和推送结果。附件保留30天。

来源扩展、热度排序、去重与质量标准见 [优化方案](CREATOR_SELECTION_PLAN.md)。成功推送事件保存7天历史并通过Actions缓存恢复；首次启用或缓存丢失时无法保证与此前消息去重。讨论依据来自已取得的社区和媒体数据，不等于X浏览量。
