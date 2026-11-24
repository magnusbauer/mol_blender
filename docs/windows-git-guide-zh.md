# 从本地 Windows 适配到 Fork 和 PR

Git 是本地版本管理工具，GitHub 是托管仓库的网站。Fork 是你在 GitHub 上
拥有的上游副本；分支保存一项独立工作；commit 是本地快照；push 上传提交；
PR 请求上游审阅并合并你的提交。Fork 和 PR 都不自动改变原作者的仓库。

## 1. 在 GitHub 创建 Fork

登录自己的 GitHub，打开 https://github.com/magnusbauer/mol_blender ，点击
Fork → Create a new fork。建议保留仓库名 `mol_blender`，便于向上游贡献。
这仍是属于你的仓库；也可以改名，但 README 应明确来源与非官方适配范围。

## 2. 继续使用当前 checkout

本地已经有源码和未提交修改，不要重新 clone 到同一目录。PowerShell：

```powershell
cd C:\path\to\mol_blender
git status
git switch -c windows-local-rendering
git remote rename origin upstream
git remote add origin https://github.com/YOUR_USERNAME/mol_blender.git
git remote -v
```

将 YOUR_USERNAME 和仓库名替换成实际 Fork 信息。远端修改只需执行一次；
`upstream` 指原作者，`origin` 指你的 Fork。先检查 `remote -v`，已存在时不要重复添加。
分支切换会保留当前未提交修改。不要使用 reset --hard 或 clean 来处理这些修改。

## 3. 检查并提交

第一次使用 Git 时设置你希望公开出现在提交里的身份：

```powershell
git config user.name "Your Name"
git config user.email "YOUR_GITHUB_NOREPLY_EMAIL"
git diff --stat
git diff
git status --short
git add .gitattributes .github/workflows/windows-local.yml README.md MANIFEST.in docs scripts/install-windows.ps1 scripts/windows_setup.py scripts/pymol_windows_startup.py scripts/verify_windows_pymol.py mol_blender/platform_support.py mol_blender/developer.py mol_blender/local_bridge.py mol_blender/runtime.py mol_blender/runtime_install.py mol_blender/runtime_initialize.py mol_blender/runtime.lock.json mol_blender/worker_ccd.py tests/test_windows_platform.py tests/test_windows_setup.py tests/test_developer_ccd_entry.py
git diff --cached --stat
git diff --cached --check
git commit -m "Support synchronous local rendering on Windows"
```

GitHub Settings → Emails 可以找到 noreply 地址；不要照抄占位符。提交前检查暂存
内容，不加入 `.runtime`、`.venv`、`.validation`、图片、凭据或本机配置。运行时
版本锁和公开下载校验值属于可提交内容。原有版权头及许可证必须保留。

## 4. 上传并创建 PR

```powershell
git push -u origin windows-local-rendering
```

HTTPS 推送可能打开 GitHub 登录页，请本人完成登录。不要把 token 发到聊天里。
在你的 Fork 页面点击 Compare & pull request。base 选原作者的 main，compare
选你的 windows-local-rendering。先建 Draft PR 也可以，等 CI 通过后再标为可评审。

建议标题：`Support synchronous local rendering on Windows`。
说明触发问题、适配文件/进程接口、可重复安装卸载、验证证据与尚未覆盖的功能。
不要宣称 co-PyMOL、warm worker 或 editor 已支持 Windows；未运行的远端 CI
也不能写成通过。评审意见通过同分支的新提交更新，PR 会自动更新。

发布自己的 ZIP 时，优先发布源码包与安装脚本，并保留所有许可声明。当前 wheel
保留上游发行名/版本，仅作本地验收；不要直接以同版本冒充上游新发行上传 PyPI。
正式独立发行应先确定名称、版本及维护范围。
