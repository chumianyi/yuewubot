# YueWuBot 1.4.2 · QQ 官方机器人开源版

极简单进程 QQ 机器人框架。常驻内存约 40 MB，自带 Web 控制台，插件化，MIT 开源。
只接入 **QQ 官方机器人（开放平台）**，不做第三方协议，代码里有完整的网关、令牌刷新、心跳与消息收发实现。

## 这个版本和其他版本的区别

| | 开源版（本仓库） | 完整版（私有） |
| --- | --- | --- |
| QQ 官方机器人 | 支持 | 支持 |
| OneBot v11 / v12 | 不支持 | 支持 |
| 微信官方机器人 | 不支持 | 支持 |
| 源码 | 公开，MIT | 不公开 |
| 内置插件 | 9 个 | 13 个 |

完整版面向需要接 NapCat / SnowLuma / 微信的场合，本仓库只做 QQ 官方这一条链路，代码更少更好读。

## 1.4.2 更新

- **访问令牌登录**：控制台用 30 位字母数字令牌进门，安装时自动生成并写入 `data/TOKEN.txt`，`./start.sh token` 随时查看。
- **`./已部署后启动.sh`**：部署完成后日常启动用它，不重跑安装、不装依赖、不动配置。
- **新手引导**：登录后概览页四步引导，配模型 → 接机器人 → 开插件 → 发消息，完成自动打勾。
- **QQ 回复带 msg_id**：群聊与单聊回复带上原消息 ID，符合开放平台被动回复要求。
- **平台配置极简**：只需要 AppID 与 AppSecret，网关地址、令牌刷新、心跳全部自动。

## 环境要求

- Linux x86_64 / ARM64（Debian / Ubuntu / CentOS 均可），macOS 亦可
- Python 3.6 及以上
- 能访问 PyPI 与 `api.sgroup.qq.com`

## 快速开始

```bash
chmod +x start.sh 已部署后启动.sh reset-token.sh
./start.sh
```

脚本会依次完成：免责声明 → 创建 `.venv` → 安装 `aiohttp` → 生成访问令牌 → 启动服务。
有 systemd 时注册为 `yuewubot.service` 并开机自启，否则后台运行，日志在 `logs/app.log`。

跑完终端会打印：

```
控制台  http://127.0.0.1:6199
令牌    7fK2mQ9xR4tY6uP1aS3dF5gH8jL0
文件    data/TOKEN.txt
```

打开控制台，把令牌粘进登录框。

## 接入 QQ 机器人

1. 到 [QQ 开放平台](https://q.qq.com/) 创建机器人，拿到 **AppID** 与 **AppSecret**
2. 在开放平台把机器人加入群，或先用单聊调试
3. 控制台「平台连接」→ 新增 → 填 AppID / AppSecret → 保存

保存后自动连 `api.sgroup.qq.com` 网关，订阅群 @ 消息、群消息与单聊消息。
在线状态、重连次数、最近错误都会显示在卡片上。

## 配置模型

控制台「模型」页填接口地址、API Key、模型名，支持 OpenAI 兼容与 Anthropic 协议，
默认 `https://api.deepseek.com/v1` + `deepseek-flash`。保存后可用「试聊」直接验证。

## 常用命令

| 命令 | 说明 |
| --- | --- |
| `./start.sh` | 首次安装并启动 |
| `./已部署后启动.sh` | 已部署后直接启动 |
| `./已部署后启动.sh restart` / `stop` / `status` / `logs` | 日常管理 |
| `./start.sh token` | 查看访问令牌 |
| `./start.sh token --reset` | 重新生成令牌 |
| `./reset-token.sh` | 同上，独立脚本 |
| `./start.sh fg` | 前台运行，调试用 |
| `./start.sh deps` | 仅修复依赖 |
| `./start.sh doctor` | 环境诊断 |
| `./start.sh uninstall` | 卸载服务（保留数据） |

端口与监听地址：

```bash
YW_PORT=7000 YW_HOST=0.0.0.0 ./start.sh
```

对外暴露时请用 Nginx 反代并加 HTTPS，控制台默认只监听 `127.0.0.1`。

## 目录

```
yuewubot.py          主程序（单文件）
web/                 控制台前端（index.html / style.css / app.js / docs.html）
plugins/             内置插件，每个插件一个目录
data/                运行时生成：config.json / 插件配置 / 图片缓存 / TOKEN.txt
logs/                非 systemd 模式的运行日志
start.sh             安装与管理脚本
已部署后启动.sh      已部署后的日常启动
reset-token.sh       重置访问令牌
```

`data/` 与 `.venv/` 不随仓库分发，删除后重启会重新生成（令牌会换新）。

## 插件

zip 包，根目录放 `main.py` + `m.json`，可选 `icon.png`，资源放 `assets/`。

```json
{
  "name": "示例插件",
  "version": "1.0.0",
  "author": "you",
  "description": "一句话介绍",
  "hooks": { "on_message": "on_message" },
  "triggers": { "on_message": ["关键词"] },
  "commands": [
    { "name": "签到", "aliases": ["qd"], "description": "每日签到", "usage": "/签到", "handler": "cmd_signin" }
  ],
  "config": { "bonus": { "type": "number", "label": "奖励", "default": 10 } },
  "requirements": ["pillow>=9"]
}
```

```python
async def on_message(ctx):
    if "关键词" not in ctx.text:
        return False
    await ctx.reply("收到")
    return True
```

`ctx` 提供 `text` / `user_id` / `group_id` / `images` / `cfg`，以及：

- `await ctx.reply(text, at=False)`
- `await ctx.send_image(url)`（需要公网可访问的图片地址）
- `await ctx.llm(prompt, system=None)`
- `ctx.save_config({...})`

QQ 官方机器人不提供 OneBot 那套动作接口，`ctx.api()` 会抛出说明性异常，插件里需要自行 try。

完整说明见控制台内「开发文档」页（`/docs`）。

## 内置插件

| 插件 | 说明 |
| --- | --- |
| 功能菜单 | 发送「菜单」打开使用手册 |
| 签到 | 每日签到、连续天数、积分 |
| 天气 | wttr.in 天气查询 |
| 壁纸 | 随机图片 |
| 网易云点歌 | 搜索并返回歌曲信息 |
| 视频解析 | B 站 / 抖音直链解析 |
| 黑名单 | 名单内用户完全无法交互 |
| 专业模式 | 无提示词直答、长文分段 |
| 投稿 | 收集使用建议与插件创意 |

## 状态指令

向机器人发送 `#YueWu` 返回运行时间、状态、系统、负载、内存、版本与更新检测，不走模型。

## 自动更新

控制台「设置 → 版本更新」读取 `https://chumianyi.github.io/v.json`，
发现新版本可一键下载、校验 SHA-256、备份并重启，`data/`、`plugins/`、`.venv/` 始终保留。

## 安全说明

- 控制台凭据是 30 位访问令牌，保存在 `data/config.json` 与 `data/TOKEN.txt`（权限 600）
- 令牌可随时重置，重置后所有已登录会话立即失效
- 登录失败限速：同一 IP 每分钟最多 8 次
- AppSecret 只存在本机配置，接口回显时打码
- 插件与主程序同权限运行，只安装可信来源的包

## 开源协议

MIT，见 [LICENSE](LICENSE)。

QQ 官方机器人接口的使用需遵守腾讯开放平台的相关协议；本项目与腾讯无隶属关系。
