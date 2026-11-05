# Fedora / KDE 部署说明

已验证 Fedora 44 / KDE、Python 3.14.10、Pillow 12.3.0 和 PT-D610BT 12 mm 覆膜 TZe。
代码需要 Python 3.10+、Pillow 10.1+。LBX 当前使用 Fedora 的 Liberation Sans 字体安装路径。
其他发行版、机型、标签带宽度尚未验证。

## 1. 安装依赖并固定代码位置

仅使用命令行打印时：

```bash
sudo dnf install git python3 python3-pillow liberation-sans-fonts bluez
```

需要 KDE 双击打印时，另安装：

```bash
sudo dnf install konsole shared-mime-info desktop-file-utils xdg-utils
```

将仓库克隆或将发布包解压到准备长期保留的位置，然后进入目录：

```bash
git clone https://github.com/Night7dady/ptd610bt-linux.git
cd ptd610bt-linux
./setup.sh --desktop
```

发布包用户直接进入解压目录执行最后一条即可。`setup.sh` 只检查现有依赖，不联网或自动安装。
命令行使用可执行 `./setup.sh --check`。`requirements.txt` 列出 Python 依赖，Fedora 推荐使用上述
发行版软件包，不要用 `sudo pip` 覆盖系统 Python。桌面入口固定使用 `/usr/bin/python3`。

## 2. 配对并验证 SPP

在打印机开启蓝牙，通过 KDE 系统设置完成配对，记录设备地址。以下步骤在同一个终端进行：

```bash
read -r -p 'PT-D610BT 蓝牙地址: ' PRINTER_MAC
python3 -I -B scripts/ptd610bt_bluetooth.py status --mac "$PRINTER_MAC" --channel 2
```

已验证设备的 SPP 通道是 2；若其他设备状态查询失败，需要先确认其 SDP 服务通道。
系统通用连接按钮的 `ProfileUnavailable` 不代表专用 SPP 不能工作；以状态查询结果为准。
本工具不自动配对、不创建 RFCOMM 设备节点、不要求打印用 root，不修改服务或安全策略。
首次打印建议按 [使用说明](USAGE.md) 先预览，再手动打印并检查实体输出。

## 3. 生成桌面配置（不会安装或打印）

从稳定的项目目录运行。下列输出目录必须尚不存在：

```bash
python3 -I -B tools/configure_desktop.py --mac "$PRINTER_MAC" --output-dir "$PWD/desktop-config" --dry-run
python3 -I -B tools/configure_desktop.py --mac "$PRINTER_MAC" --output-dir "$PWD/desktop-config"
desktop-file-validate desktop-config/ptd610bt-lbx-print.desktop
```

生成器只新建两个文件，重复运行不会覆盖输出。`--dry-run` 不写入任何文件。
生成的 `.desktop` 包含本机打印机地址和项目绝对路径；这些是本机部署数据，不要提交到仓库。
安装后不要移动/删除项目目录；需要移动时先卸载关联，在新位置重新生成。

## 4. 只为当前用户安装并设置默认打开方式

先记录原 LBX 默认应用；记录文件也应当是新文件：

```bash
(
    set -euC
    xdg-mime query default application/x-brother-lbx > desktop-config/previous-default.txt
)
```

下面的安装操作只创建两个明确的用户文件；如果目标已经存在，会先报错退出，避免覆盖现有配置：

```bash
(
set -eu
python3 -I -B - "$PWD/desktop-config" "${XDG_DATA_HOME:-$HOME/.local/share}" <<'PY'
from pathlib import Path
import os
import sys
source, data = map(Path, sys.argv[1:])
pairs = [(source / 'ptd610bt-lbx.xml', data / 'mime/packages/ptd610bt-lbx.xml'),
         (source / 'ptd610bt-lbx-print.desktop', data / 'applications/ptd610bt-lbx-print.desktop')]
for src, dst in pairs:
    if not src.is_file() or os.path.lexists(dst):
        raise SystemExit(f'Missing source or target already exists: {src} -> {dst}')
for src, dst in pairs:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open('xb') as stream:
        stream.write(src.read_bytes())
    dst.chmod(0o644)
PY
update-mime-database "${XDG_DATA_HOME:-$HOME/.local/share}/mime"
update-desktop-database "${XDG_DATA_HOME:-$HOME/.local/share}/applications"
xdg-mime default ptd610bt-lbx-print.desktop application/x-brother-lbx
)
```

最后一条会设置当前用户的 LBX 默认应用，保留其他类型的默认关联。
独立类型 `application/x-brother-lbx` 只匹配 `*.lbx`（默认不区分大小写），
`application/zip` 仅是父类型。**不要把打印程序设为 `application/zip` 的默认应用。**

验证配置，不会打印：

```bash
kmimetypefinder '/path/to/label.lbx'
xdg-mime query default application/x-brother-lbx
```

预期分别是 `application/x-brother-lbx` 和 `ptd610bt-lbx-print.desktop`。
在缺少桌面环境变量的终端里，`xdg-mime query filetype` 可能退回 `file` 并显示 ZIP；
KDE 原生识别应以 `kmimetypefinder` 为准。文件管理器若仍显示旧类型，可刷新目录或重新打开窗口。

现在双击 LBX 会直接发送一张打印任务，Konsole 保留完成/错误结果。
需要蓝牙可用、打印机开机，且标签布局符合当前限制。不要同时批量打开多个文件。

## 5. 卸载或恢复以前的关联

先在项目目录读取之前保存的应用名：

```bash
previous_default=$(cat desktop-config/previous-default.txt)
if [ -n "$previous_default" ]; then
    xdg-mime default "$previous_default" application/x-brother-lbx
fi
```

若记录为空，打开 KDE“系统设置 → 应用程序 → 文件关联”，找到
`application/x-brother-lbx`，只移除“打印 Brother LBX 标签”的默认应用项。
不要删除整个用户 `mimeapps.list`，里面可能有其他应用的关联。

确认下述文件仍与本次生成配置一致，再删除这两个工具文件并更新缓存：

```bash
(
    set -eu
    cmp desktop-config/ptd610bt-lbx.xml "${XDG_DATA_HOME:-$HOME/.local/share}/mime/packages/ptd610bt-lbx.xml"
    cmp desktop-config/ptd610bt-lbx-print.desktop "${XDG_DATA_HOME:-$HOME/.local/share}/applications/ptd610bt-lbx-print.desktop"
    rm -- "${XDG_DATA_HOME:-$HOME/.local/share}/mime/packages/ptd610bt-lbx.xml" "${XDG_DATA_HOME:-$HOME/.local/share}/applications/ptd610bt-lbx-print.desktop"
    update-mime-database "${XDG_DATA_HOME:-$HOME/.local/share}/mime"
    update-desktop-database "${XDG_DATA_HOME:-$HOME/.local/share}/applications"
)
```

如果 `cmp` 报告不同，请先停下检查，不要继续删除已被他人修改的文件。
上述卸载不删除标签、项目源码或打印机配对；它只撤销本工具的当前用户桌面入口。
如果系统管理员另外安装过全局关联，那仍可继续生效，需要由管理员单独处理。

## 依据

- [Desktop Entry Exec 与单文件参数规则](https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html)
- [Shared MIME-info 类型注册规则](https://specifications.freedesktop.org/shared-mime-info/latest/ar01s02.html)
- [默认应用关联规则](https://specifications.freedesktop.org/mime-apps/latest/default.html)
