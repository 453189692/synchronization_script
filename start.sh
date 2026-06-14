#!/bin/bash
# ============================================
# 启动 Rsync 同步监控 Web UI (后台运行)
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_FILE="${SCRIPT_DIR}/app.py"
LOG_FILE="${SCRIPT_DIR}/webui.log"
PID_FILE="${SCRIPT_DIR}/webui.pid"

start() {
    if [ -f "${PID_FILE}" ]; then
        PID=$(cat "${PID_FILE}")
        if kill -0 "${PID}" 2>/dev/null; then
            echo "Web UI 已在运行 (PID: ${PID})"
            return 1
        fi
        rm -f "${PID_FILE}"
    fi

    nohup python3 "${APP_FILE}" > "${LOG_FILE}" 2>&1 &
    echo $! > "${PID_FILE}"
    echo "Web UI 已启动 (PID: $!)"
    echo "日志: ${LOG_FILE}"
}

stop() {
    if [ -f "${PID_FILE}" ]; then
        PID=$(cat "${PID_FILE}")
        if kill -0 "${PID}" 2>/dev/null; then
            kill "${PID}"
            rm -f "${PID_FILE}"
            echo "Web UI 已停止 (PID: ${PID})"
        else
            rm -f "${PID_FILE}"
            echo "进程已不存在，已清理PID文件"
        fi
    else
        echo "Web UI 未在运行"
    fi
}

status() {
    if [ -f "${PID_FILE}" ]; then
        PID=$(cat "${PID_FILE}")
        if kill -0 "${PID}" 2>/dev/null; then
            echo "Web UI 运行中 (PID: ${PID})"
        else
            echo "Web UI 未运行 (PID文件残留)"
            rm -f "${PID_FILE}"
        fi
    else
        echo "Web UI 未运行"
    fi
}

restart() {
    stop
    sleep 1
    start
}

case "${1}" in
    start)   start ;;
    stop)    stop ;;
    restart) restart ;;
    status)  status ;;
    *)
        echo "用法: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
