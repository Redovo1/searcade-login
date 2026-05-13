# auto_login_searcade 登录续期 建议每 4 天内一定要成功登录一回

用来在 GitHub Actions 上周期性自动登录 [searcade.com](https://searcade.com)，防止免费服务器因长期未登录被回收。登录流程走的是 searcade 自带的 userveria SSO（邮箱 → 密码）。

脚本逻辑大同小异参考了 [lunes-login](https://github.com/Redovo1/lunes-login)：登录成功 → 进服务器控制台停留 4-6 秒 → 返回首页停留 3-5 秒 → 退出。

## 你需要做以下修改

### 1、设置环境变量 `ACCOUNTS_BATCH`

到仓库 `Settings → Secrets and variables → Actions → New repository secret`，
名字写 `ACCOUNTS_BATCH`，值按下面格式，**每行一套账号**：

```
a1@example.com,pass1
a2@example.com,pass2,123456:AAxxxxxx,123456789
a3@example.com,pass3,6928
a4@example.com,pass4,6929,123456:AAxxxxxx,123456789
```

每行支持以下 4 种格式：

| 列数 | 格式 | 说明 |
| --- | --- | --- |
| 2 | `email,password` | 最简单，使用默认 server_id（6927），不发 TG |
| 3 | `email,password,server_id` | 指定服务器 ID（比如你的不是 6927），不发 TG |
| 4 | `email,password,tg_bot_token,tg_chat_id` | 默认 server_id，发 TG 通知 |
| 5 | `email,password,server_id,tg_bot_token,tg_chat_id` | 指定 server_id，发 TG 通知 |

没配 TG 也能正常跑，TG 完全是可选的。

### 2、（可选）覆盖默认 server_id

默认 `SERVER_ID=6927`。如果你所有账号都想用同一个非 6927 的 server_id，可以直接在仓库
`Settings → Secrets and variables → Actions → Variables` 里加一个 `SEARCADE_SERVER_ID`，
并把 `.github/workflows/main.yml` 的 env 里加上：

```yaml
SEARCADE_SERVER_ID: ${{ vars.SEARCADE_SERVER_ID }}
```

或者直接在每行账号里写第 3 列覆盖（推荐）。

### 3、开放自动写 `time.txt` 的文件权限

为了规避 GitHub 默认 60 天仓库无任何变动就自动禁用 Actions 定时任务的限制，
workflow 最后一步会把当前时间写进 `time.txt` 并 commit 回仓库。

`GITHUB_TOKEN` 不用你手动创建 —— GitHub Actions 会自动为每次运行生成一个临时的
`secrets.GITHUB_TOKEN`。你要做的是给它写权限：

到仓库：

**Settings → Actions → General → Workflow permissions → 选择 "Read and write permissions"**

如果是 "Read repository contents permission" 只读，`git push` 会失败。

workflow 里这段已经写好了：

```yaml
permissions:
  contents: write
```

### 4、修改定时任务执行时间

`.github/workflows/main.yml` 里默认：

```yaml
on:
  schedule:
    - cron: '17 18 */4 * *'   # 每 4 天 UTC 18:17（北京次日 02:17 凌晨）跑一次
```

建议每 4 天内至少成功登录一次，按自己需要改就行。

## 本地调试

```bash
pip install -r requirements.txt
export ACCOUNTS_BATCH='you@example.com,yourpassword'
python login.py
```

跑完会在 `screenshots/` 下生成各步骤的截图，如果登录失败直接看截图能很快定位
是哪一步选择器没对上。

## 目录结构

```
.
├── .github/workflows/main.yml   # GitHub Actions 定时任务
├── login.py                     # 登录主脚本（SeleniumBase UC Mode）
├── requirements.txt             # Python 依赖
├── time.txt                     # 防仓库 60 天无变动被禁用
└── README.md
```
