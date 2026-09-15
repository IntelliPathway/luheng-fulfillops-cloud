#!/usr/bin/env bash
set -euo pipefail

TARGET_PATH="${1:-/opt}"
if [[ ! -d "$TARGET_PATH" ]]; then
  TARGET_PATH="/"
fi

CPU_CORES="$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc)"
MEM_MIB="$(awk '/MemTotal:/ {printf "%d", $2 / 1024}' /proc/meminfo)"
SWAP_MIB="$(awk '/SwapTotal:/ {printf "%d", $2 / 1024}' /proc/meminfo)"
DISK_TOTAL_GIB="$(df -Pk "$TARGET_PATH" | awk 'NR==2 {printf "%d", $2 / 1024 / 1024}')"
DISK_FREE_GIB="$(df -Pk "$TARGET_PATH" | awk 'NR==2 {printf "%d", $4 / 1024 / 1024}')"
ARCH="$(uname -m)"
FAILURES=0
WARNINGS=0

pass() { printf '[PASS] %s\n' "$1"; }
warn() { printf '[WARN] %s\n' "$1"; WARNINGS=$((WARNINGS + 1)); }
fail() { printf '[FAIL] %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

printf '履约智控 AI ECS capacity preflight (read-only)\n'
printf 'Path: %s | Arch: %s | CPU: %s | RAM: %s MiB | Swap: %s MiB | Disk: %s GiB total / %s GiB free\n\n' \
  "$TARGET_PATH" "$ARCH" "$CPU_CORES" "$MEM_MIB" "$SWAP_MIB" "$DISK_TOTAL_GIB" "$DISK_FREE_GIB"

if (( CPU_CORES < 2 )); then fail '至少需要 2 vCPU'; elif (( CPU_CORES < 4 )); then warn '2 vCPU 仅适合小流量验收'; else pass 'CPU 达到推荐 4 vCPU'; fi
if (( MEM_MIB < 3700 )); then fail '可识别内存不足 4 GB'; elif (( MEM_MIB < 7500 )); then warn '4 GB 内存仅适合小流量验收'; else pass '内存达到推荐 8 GB'; fi
if (( DISK_TOTAL_GIB < 55 )); then fail '系统盘不足标称 60 GB'; elif (( DISK_TOTAL_GIB < 90 )); then warn '系统盘仅达到最低验收档'; else pass '系统盘达到推荐 100 GB'; fi
if (( DISK_FREE_GIB < 20 )); then fail '可用磁盘不足 20 GB'; elif (( DISK_FREE_GIB < 40 )); then warn '建议至少保留 40 GB 用于镜像、日志、数据库和备份'; else pass '可用磁盘空间充足'; fi
if (( MEM_MIB < 7500 && SWAP_MIB < 1024 )); then warn '8 GB 以下实例建议配置至少 2 GB Swap，并监控换页'; else pass 'Swap/内存余量满足建议'; fi

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then pass 'Docker daemon 可用'; else fail 'Docker 已安装但 daemon 不可访问'; fi
  if docker compose version >/dev/null 2>&1; then pass 'Docker Compose v2 可用'; else fail 'Docker Compose v2 不可用'; fi
else
  fail '未安装 Docker'
fi

if command -v 1pctl >/dev/null 2>&1 || [[ -d /opt/1panel ]]; then pass '检测到 1Panel'; else warn '未检测到常见 1Panel 安装路径'; fi

printf '\n'
if (( FAILURES > 0 )); then
  printf 'RESULT=FAIL failures=%d warnings=%d\n' "$FAILURES" "$WARNINGS"
  exit 2
fi
if (( CPU_CORES >= 4 && MEM_MIB >= 7500 && DISK_TOTAL_GIB >= 90 && DISK_FREE_GIB >= 40 )); then
  printf 'RESULT=RECOMMENDED failures=0 warnings=%d\n' "$WARNINGS"
else
  printf 'RESULT=MINIMUM_ACCEPTANCE failures=0 warnings=%d\n' "$WARNINGS"
fi
