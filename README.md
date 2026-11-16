# Legion Go S Power · 功率限制

Decky 插件，适用于 Lenovo Legion Go S 83Q2（Ryzen Z2 Go）的 SteamOS。通过系统现有的联想 WMI 驱动设置 APU 的持续、短时和瞬时功率上限，并读取实际生效的值。滑块范围取自固件。

从 [GitHub Releases](https://github.com/tywinMa/legion-go-s-power-plugin/releases/latest) 下载 Assets 下的 `legion-go-s-power-<版本>.zip`。在游戏模式打开 Decky「设置 → 开发者」，选择从 ZIP 安装。也可以在「从 URL 安装」中粘贴 ZIP 的下载链接。

打开「Legion Go S 功率限制」，设置并保存电池和插电瓦数。两组瓦数在整个系统中生效，切换游戏时继续使用当前供电方式的瓦数。升级旧版时会保留全局瓦数和开关状态，并移除按游戏保存的设置。

打开「全局功率限制」会立即应用已保存的瓦数。关闭开关会交回系统控制，并保留已保存的瓦数。关闭时也可以编辑并保存瓦数，供下次开启使用。

开关状态会在 Decky 重载或机器重启后恢复。供电变化、唤醒或系统覆盖上限后，插件会重新应用已保存的设置。插件需要联想「自定义」性能档。使用插件时可关闭 Steam 的手动 TDP 限制，避免两个设置互相覆盖。卸载前可先关闭开关。

Decky 后端通过插件清单的 `root` 权限写入固件接口。配置文件归 SteamOS 登录用户所有，权限为 `0600`，旧版的配置文件会自动修复所有权。

`pnpm install --frozen-lockfile` 安装构建依赖，`pnpm check` 检查类型，`pnpm build` 生成前端。ZIP 中应包含单个 `legion-go-s-power/` 目录，目录内须保留 `plugin.json`、`package.json`、`main.py` 和 `dist/index.js`。

## 构建与发布

推送到 `main` 会触发 GitHub Actions。工作流自动生成补丁版本号，构建并检查 ZIP，随后将 Decky 安装包和 SHA-256 校验文件发布到 GitHub Releases。重跑同一次工作流会沿用同一个版本号。

插件采用 BSD-3-Clause 许可证，详见 [LICENSE](LICENSE)。
