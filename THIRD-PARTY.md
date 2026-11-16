# 第三方组件声明

本软件自身不包含第三方源码。运行时通过 pip 安装以下依赖：

| 组件 | 用途 | 许可证 | 项目地址 |
| --- | --- | --- | --- |
| aiohttp | 异步 HTTP 服务与 WebSocket 客户端 | Apache-2.0 | https://github.com/aio-libs/aiohttp |
| Python | 解释器（系统或用户自行安装，不随包分发） | PSF License | https://www.python.org |

aiohttp 以 Apache License 2.0 授权，许可证原文随其发行包提供（`.venv/lib/python*/site-packages/aiohttp-*.dist-info/LICENSE.txt`）。

内置的 9 个插件（menu / signin / weather / wallpaper / netease_music / video_parser / blacklist / expert / tougao）与本软件同许可，均为 MIT。
