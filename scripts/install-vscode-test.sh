#!/usr/bin/env bash
# 临时脚本：在 ubuntu-desktop-test 里安装 VS Code（arm64），供容器 GUI 多语言渲染测试。
# 不属于 desk 的产品行为，也不随 install.sh 安装。
#
# 用法（在测试容器的 root shell 里运行）：
#   proot-distro login ubuntu-desktop-test
#   bash /path/to/install-vscode-test.sh
# 结束后回到宿主执行 desk app refresh，再从 XFCE 菜单启动。
#
# 安装方式与官方文档 Debian and Ubuntu-based distributions 章节一致：
# 微软仓库 + apt install code。之前用 .deb 直接安装时 postinst 因容器缺少
# gpg 半途失败，留下指向空 keyring 的 vscode.sources，这里一并处理。

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "run this script as root inside the ubuntu-desktop-test container" >&2
    exit 1
fi

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates wget gpg apt-transport-https

install -d -m 755 /etc/apt/keyrings
if [ ! -s /etc/apt/keyrings/packages.microsoft.gpg ]; then
    wget -qO- https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor > /etc/apt/keyrings/packages.microsoft.gpg
fi

echo 'deb [arch=amd64,arm64,armhf signed-by=/etc/apt/keyrings/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main' \
    > /etc/apt/sources.list.d/vscode.list

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y code

# deb 的 postinst 会写指向 /usr/share/keyrings/microsoft.gpg 的源；
# 容器里那个 keyring 是空的，会覆盖上面的仓库配置。
rm -f /etc/apt/sources.list.d/vscode.sources

# Electron 拒绝以 root 运行；proot 容器没有可用的沙箱用户，用 wrapper 补参数。
cat > /usr/local/bin/code <<'EOF'
#!/bin/sh
exec /usr/share/code/code --no-sandbox --user-data-dir=/root/.config/Code "$@"
EOF
chmod 755 /usr/local/bin/code

# 容器里的 .desktop 把 Exec 指向 /usr/share/code/code；改成 wrapper，
# 这样无论入口是菜单还是命令行，root 适配和参数都一致。
for desktop in /usr/share/applications/com.microsoft.VSCode.desktop; do
    if [ -f "$desktop" ]; then
        sed -i 's#Exec=/usr/share/code/code#Exec=/usr/local/bin/code#g' "$desktop"
    fi
done

echo "VS Code installed."
echo "On the host, run: desk app refresh"
echo "Then start it from the XFCE menu so desk app launch applies the font bindings."
