#!/bin/bash
# ============================================
# Rsync 增量同步脚本 (多任务模式)
# 用法: bash sync.sh <任务配置文件路径>
# ============================================

set -euo pipefail

# 加载全局配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.env"

# 加载任务配置
TASK_CONFIG="${1:-}"
if [ -z "${TASK_CONFIG}" ]; then
    echo "用法: $0 <任务配置文件路径>"
    echo "示例: $0 ${SCRIPT_DIR}/tasks/template-sync.env"
    exit 1
fi

if [ ! -f "${TASK_CONFIG}" ]; then
    echo "ERROR: 配置文件不存在: ${TASK_CONFIG}"
    exit 1
fi

source "${TASK_CONFIG}"

# 任务ID（配置文件名，去掉扩展名）
TASK_ID="$(basename "${TASK_CONFIG}" .env)"

# 日志目录按任务隔离
LOG_DIR="${LOG_BASE_DIR}/${TASK_ID}"
HISTORY_FILE="${LOG_DIR}/sync_history.json"

# 创建日志目录
mkdir -p "${LOG_DIR}"

# 日志文件
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="${LOG_DIR}/sync_${TIMESTAMP}.log"
PID_FILE="${LOG_DIR}/sync.pid"
STATUS_FILE="${LOG_DIR}/sync_status.json"

# ============================================
# 函数定义
# ============================================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "${LOG_FILE}"
}

update_status() {
    local status=$1
    local message=$2
    cat > "${STATUS_FILE}" <<EOF
{
    "status": "${status}",
    "message": "${message}",
    "timestamp": "$(date '+%Y-%m-%d %H:%M:%S')",
    "pid": $$,
    "log_file": "${LOG_FILE}"
}
EOF
}

save_history() {
    local start_time=$1
    local end_time=$2
    local exit_code=$3
    local files_transferred=$4
    local total_size=$5

    local duration=$(( end_time - start_time ))
    local history_entry="{\"start_time\":\"$(date -d @${start_time} '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -r ${start_time} '+%Y-%m-%d %H:%M:%S')\",\"end_time\":\"$(date -d @${end_time} '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -r ${end_time} '+%Y-%m-%d %H:%M:%S')\",\"duration\":${duration},\"exit_code\":${exit_code},\"files_transferred\":\"${files_transferred}\",\"total_size\":\"${total_size}\"}"

    if [ -f "${HISTORY_FILE}" ]; then
        # 追加到历史记录 (保留最近100条)
        python3 -c "
import json, sys
try:
    with open('${HISTORY_FILE}', 'r') as f:
        history = json.load(f)
except:
    history = []
history.append(json.loads('${history_entry}'))
history = history[-100:]
with open('${HISTORY_FILE}', 'w') as f:
    json.dump(history, f, indent=2, ensure_ascii=False)
"
    else
        echo "[${history_entry}]" > "${HISTORY_FILE}"
    fi
}

cleanup() {
    rm -f "${PID_FILE}"
    update_status "idle" "同步已结束"
}

# ============================================
# 主逻辑
# ============================================

# 检查是否已有同步进程在运行
if [ -f "${PID_FILE}" ]; then
    OLD_PID=$(cat "${PID_FILE}")
    if kill -0 "${OLD_PID}" 2>/dev/null; then
        log "ERROR: 同步进程已在运行 (PID: ${OLD_PID})，退出"
        exit 1
    else
        log "WARN: 发现残留PID文件，清理中..."
        rm -f "${PID_FILE}"
    fi
fi

# 写入PID
echo $$ > "${PID_FILE}"
trap cleanup EXIT

log "========== 开始同步 =========="
log "任务: ${TASK_NAME:-${TASK_ID}}"
log "源: ${SOURCE_USER}@${SOURCE_HOST}:${SOURCE_PATH}"
log "目标: ${DEST_PATH}"

# 构建限速参数
BW_ARG=""
if [ "${BANDWIDTH_ENABLED}" = "true" ]; then
    BW_ARG="--bwlimit=${BANDWIDTH_LIMIT}"
    log "限速: ${BANDWIDTH_LIMIT} KB/s"
else
    log "限速: 已关闭（不限速）"
fi

update_status "running" "正在同步中..."

# 创建目标目录
mkdir -p "${DEST_PATH}"

# 记录开始时间
START_TIME=$(date +%s)

# 执行 rsync
RETRY_COUNT=0
SYNC_EXIT_CODE=1

while [ ${RETRY_COUNT} -lt ${MAX_RETRIES} ]; do
    RETRY_COUNT=$((RETRY_COUNT + 1))

    if [ ${RETRY_COUNT} -gt 1 ]; then
        log "第 ${RETRY_COUNT} 次重试..."
        sleep 5
    fi

    set +e
    rsync -avz \
        --progress \
        --partial \
        --partial-dir=.rsync-partial \
        --no-perms \
        --no-owner \
        --no-group \
        --chmod=D755,F644 \
        --ignore-existing \
        ${BW_ARG} \
        --timeout="${TIMEOUT}" \
        --stats \
        --human-readable \
        --log-file="${LOG_FILE}" \
        -e "ssh -p ${SSH_PORT} -o StrictHostKeyChecking=no -o ConnectTimeout=10" \
        "${SOURCE_USER}@${SOURCE_HOST}:${SOURCE_PATH}" \
        "${DEST_PATH}" \
        2>&1 | tee -a "${LOG_FILE}"

    SYNC_EXIT_CODE=$?
    set -e

    if [ ${SYNC_EXIT_CODE} -eq 0 ]; then
        log "同步成功完成"
        break
    elif [ ${SYNC_EXIT_CODE} -eq 23 ] || [ ${SYNC_EXIT_CODE} -eq 24 ]; then
        # 23: 部分文件传输错误, 24: 部分文件消失
        log "WARN: 同步完成但有部分文件错误 (exit code: ${SYNC_EXIT_CODE})"
        break
    else
        log "ERROR: 同步失败 (exit code: ${SYNC_EXIT_CODE})"
        if [ ${RETRY_COUNT} -ge ${MAX_RETRIES} ]; then
            log "ERROR: 已达最大重试次数 ${MAX_RETRIES}，放弃"
        fi
    fi
done

# 记录结束时间
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

# 提取统计信息
FILES_TRANSFERRED=$(grep -o "Number of regular files transferred: [0-9,]*" "${LOG_FILE}" | tail -1 | grep -o "[0-9,]*$" || echo "N/A")
TOTAL_SIZE=$(grep -o "Total transferred file size: .*" "${LOG_FILE}" | tail -1 | sed 's/Total transferred file size: //' || echo "N/A")

log "========== 同步结束 =========="
log "耗时: ${DURATION} 秒"
log "传输文件数: ${FILES_TRANSFERRED}"
log "传输大小: ${TOTAL_SIZE}"
log "退出码: ${SYNC_EXIT_CODE}"

# 更新状态
if [ ${SYNC_EXIT_CODE} -eq 0 ] || [ ${SYNC_EXIT_CODE} -eq 23 ] || [ ${SYNC_EXIT_CODE} -eq 24 ]; then
    update_status "idle" "上次同步成功 ($(date '+%Y-%m-%d %H:%M:%S'))"
else
    update_status "error" "上次同步失败 (exit code: ${SYNC_EXIT_CODE})"
fi

# 保存历史记录
save_history "${START_TIME}" "${END_TIME}" "${SYNC_EXIT_CODE}" "${FILES_TRANSFERRED}" "${TOTAL_SIZE}"

# 清理旧日志 (保留最近30天)
find "${LOG_DIR}" -name "sync_*.log" -mtime +30 -delete 2>/dev/null || true

exit ${SYNC_EXIT_CODE}
