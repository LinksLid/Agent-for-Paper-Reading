# PaperTeam 可视化阅读网站

从项目根目录运行 `python launch.py` 或双击 `start.bat`，打开 http://127.0.0.1:8600/#read 。首次自动准备运行依赖。

首页直接上传 PDF、填写问题、启动真实 LangGraph 阅读，显示节点开始/结束、调用与 token 统计，完成后显示报告、证据和日志，可下载 Markdown 或打印 PDF。右侧配置自己的模型连接，密钥保存到本机 .env。

`assets/reader.js` / `reader.css` 是真实阅读界面；`paperteam/web_jobs.py` 是持久化后台任务；`paperteam/showcase.py` 提供 HTTP API 与站点资源。历史展示页面由 `assets/app.js` 提供。

直接双击 `index.html` 和 GitHub Pages 是静态模式，只能查看公开历史记录；上传、模型调用和最近阅读需要本地 Python 服务。完整操作见 `docs/user-guide.md`。
