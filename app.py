#!/usr/bin/env python3
"""
Rsync 同步监控 Web UI (多任务模式)
支持多个同步任务的管理、独立触发、并行运行
"""

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# 路径配置
SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "config.env"
TASKS_DIR = SCRIPT_DIR / "tasks"
SYNC_SCRIPT = SCRIPT_DIR / "sync.sh"


def load_env_file(filepath):
    """解析 .env 文件为字典"""
    config = {}
    p = Path(filepath)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    if "=" in line:
                        key, value = line.split("=", 1)
                        config[key.strip()] = value.strip().split("#")[0].strip()
    return config


def load_global_config():
    """加载全局配置"""
    return load_env_file(CONFIG_FILE)


def get_task_ids():
    """获取所有任务ID列表"""
    if not TASKS_DIR.exists():
        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        return []
    return sorted([f.stem for f in TASKS_DIR.glob("*.env")])


def get_task_config(task_id):
    """获取指定任务的配置"""
    task_file = TASKS_DIR / (task_id + ".env")
    if not task_file.exists():
        return None
    return load_env_file(task_file)


def get_task_log_dir(task_id):
    """获取任务的日志目录"""
    global_cfg = load_global_config()
    base = Path(global_cfg.get("LOG_BASE_DIR", "/var/log/rsync"))
    return base / task_id


def get_task_status_file(task_id):
    """获取任务的状态文件路径"""
    return get_task_log_dir(task_id) / "sync_status.json"


def get_task_history_file(task_id):
    """获取任务的历史文件路径"""
    return get_task_log_dir(task_id) / "sync_history.json"


def read_log_tail(log_file, lines):
    """读取日志文件末尾（纯Python实现）"""
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            content = "".join(all_lines[-lines:])
        return {"content": content, "file": os.path.basename(log_file)}
    except IOError:
        return {"content": "无法读取日志", "file": ""}


# ============================================
# 页面路由
# ============================================

@app.route("/")
def index():
    """主页"""
    return render_template("index.html")


@app.route("/api/readme")
def get_readme():
    """获取 README.md 内容"""
    readme_file = SCRIPT_DIR / "README.md"
    if readme_file.exists():
        content = readme_file.read_text(encoding="utf-8")
        return jsonify({"success": True, "content": content})
    return jsonify({"success": False, "content": ""})


# ============================================
# 任务管理 API
# ============================================

@app.route("/api/tasks")
def list_tasks():
    """列出所有任务及其当前状态摘要"""
    tasks = []
    for task_id in get_task_ids():
        cfg = get_task_config(task_id)
        if cfg is None:
            continue

        # 读取状态
        status_file = get_task_status_file(task_id)
        status_info = {"status": "idle", "message": "尚未执行过同步", "timestamp": ""}
        if status_file.exists():
            try:
                with open(status_file, "r") as f:
                    status_info = json.load(f)
                # 检查进程是否仍在运行
                if status_info.get("status") == "running":
                    pid = status_info.get("pid")
                    if pid:
                        try:
                            os.kill(int(pid), 0)
                        except (OSError, ValueError):
                            status_info["status"] = "idle"
                            status_info["message"] = "进程已结束（异常退出）"
            except (json.JSONDecodeError, IOError):
                pass

        tasks.append({
            "id": task_id,
            "name": cfg.get("TASK_NAME", task_id),
            "source": "{0}@{1}:{2}".format(
                cfg.get("SOURCE_USER", ""),
                cfg.get("SOURCE_HOST", ""),
                cfg.get("SOURCE_PATH", "")
            ),
            "dest": cfg.get("DEST_PATH", ""),
            "status": status_info.get("status", "idle"),
            "message": status_info.get("message", ""),
            "timestamp": status_info.get("timestamp", "")
        })

    return jsonify(tasks)


@app.route("/api/tasks", methods=["POST"])
def create_task():
    """新建任务"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "缺少数据"}), 400

    task_id = data.get("id", "").strip()
    if not task_id:
        return jsonify({"success": False, "message": "缺少任务ID"}), 400

    # 合法性校验
    if not all(c.isalnum() or c in ("-", "_") for c in task_id):
        return jsonify({"success": False, "message": "任务ID只能包含字母、数字、横杠、下划线"}), 400

    task_file = TASKS_DIR / (task_id + ".env")
    if task_file.exists():
        return jsonify({"success": False, "message": "任务ID已存在"}), 409

    # 生成配置内容
    task_name = data.get("name", task_id)
    content = """# ============================================
# 同步任务: {name}
# ============================================

# 任务名称（页面显示用）
TASK_NAME={name}

# 源服务器配置
SOURCE_HOST={source_host}
SOURCE_USER={source_user}
SOURCE_PATH={source_path}
SSH_PORT={ssh_port}

# 目标路径 (本机)
DEST_PATH={dest_path}

# 同步参数
BANDWIDTH_ENABLED={bw_enabled}
BANDWIDTH_LIMIT={bw_limit}
TIMEOUT={timeout}
MAX_RETRIES={max_retries}
""".format(
        name=task_name,
        source_host=data.get("source_host", "192.168.1.37"),
        source_user=data.get("source_user", "root"),
        source_path=data.get("source_path", "/path/to/source"),
        ssh_port=data.get("ssh_port", "22"),
        dest_path=data.get("dest_path", "/path/to/dest"),
        bw_enabled=data.get("bandwidth_enabled", "true"),
        bw_limit=data.get("bandwidth_limit", "50000"),
        timeout=data.get("timeout", "300"),
        max_retries=data.get("max_retries", "3")
    )

    try:
        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        task_file.write_text(content, encoding="utf-8")
        return jsonify({"success": True, "message": "任务创建成功", "id": task_id})
    except IOError as e:
        return jsonify({"success": False, "message": "创建失败: {0}".format(str(e))}), 500


@app.route("/api/tasks/<task_id>", methods=["DELETE"])
def delete_task(task_id):
    """删除任务"""
    task_file = TASKS_DIR / (task_id + ".env")
    if not task_file.exists():
        return jsonify({"success": False, "message": "任务不存在"}), 404

    # 检查是否正在运行
    status_file = get_task_status_file(task_id)
    if status_file.exists():
        try:
            with open(status_file, "r") as f:
                status = json.load(f)
            if status.get("status") == "running":
                pid = status.get("pid")
                if pid:
                    try:
                        os.kill(int(pid), 0)
                        return jsonify({"success": False, "message": "任务正在运行中，无法删除"}), 409
                    except (OSError, ValueError):
                        pass
        except (json.JSONDecodeError, IOError):
            pass

    try:
        task_file.unlink()
        return jsonify({"success": True, "message": "任务已删除"})
    except IOError as e:
        return jsonify({"success": False, "message": "删除失败: {0}".format(str(e))}), 500


# ============================================
# 单任务操作 API
# ============================================

@app.route("/api/tasks/<task_id>/status")
def get_task_status(task_id):
    """获取指定任务的同步状态"""
    status_file = get_task_status_file(task_id)
    if status_file.exists():
        try:
            with open(status_file, "r") as f:
                status = json.load(f)
            if status.get("status") == "running":
                pid = status.get("pid")
                if pid:
                    try:
                        os.kill(int(pid), 0)
                    except (OSError, ValueError):
                        status["status"] = "idle"
                        status["message"] = "进程已结束（异常退出）"
            return jsonify(status)
        except (json.JSONDecodeError, IOError):
            pass
    return jsonify({
        "status": "idle",
        "message": "尚未执行过同步",
        "timestamp": "",
        "pid": None,
        "log_file": ""
    })


@app.route("/api/tasks/<task_id>/logs")
def get_task_logs(task_id):
    """获取指定任务的日志"""
    lines = request.args.get("lines", 150, type=int)
    log_dir = get_task_log_dir(task_id)

    # 优先从状态文件获取当前日志
    status_file = get_task_status_file(task_id)
    if status_file.exists():
        try:
            with open(status_file, "r") as f:
                status = json.load(f)
            log_file = status.get("log_file", "")
            if log_file and Path(log_file).exists():
                return jsonify(read_log_tail(log_file, lines))
        except (json.JSONDecodeError, IOError):
            pass

    # 查找最新日志文件
    if log_dir.exists():
        log_files = sorted(log_dir.glob("sync_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if log_files:
            return jsonify(read_log_tail(str(log_files[0]), lines))

    return jsonify({"content": "暂无日志", "file": ""})


@app.route("/api/tasks/<task_id>/history")
def get_task_history(task_id):
    """获取指定任务的历史记录"""
    history_file = get_task_history_file(task_id)
    if history_file.exists():
        try:
            with open(history_file, "r") as f:
                history = json.load(f)
            return jsonify(list(reversed(history[-50:])))
        except (json.JSONDecodeError, IOError):
            pass
    return jsonify([])


@app.route("/api/tasks/<task_id>/trigger", methods=["POST"])
def trigger_task(task_id):
    """触发指定任务同步"""
    task_file = TASKS_DIR / (task_id + ".env")
    if not task_file.exists():
        return jsonify({"success": False, "message": "任务不存在"}), 404

    # 检查是否已在运行
    status_file = get_task_status_file(task_id)
    if status_file.exists():
        try:
            with open(status_file, "r") as f:
                status = json.load(f)
            if status.get("status") == "running":
                pid = status.get("pid")
                if pid:
                    try:
                        os.kill(int(pid), 0)
                        return jsonify({"success": False, "message": "该任务正在同步中，请等待完成"}), 409
                    except (OSError, ValueError):
                        pass
        except (json.JSONDecodeError, IOError):
            pass

    # 启动同步进程
    try:
        process = subprocess.Popen(
            ["bash", str(SYNC_SCRIPT), str(task_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        return jsonify({
            "success": True,
            "message": "同步已启动 (PID: {0})".format(process.pid),
            "pid": process.pid
        })
    except Exception as e:
        return jsonify({"success": False, "message": "启动失败: {0}".format(str(e))}), 500


@app.route("/api/tasks/<task_id>/config/raw")
def get_task_config_raw(task_id):
    """获取任务配置文件原始内容"""
    task_file = TASKS_DIR / (task_id + ".env")
    if not task_file.exists():
        return jsonify({"success": False, "content": "", "message": "任务不存在"}), 404
    try:
        content = task_file.read_text(encoding="utf-8")
        return jsonify({"success": True, "content": content})
    except IOError as e:
        return jsonify({"success": False, "content": "", "message": str(e)}), 500


@app.route("/api/tasks/<task_id>/config/save", methods=["POST"])
def save_task_config(task_id):
    """保存任务配置文件"""
    task_file = TASKS_DIR / (task_id + ".env")
    if not task_file.exists():
        return jsonify({"success": False, "message": "任务不存在"}), 404

    data = request.get_json()
    if not data or "content" not in data:
        return jsonify({"success": False, "message": "缺少 content 字段"}), 400

    try:
        task_file.write_text(data["content"], encoding="utf-8")
        return jsonify({"success": True, "message": "配置已保存"})
    except IOError as e:
        return jsonify({"success": False, "message": "保存失败: {0}".format(str(e))}), 500


@app.route("/api/tasks/<task_id>/disk")
def get_task_disk(task_id):
    """获取任务目标路径磁盘信息"""
    cfg = get_task_config(task_id)
    if not cfg:
        return jsonify({"error": "任务不存在"})

    dest_path = cfg.get("DEST_PATH", "/")
    try:
        result = subprocess.run(
            ["df", "-h", dest_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=5
        )
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            return jsonify({
                "filesystem": parts[0] if len(parts) > 0 else "N/A",
                "size": parts[1] if len(parts) > 1 else "N/A",
                "used": parts[2] if len(parts) > 2 else "N/A",
                "available": parts[3] if len(parts) > 3 else "N/A",
                "use_percent": parts[4] if len(parts) > 4 else "N/A",
                "mount": parts[5] if len(parts) > 5 else "N/A"
            })
    except (subprocess.TimeoutExpired, FileNotFoundError, IndexError):
        pass
    return jsonify({"error": "无法获取磁盘信息"})


# ============================================
# 启动
# ============================================

if __name__ == "__main__":
    global_cfg = load_global_config()
    port = int(global_cfg.get("WEB_PORT", 5000))
    host = global_cfg.get("WEB_HOST", "0.0.0.0")
    # 确保 tasks 目录存在
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    print("Rsync 同步监控 Web UI (多任务模式) 启动: http://{0}:{1}".format(host, port))
    app.run(host=host, port=port, debug=False)
