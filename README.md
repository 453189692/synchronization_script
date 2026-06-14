# Rsync 增量同步工具 — 部署与运行手册

多任务模式：支持多个同步任务并行运行，通过 Web UI 管理、触发和监控。

---

## 一、项目概述

### 1.1 功能特性

| 特性 | 说明 |
|------|------|
| 增量同步 | 只传输新增/变更文件，支持断点续传 |
| 保护目标 | 不删除 32 上的独有文件，不覆盖已存在文件 |
| 权限安全 | 不保留源服务器权限，统一设置目录755/文件644 |
| 限速传输 | 可配置带宽限制（默认50MB/s），避免网络拥堵 |
| 自动重试 | 失败自动重试，最多 3 次 |
| Web 监控 | 实时状态、日志查看、手动触发、历史记录、在线编辑配置 |

### 1.2 架构说明

```
┌─────────────────┐         rsync over SSH          ┌─────────────────┐
│  192.168.1.37   │ ──────────────────────────────→  │  192.168.1.32   │
│  (源服务器)      │         单向增量同步              │  (目标服务器)    │
│                 │                                  │                 │
│  /source/path/  │                                  │  /dest/path/    │
└─────────────────┘                                  │  Flask Web UI   │
                                                     │  :5000          │
                                                     └─────────────────┘
```

- **脚本运行在 32 服务器**上，从 37 拉取文件
- 32 上新产生的文件**不会被删除或覆盖**
- 37 上新增的文件会**自动增量同步**到 32

### 1.3 目录结构

```
/opt/rsync-sync/                 # 建议部署路径
├── config.env                   # 全局配置（Web UI端口、日志根目录）
├── tasks/                       # 任务配置目录（每个任务一个 .env）
│   ├── template-sync.env        # 任务示例：模板文件同步
│   └── report-sync.env          # 任务示例：报告文件同步
├── sync.sh                      # rsync 同步主脚本（参数化）
├── app.py                       # Flask Web UI 应用（多任务API）
├── start.sh                     # 后台启动/停止脚本
├── templates/
│   └── index.html               # 监控面板前端页面
├── requirements.txt             # Python 依赖
└── README.md                    # 本文档
```

---

## 二、环境要求

在 **192.168.1.32**（目标服务器）上需要：

| 依赖 | 版本要求 | 检查命令 |
|------|---------|---------|
| Linux OS | CentOS 7+ / Ubuntu 18+ | `cat /etc/os-release` |
| rsync | 3.0+ | `rsync --version` |
| SSH | OpenSSH | `ssh -V` |
| Python3 | 3.6+ | `python3 --version` |
| pip3 | 任意 | `pip3 --version` |

在 **192.168.1.37**（源服务器）上需要：

| 依赖 | 说明 |
|------|------|
| SSH Server | 开启 sshd 并允许 32 连接 |
| rsync | 已安装（大多数 Linux 默认自带） |

---

## 三、部署步骤

> 以下所有操作均在 **192.168.1.32** 上执行，除非特别说明。

### 3.1 上传项目文件

```bash
# 方式一：从本地上传
scp -r ./synchronization_script/ root@192.168.1.32:/opt/rsync-sync/

# 方式二：直接在 32 上创建目录并复制文件
mkdir -p /opt/rsync-sync
# 然后将文件放入该目录
```

### 3.2 SSH 免密配置

```bash
# 1. 生成密钥对（如已有可跳过）
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N ""

# 2. 将公钥复制到 37 服务器
ssh-copy-id -p 22 root@192.168.1.37
# 按提示输入 37 服务器的 root 密码

# 3. 验证免密登录（不应再提示密码）
ssh root@192.168.1.37 "hostname && echo '免密配置成功'"
```

> **注意**：如果使用非 root 用户，将上面的 `root` 替换为实际用户名，并确保该用户有源目录的读权限。

### 3.3 修改全局配置

```bash
vi /opt/rsync-sync/config.env
```

```bash
# 日志根目录（各任务日志按子目录隔离）
LOG_BASE_DIR=/home/sy_lims/synchronization_script/log

# Web UI 配置
WEB_PORT=5000
WEB_HOST=0.0.0.0
```

### 3.4 创建同步任务

每个同步任务对应 `tasks/` 目录下一个 `.env` 文件：

```bash
vi /opt/rsync-sync/tasks/template-sync.env
```

```bash
TASK_NAME=模板文件同步       # 页面显示名称
SOURCE_HOST=192.168.1.37
SOURCE_USER=root
SOURCE_PATH=/data/shared/files/
SSH_PORT=22
DEST_PATH=/data/synced/files/
BANDWIDTH_ENABLED=true
BANDWIDTH_LIMIT=50000        # KB/s
TIMEOUT=300
MAX_RETRIES=3
```

也可以在 Web UI 上通过「+ 新建任务」按钮直接创建。

### 3.5 创建必要目录

```bash
# 日志目录
mkdir -p /home/sy_lims/synchronization_script/log

# 设置脚本可执行权限
chmod +x /opt/rsync-sync/sync.sh
chmod +x /opt/rsync-sync/start.sh
```

### 3.5 安装 Python 依赖

```bash
pip3 install -r /opt/rsync-sync/requirements.txt
```

---

## 四、运行方式

### 4.1 手动执行同步

```bash
# 执行指定任务的同步（参数为任务配置文件路径）
bash /opt/rsync-sync/sync.sh /opt/rsync-sync/tasks/template-sync.env

# 也可以在 Web UI 上点击「手动触发同步」按钮
```

### 4.2 启动 Web UI 监控面板

#### 方式一：前台运行（测试用）

```bash
cd /opt/rsync-sync
python3 app.py
```

输出：`Rsync 同步监控 Web UI (多任务模式) 启动: http://0.0.0.0:5000`

浏览器访问：**http://192.168.1.32:5000**

#### 方式二：后台运行

```bash
nohup python3 /opt/rsync-sync/app.py > /var/log/rsync/webui.log 2>&1 &
```

#### 方式三：systemd 服务（推荐生产使用）

```bash
# 创建服务文件
cat > /etc/systemd/system/rsync-monitor.service << 'EOF'
[Unit]
Description=Rsync Sync Monitor Web UI
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/rsync-sync
ExecStart=/usr/bin/python3 /opt/rsync-sync/app.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 加载、启用、启动
systemctl daemon-reload
systemctl enable rsync-monitor
systemctl start rsync-monitor

# 查看运行状态
systemctl status rsync-monitor
```

### 4.3 配置定时自动同步

```bash
crontab -e
```

每个任务可以独立配置 cron 定时：

```bash
# 任务1: 每天凌晨 2 点同步模板文件
0 2 * * * /opt/rsync-sync/sync.sh /opt/rsync-sync/tasks/template-sync.env >> /home/sy_lims/synchronization_script/log/cron.log 2>&1

# 任务2: 每 6 小时同步报告文件
0 */6 * * * /opt/rsync-sync/sync.sh /opt/rsync-sync/tasks/report-sync.env >> /home/sy_lims/synchronization_script/log/cron.log 2>&1
```

验证 crontab 已生效：

```bash
crontab -l
```

---

## 五、Web UI 使用说明

访问地址：**http://192.168.1.32:5000**

### 5.1 功能说明

| 功能模块 | 说明 |
|---------|------|
| 任务概览栏 | 顶部显示所有任务及其运行状态，点击切换 |
| 新建任务 | 通过表单创建新的同步任务 |
| 删除任务 | 删除指定任务的配置文件（日志保留） |
| 同步状态 | 显示当前任务是否在同步（空闲/同步中/错误） |
| 同步配置 | 展示当前任务的源地址、目标路径、限速等 |
| 磁盘空间 | 目标路径的磁盘使用率和剩余空间 |
| 同步日志 | 实时滚动显示当前任务最新日志 |
| 同步历史 | 当前任务历次同步记录 |
| 编辑配置 | 在线修改任务 .env 配置 |
| 手动触发 | 一键启动当前任务的同步 |
| 并行运行 | 多个任务可同时运行，互不影响 |

### 5.2 在线编辑配置

1. 选择要编辑的任务
2. 页面底部点击「编辑任务配置」展开编辑器
3. 直接修改配置内容
4. 点击「保存配置」按钮，下次同步生效

---

## 六、同步策略详解

| rsync 参数 | 作用 | 为什么用 |
|-----------|------|---------|
| `-a` | 归档模式（递归、保留符号链接等） | 基础同步能力 |
| `-v` | 详细输出 | 便于日志记录 |
| `-z` | 传输时压缩 | 减少网络带宽消耗 |
| `--progress` | 显示传输进度 | 日志可查看进度 |
| `--partial` | 保留部分传输的文件 | 支持断点续传 |
| `--partial-dir=.rsync-partial` | 临时文件存放目录 | 避免污染目标目录 |
| `--ignore-existing` | 跳过目标已有的文件 | 保护32上的新文件不被覆盖 |
| `--no-perms --no-owner --no-group` | 不保留源文件权限/属主 | 避免跨服务器权限问题 |
| `--chmod=D755,F644` | 统一设置权限 | 确保目标文件可读可用 |
| `--bwlimit=50000` | 限速50MB/s | 避免打满带宽影响业务 |
| `--timeout=300` | 连接超时5分钟 | 防止网络中断时无限等待 |
| **不用** `--delete` | 不删除目标独有文件 | 32上新产生的文件得以保留 |

---

## 七、日志与排查

### 7.1 日志位置

| 日志文件 | 说明 |
|---------|------|
| `LOG_BASE_DIR/<任务ID>/sync_YYYYMMDD_HHMMSS.log` | 每次同步的详细日志 |
| `LOG_BASE_DIR/<任务ID>/sync_status.json` | 当前任务同步状态 |
| `LOG_BASE_DIR/<任务ID>/sync_history.json` | 任务同步历史记录 |
| `LOG_BASE_DIR/cron.log` | crontab 执行日志 |
| `/opt/rsync-sync/webui.log` | Web UI 日志（start.sh方式） |

### 7.2 常见问题排查

**Q: SSH 连接失败**
```bash
# 检查网络连通性
ping 192.168.1.37

# 检查 SSH 端口
telnet 192.168.1.37 22

# 手动尝试 SSH
ssh -v root@192.168.1.37
```

**Q: 权限不足（Permission denied）**
```bash
# 检查密钥权限
ls -la ~/.ssh/
chmod 600 ~/.ssh/id_rsa
chmod 644 ~/.ssh/id_rsa.pub

# 检查 37 上的 authorized_keys
ssh root@192.168.1.37 "cat ~/.ssh/authorized_keys"
```

**Q: 同步进程卡住**
```bash
# 查看当前 rsync 进程
ps aux | grep rsync

# 清理残留 PID 文件
rm -f /var/log/rsync/sync.pid

# 强制终止
kill -9 $(cat /var/log/rsync/sync.pid)
```

**Q: 磁盘空间不足**
```bash
# 查看磁盘使用
df -h

# 查看目标目录大小
du -sh /data/synced/files/

# 清理旧日志
find /var/log/rsync -name "sync_*.log" -mtime +7 -delete
```

**Q: Web UI 无法访问**
```bash
# 检查服务状态
systemctl status rsync-monitor

# 检查端口是否监听
ss -tlnp | grep 5000

# 检查防火墙
firewall-cmd --list-ports
# 如需开放端口
firewall-cmd --add-port=5000/tcp --permanent
firewall-cmd --reload
```

---

## 八、维护操作

### 8.1 日志清理

脚本已内置自动清理30天以上的日志。如需手动清理：

```bash
# 清理7天前的日志
find /var/log/rsync -name "sync_*.log" -mtime +7 -delete
```

### 8.2 服务管理

```bash
# 查看 Web UI 状态
systemctl status rsync-monitor

# 重启 Web UI
systemctl restart rsync-monitor

# 停止 Web UI
systemctl stop rsync-monitor

# 查看 Web UI 日志
journalctl -u rsync-monitor -f
```

### 8.3 修改同步频率

```bash
crontab -e
# 修改 cron 表达式后保存即可
```

### 8.4 临时暂停同步

```bash
# 注释掉 crontab 条目
crontab -e
# 在行首加 # 号保存

# 或直接移除
crontab -r   # 注意：这会清除所有定时任务
```

---

## 九、注意事项

1. **首次同步**：100G 数据首次全量传输耗时较长（50MB/s 约需 35 分钟），建议在业务空闲时段执行
2. **路径末尾的 `/`**：`SOURCE_PATH` 末尾**必须有 `/`**，否则 rsync 会在目标路径下多创建一层同名目录
3. **磁盘空间**：确保 32 服务器目标分区有足够空间（建议剩余 > 120G）
4. **网络稳定性**：脚本已内置重试机制（默认3次），短暂网络抖动不会导致同步失败
5. **并发控制**：脚本通过 PID 文件防止多个同步进程同时运行
6. **安全性**：Web UI 默认无认证，建议仅在内网使用或配合 nginx 做 BasicAuth
