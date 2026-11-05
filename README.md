# ptd610bt-linux

通过 Linux 蓝牙给 **Brother PT-D610BT** 打印标签：查询状态、预览及单张打印文本、
图片和有限类型的 `.lbx` 文件，并生成 KDE 双击直接打印的关联配置。

这是独立实现，不是 Brother 官方程序，也不是完整 P-touch Editor。当前仅支持
**12 mm 覆膜 TZe 标签带**。打印脚本已在 Fedora 44 / KDE 上完成实体标签验证；
打包阶段运行离线测试，不额外打印。

## 快速开始

Fedora 的命令行依赖：

```bash
sudo dnf install git python3 python3-pillow liberation-sans-fonts bluez
git clone https://github.com/Night7dady/ptd610bt-linux.git
cd ptd610bt-linux
./setup.sh
python3 -I -B scripts/ptd610bt_bluetooth.py preview --text 'BT TEST' --output /tmp/ptd610-first-preview.png
```

`setup.sh` 只检查依赖，不安装软件、不配对、不修改配置、不连接打印机。
预览路径必须是尚不存在的新文件。先通过 KDE 蓝牙设置与打印机完成配对，再查询状态、打印。

- [使用说明](docs/USAGE.md)：状态查询、文本/图片/LBX 打印、返回码和裁切说明。
- [部署说明](docs/DEPLOYMENT.md)：Fedora 依赖、配对、KDE 双击直接打印及卸载。
- [更新记录](CHANGELOG.md)。

## 支持范围

| 项目 | 当前范围 |
|---|---|
| 机型与连接 | PT-D610BT；Linux Bluetooth Classic RFCOMM/SPP，已验证通道 2 |
| 标签带 | 12 mm 覆膜 TZe；64 点可打印高度，180 dpi；单页、单份 |
| LBX | 单张、横向、自动长度；简单未旋转文本和内嵌图片 |
| LBX 字体 | Arial 用 Fedora Liberation Sans 替代；常规/粗体 |
| 桌面 | KDE / Konsole；只注册 LBX 类型，普通 ZIP 关联不变 |
| 平台验证 | Fedora 44 / KDE；Python 3.14.10，Pillow 12.3.0 |

代码要求 Python 3.10+、Pillow 10.1+；其他系统/版本尚未做实体打印验证。
LBX 的多页、条码、旋转对象等不支持布局会报错，不会悄悄丢弃对象。

## 开发与 Codex

```bash
python3 -I -B -m unittest discover -s tests -v
```

目前 36 项离线测试覆盖协议、光栅方向、完成回包、LBX 和桌面配置生成。
本项目提供供 Codex 使用的 [AGENTS.md](AGENTS.md)；在当前 Codex 会话中读取该文件即可了解
命令和边界。无需外部模型工具或共享 Agent 配置。参与开发见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可与实现来源

[MIT](LICENSE)，Copyright 2026 Night7dady。
打印协议参考官方 Android iPrint&Label 的静态分析结果，并用实体输出校正了位序和方向。
仓库不包含官方 APK、原生库、SDK 二进制、用户标签文件或照片。
Brother、P-touch 和 TZe 名称归其权利人所有。
