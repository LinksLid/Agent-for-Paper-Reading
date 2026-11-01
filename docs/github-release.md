# GitHub发布与下载运行

推荐从原项目Fork并保留上游历史，再提交网站和可移植改造；也可将整理好的完整发布包内容放入自己的仓库。先阅读`NOTICE.md`中的来源和许可状态，不将全部上游代码描述为本组原创。

发布包包含源码、网站、公共演示、启动入口、配置模板、依赖、Docker、CI与文档。排除`.env`、`.venv`、`.runtime`、`output`、`external`、`.cache`、PDF全文、个人PyCharm配置。不要上传E盘整个运行目录。

上传后，他人使用Code → Download ZIP，解压运行`start.bat`或`sh start.sh`。首次启动自动安装完整阅读依赖，浏览器打开上传与阅读首页；生成新报告需要配置下载者自己的模型 API。

新建仓库的命令示例，远程地址换成自己的：

```bash
git init
git add .
git commit -m "Add portable PaperTeam framework and presentation"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git
git push -u origin main
```

已是Fork或已有Git仓库时，不重复初始化或覆盖远程。本次整理只准备文件，没有替用户推送原维护者仓库。

## GitHub Pages直接访问

1. 自己仓库进入Settings → Pages，Source选择GitHub Actions。
2. Actions选择 **Publish presentation to GitHub Pages**，点击Run workflow。
3. 工作流导出公开demo、上传`showcase/`并部署；成功后会返回实际访问网址。

支持`https://YOUR-USERNAME.github.io/YOUR-REPO/`这样的仓库子路径。Pages只提供静态演示，不运行Python、模型、容器或密钥后台。工作流默认仅手动触发，不因每次push公开个人内容。

配置依据：[发布来源](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)、[自定义工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)。

## 发布检查

在没有`.env`、`output`、`.venv`和`.runtime`的新目录中运行：

```bash
python launch.py --no-browser --data-mode demo --presentation-only
```

应显示公开演示标记、8篇论文、88条接受结论和评分摘要。再验证完整测试和Docker。该命令只测不安装依赖的静态历史展示。默认 `python launch.py` 会准备真实阅读依赖，但只有用户点击“开始阅读”才调用模型；模型连接需使用自己的 Key。
